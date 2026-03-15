#!/usr/bin/env python3
"""Post-open OCO protector — places OCO protection orders after OPG fills.

Reads pending_protections.json, checks positions, places OCO orders.
No LLM needed — pure mechanical execution.

Usage:
    python trading/protector.py [--file pending_protections.json] [--retries 3] [--delay 30]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import (
    get_clock,
    get_open_orders,
    get_positions,
    place_oco_order,
)


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_protections(filepath: str) -> list[dict]:
    path = Path(filepath)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def save_protections(filepath: str, protections: list[dict]):
    """Atomically write protections to avoid corruption on crash."""
    import os
    import tempfile

    path = Path(filepath)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".protections_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(protections, f, indent=2)
        os.replace(tmp_path, filepath)  # atomic on POSIX
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def has_oco_for_symbol(symbol: str, orders: list[dict]) -> bool:
    """Check if there's already an OCO or bracket leg protecting this symbol."""
    for order in orders:
        if order["symbol"] != symbol:
            continue
        order_class = order.get("order_class", "")
        if "OCO" in order_class.upper():
            return True
        if order.get("legs"):
            return True
    return False


def find_position(symbol: str, positions: list[dict]) -> dict | None:
    for pos in positions:
        if pos["symbol"] == symbol:
            return pos
    return None


def auto_detect_unprotected(positions: list[dict], orders: list[dict]) -> list[dict]:
    """Detect positions without OCO/bracket protection and build protection entries.

    Uses a default 7% SL and ATR-estimated TP to protect orphaned positions.
    This is the LAST RESORT — proper OCO should come from the trading pipeline.
    """
    protected_symbols = set()
    for order in orders:
        sym = order["symbol"]
        order_class = order.get("order_class", "")
        if "OCO" in order_class.upper() or "BRACKET" in order_class.upper():
            protected_symbols.add(sym)
        if order.get("legs"):
            protected_symbols.add(sym)

    emergency = []
    for pos in positions:
        if pos["symbol"] in protected_symbols:
            continue
        sym = pos["symbol"]
        entry = pos["avg_entry_price"]
        qty = int(abs(pos["qty"]))
        is_long = "LONG" in pos["side"].upper()

        # Emergency protection: 7% SL, 10% TP (wide enough to not trigger on noise)
        if is_long:
            sl = round(entry * 0.93, 2)
            tp = round(entry * 1.10, 2)
            oco_side = "sell"
            direction = "LONG"
        else:
            sl = round(entry * 1.07, 2)
            tp = round(entry * 0.90, 2)
            oco_side = "buy"
            direction = "SHORT"

        emergency.append({
            "symbol": sym,
            "qty": qty,
            "direction": direction,
            "oco_side": oco_side,
            "tp": tp,
            "sl": sl,
            "source": "auto_detect_emergency",
        })

    return emergency


def protect(filepath: str, max_retries: int = 3, delay: int = 30) -> int:
    protections = load_protections(filepath)

    # Even if no pending protections file, check for naked positions
    if not protections:
        log("No pending protections file. Checking for unprotected positions...")
        try:
            positions = get_positions()
            orders = get_open_orders()
            if positions:
                protections = auto_detect_unprotected(positions, orders)
                if protections:
                    log(f"EMERGENCY: Found {len(protections)} unprotected position(s)!")
                else:
                    log("All positions are protected. Nothing to do.")
                    return 0
            else:
                log("No open positions. Nothing to do.")
                return 0
        except Exception as e:
            log(f"WARNING: Could not check positions: {e}")
            return 0

    if not protections:
        log("No pending protections found. Nothing to do.")
        return 0

    log(f"Loaded {len(protections)} pending protection(s):")
    for p in protections:
        log(f"  - {p['symbol']} {p['direction']} | OCO side={p['oco_side']} TP={p['tp']} SL={p['sl']}")

    # Check market status
    clock = get_clock()
    if not clock["is_open"]:
        log(f"WARNING: Market is closed. Next open: {clock['next_open']}")
        log("OPG orders may not have filled yet. Exiting.")
        return 1

    log("Market is OPEN. Checking positions...")

    remaining = list(protections)
    completed = []

    for attempt in range(1, max_retries + 1):
        if not remaining:
            break

        log(f"--- Attempt {attempt}/{max_retries} ---")
        positions = get_positions()
        orders = get_open_orders()

        log(f"  Positions: {len(positions)} | Open orders: {len(orders)}")

        still_pending = []
        for prot in remaining:
            symbol = prot["symbol"]

            # Already protected?
            if has_oco_for_symbol(symbol, orders):
                log(f"  {symbol}: OCO already exists. Skipping.")
                prot["oco_status"] = "already_exists"
                completed.append(prot)
                continue

            # Position filled?
            pos = find_position(symbol, positions)
            if pos is None:
                log(f"  {symbol}: No position yet (OPG not filled). Will retry.")
                still_pending.append(prot)
                continue

            # Place OCO
            actual_qty = abs(int(float(pos["qty"])))
            log(f"  {symbol}: Position found ({actual_qty} shares, P&L: ${pos['unrealized_pl']:.2f})")
            log(f"  {symbol}: Placing OCO (side={prot['oco_side']}, TP={prot['tp']}, SL={prot['sl']})...")

            result = place_oco_order(
                symbol=symbol,
                qty=actual_qty,
                take_profit_price=prot["tp"],
                stop_loss_price=prot["sl"],
                side=prot["oco_side"],
            )

            if result.success:
                log(f"  {symbol}: OCO PLACED -- ID: {result.order_id}")
                prot["oco_order_id"] = result.order_id
                prot["oco_status"] = "placed"
                completed.append(prot)
            else:
                log(f"  {symbol}: OCO FAILED -- {result.error}")
                if "exit order" in str(result.error).lower():
                    log(f"  {symbol}: Position may not be settled yet. Will retry.")
                    still_pending.append(prot)
                else:
                    prot["oco_status"] = "error"
                    prot["oco_error"] = result.error
                    completed.append(prot)

        remaining = still_pending

        if remaining and attempt < max_retries:
            log(f"  {len(remaining)} pending. Waiting {delay}s before retry...")
            time.sleep(delay)

    # Final report
    log("=== PROTECTION REPORT ===")
    for p in completed:
        status = p.get("oco_status", "unknown")
        oid = p.get("oco_order_id", "N/A")
        err = p.get("oco_error", "")
        log(f"  {p['symbol']} {p['direction']}: {status} (order: {oid}) {err}")
    for p in remaining:
        log(f"  {p['symbol']} {p['direction']}: STILL PENDING (OPG not filled)")

    # Save only remaining (unprotected) items
    save_protections(filepath, remaining)
    log(f"Updated {filepath}: {len(remaining)} remaining, {len(completed)} done")

    return 0 if not remaining else 1


def main():
    parser = argparse.ArgumentParser(description="Post-open OCO protector")
    parser.add_argument(
        "--file", default="pending_protections.json",
        help="Path to pending protections JSON (default: pending_protections.json)",
    )
    parser.add_argument(
        "--retries", type=int, default=3,
        help="Max retry attempts for unfilled positions (default: 3)",
    )
    parser.add_argument(
        "--delay", type=int, default=30,
        help="Seconds between retries (default: 30)",
    )
    args = parser.parse_args()
    sys.exit(protect(args.file, args.retries, args.delay))


if __name__ == "__main__":
    main()
