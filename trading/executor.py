#!/usr/bin/env python3
"""Trade executor & data CLI — replaces MCP Alpaca tools entirely.

Usage (called by agents via Bash):

  # Data queries (read-only)
  python trading/executor.py account
  python trading/executor.py positions
  python trading/executor.py orders
  python trading/executor.py clock
  python trading/executor.py quote NVDA
  python trading/executor.py quote NVDA AAPL AMD
  python trading/executor.py bars NVDA --timeframe 1Day --days 60
  python trading/executor.py latest-trade NVDA
  python trading/executor.py latest-bar NVDA
  python trading/executor.py asset NVDA
  python trading/executor.py analyze NVDA AAPL AMD --days 60 --json
  python trading/executor.py status

  # Order placement (--side buy|sell, default buy)
  python trading/executor.py bracket NVDA 28 185.00 166.50
  python trading/executor.py bracket NVDA 28 185.00 166.50 --side sell
  python trading/executor.py bracket NVDA 28 185.00 166.50 --limit 175.00
  python trading/executor.py opg NVDA 28
  python trading/executor.py opg NVDA 28 --side sell
  python trading/executor.py opg NVDA 28 --limit 175.00
  python trading/executor.py oco NVDA 28 185.00 166.50
  python trading/executor.py oco NVDA 28 185.00 166.50 --side buy  # cover short

  # Position management
  python trading/executor.py close NVDA
  python trading/executor.py close NVDA --pct 50
  python trading/executor.py cancel ORDER_UUID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import (
    cancel_order,
    close_position,
    get_account,
    get_asset_info,
    get_bars,
    get_clock,
    get_closed_orders,
    get_latest_bar,
    get_latest_quote,
    get_latest_trade,
    get_open_orders,
    get_portfolio_history,
    get_positions,
    place_bracket_order,
    place_oco_order,
    place_opg_order,
)
from trading.indicators import compute_signals
from trading.sectors import check_sector_limit, get_sector


# ---------------------------------------------------------------------------
# Data query commands (read-only)
# ---------------------------------------------------------------------------

def cmd_account(args):
    """Print account info."""
    print(json.dumps(get_account(), indent=2))
    return 0


def cmd_positions(args):
    """Print all open positions."""
    print(json.dumps(get_positions(), indent=2))
    return 0


def cmd_orders(args):
    """Print all open orders."""
    print(json.dumps(get_open_orders(), indent=2))
    return 0


def cmd_clock(args):
    """Print market clock."""
    print(json.dumps(get_clock(), indent=2))
    return 0


def cmd_quote(args):
    """Get latest bid/ask quote for one or more symbols."""
    if len(args.symbols) == 1:
        print(json.dumps(get_latest_quote(args.symbols[0]), indent=2))
    else:
        results = {}
        for sym in args.symbols:
            try:
                results[sym] = get_latest_quote(sym)
            except Exception as e:
                results[sym] = {"error": str(e)}
        print(json.dumps(results, indent=2))
    return 0


def cmd_bars(args):
    """Fetch OHLCV bars as JSON."""
    df = get_bars(args.symbol, timeframe=args.timeframe, days=args.days)
    # Convert to list of dicts with string timestamps
    records = []
    for ts, row in df.iterrows():
        rec = {
            "timestamp": str(ts),
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(row["volume"]),
        }
        if "vwap" in row:
            rec["vwap"] = round(float(row["vwap"]), 4)
        records.append(rec)
    output = {"symbol": args.symbol, "timeframe": args.timeframe, "bars": records}
    if args.last:
        output["bars"] = records[-args.last:]
    print(json.dumps(output, indent=2))
    return 0


def cmd_latest_trade(args):
    """Get latest trade for a symbol."""
    print(json.dumps(get_latest_trade(args.symbol), indent=2))
    return 0


def cmd_latest_bar(args):
    """Get latest bar for a symbol."""
    print(json.dumps(get_latest_bar(args.symbol), indent=2))
    return 0


def cmd_asset(args):
    """Get asset info (tradability, exchange, class)."""
    print(json.dumps(get_asset_info(args.symbol), indent=2))
    return 0


def cmd_status(args):
    """Print account, positions, orders, and market clock."""
    output = {
        "clock": get_clock(),
        "account": get_account(),
        "positions": get_positions(),
        "open_orders": get_open_orders(),
    }
    print(json.dumps(output, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Order placement commands
# ---------------------------------------------------------------------------

def _check_sector_before_order(symbol: str) -> dict | None:
    """Check sector limit before placing an order. Returns error dict or None.

    Fail-closed: if the check itself errors, BLOCK the order rather than
    silently allowing it (learned from $2,548 sector concentration loss).
    """
    try:
        positions = get_positions()
        orders = get_open_orders()
        allowed, reason = check_sector_limit(symbol, positions, orders)
        if not allowed:
            return {"success": False, "error": reason, "sector": get_sector(symbol)}
    except Exception as e:
        return {
            "success": False,
            "error": f"Sector check failed (fail-closed): {e}",
            "sector": get_sector(symbol),
        }
    return None


def cmd_bracket(args):
    """Place a bracket order (long or short).

    When --validate is set (default), runs R/R validation with live bid/ask
    BEFORE placing the order. This prevents Disaster 5 (gap risk).
    """
    sector_err = _check_sector_before_order(args.symbol)
    if sector_err:
        print(json.dumps(sector_err, indent=2))
        return 1

    # Auto-validate R/R with live prices before placement
    if args.validate and args.limit is not None:
        try:
            quote = get_latest_quote(args.symbol)
            is_short = args.side == "sell"
            live_entry = quote["bid_price"] if is_short else quote["ask_price"]

            if is_short:
                live_reward = live_entry - args.tp
                live_risk = args.sl - live_entry
            else:
                live_reward = args.tp - live_entry
                live_risk = live_entry - args.sl

            live_rr = live_reward / live_risk if live_risk > 0 else 0
            drift_pct = abs(live_entry - args.limit) / args.limit * 100 if args.limit > 0 else 0

            if live_rr < args.min_rr or live_reward <= 0 or live_risk <= 0:
                reasons = []
                if live_rr < args.min_rr:
                    reasons.append(f"R/R {live_rr:.2f} < min {args.min_rr}")
                if live_reward <= 0:
                    reasons.append(f"negative reward ({live_reward:.2f})")
                if live_risk <= 0:
                    reasons.append(f"invalid risk ({live_risk:.2f})")
                print(json.dumps({
                    "success": False,
                    "error": f"R/R validation FAILED: {'; '.join(reasons)}",
                    "live_entry": live_entry,
                    "live_rr": round(live_rr, 2),
                    "planned_entry": args.limit,
                    "drift_pct": round(drift_pct, 2),
                    "bid": quote["bid_price"],
                    "ask": quote["ask_price"],
                }, indent=2))
                return 1
        except Exception as e:
            print(json.dumps({
                "success": False,
                "error": f"R/R validation error (fail-closed): {e}",
            }, indent=2))
            return 1

    result = place_bracket_order(
        symbol=args.symbol,
        qty=args.qty,
        take_profit_price=args.tp,
        stop_loss_price=args.sl,
        side=args.side,
        time_in_force=args.tif,
        limit_price=args.limit,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def cmd_opg(args):
    """Place an OPG (market-on-open) order (long or short)."""
    sector_err = _check_sector_before_order(args.symbol)
    if sector_err:
        print(json.dumps(sector_err, indent=2))
        return 1
    result = place_opg_order(
        symbol=args.symbol,
        qty=args.qty,
        side=args.side,
        limit_price=args.limit,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def cmd_oco(args):
    """Place an OCO protection order (sell to close long, buy to cover short)."""
    result = place_oco_order(
        symbol=args.symbol,
        qty=args.qty,
        take_profit_price=args.tp,
        stop_loss_price=args.sl,
        side=args.side,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Position management commands
# ---------------------------------------------------------------------------

def cmd_close(args):
    """Close a position."""
    result = close_position(args.symbol, percentage=args.pct)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def cmd_cancel(args):
    """Cancel an order."""
    ok = cancel_order(args.order_id)
    print(json.dumps({"cancelled": ok}))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Technical analysis
# ---------------------------------------------------------------------------

def cmd_analyze(args):
    """Run technical analysis on one or more symbols."""
    results = {}
    for symbol in args.symbols:
        try:
            df = get_bars(symbol, timeframe="1Day", days=args.days)
            signals = compute_signals(df)

            quote = get_latest_quote(symbol)
            signals["liquidity"] = {
                "bid": quote["bid_price"],
                "ask": quote["ask_price"],
                "spread_pct": quote["spread_pct"],
                "tradable": quote["spread_pct"] < 0.5,
            }

            # Check if asset is shortable
            try:
                asset = get_asset_info(symbol)
                signals["shortable"] = asset["shortable"]
            except Exception:
                signals["shortable"] = None

            results[symbol] = signals
        except Exception as e:
            results[symbol] = {"error": str(e)}

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for sym, data in results.items():
            if "error" in data:
                print(f"\n{sym}: ERROR — {data['error']}")
                continue
            sig = data["signals"]
            ind = data["indicators"]
            print(f"\n{'='*50}")
            print(f" {sym} @ ${data['price']}")
            print(f"{'='*50}")
            print(f" Long Score:  {sig['long_score']}/100 ({sig['long_direction']}, {sig['long_strength']})")
            print(f" Short Score: {sig['short_score']}/100 ({sig['short_direction']}, {sig['short_strength']})")
            print(f" EMA20: {ind['ema20']}  EMA50: {ind['ema50']}  Trend: {ind['ema_trend']}")
            print(f" MACD: {ind['macd_line']}  Signal: {ind['macd_signal']}  Hist: {ind['macd_histogram']}")
            print(f" RSI(14): {ind['rsi14']}")
            print(f" BB: [{ind['bollinger_lower']}, {ind['bollinger_middle']}, {ind['bollinger_upper']}] %B={ind['bollinger_pct_b']}")
            print(f" ATR(14): {ind['atr14']}  Vol ratio: {ind['volume_ratio']}")
            shortable = data.get("shortable")
            short_str = "Yes" if shortable else ("No" if shortable is False else "?")
            print(f" Shortable: {short_str}")
            if data.get("liquidity"):
                liq = data["liquidity"]
                print(f" Spread: {liq['spread_pct']:.3f}%  Tradable: {liq['tradable']}")
    return 0


# ---------------------------------------------------------------------------
# Auto-improve commands (history & diagnostics)
# ---------------------------------------------------------------------------

def cmd_closed_orders(args):
    """Print closed/filled orders from the last N days."""
    print(json.dumps(get_closed_orders(days=args.days), indent=2, default=str))
    return 0


def cmd_portfolio_history(args):
    """Print portfolio equity history."""
    print(json.dumps(get_portfolio_history(days=args.days), indent=2, default=str))
    return 0


def cmd_analyze_trades(args):
    """Run full trade performance analysis."""
    from trading.analyzer import full_analysis
    result = full_analysis(args.days)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        from trading.analyzer import _print_analysis
        _print_analysis(result)
    return 0


def cmd_diagnose(args):
    """Run infrastructure diagnostics."""
    from trading.diagnostics import run_diagnostics
    result = run_diagnostics(args.days)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        from trading.diagnostics import _print_diagnostics
        _print_diagnostics(result)
    return 0


def cmd_collect(args):
    """Collect data from all sources."""
    from trading.collector import collect_all
    result = collect_all(args.days)
    print(json.dumps(result, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Reconciliation & protection checks (profit protection)
# ---------------------------------------------------------------------------

def cmd_reconcile(args):
    """Reconcile progress.md with live Alpaca positions.

    Prevents phantom positions from blocking trades.
    Overwrites progress.md positions section with reality.
    Also detects non-pipeline positions (crypto, manual trades)
    and flags them so they don't inflate exposure calculations.
    """
    import re

    # Known non-pipeline asset classes (crypto, etc.)
    NON_PIPELINE_CLASSES = {"crypto"}
    # Crypto symbols pattern — BTC, ETH, SOL, etc. traded as USD pairs
    CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "AVAXUSD", "LINKUSD",
                      "UNIUSD", "AAVEUSD", "DOTUSD", "MATICUSD", "SHIBUSD", "LTCUSD"}

    progress_path = Path(__file__).resolve().parent.parent / "progress.md"
    if not progress_path.exists():
        print(json.dumps({"status": "no_progress_file", "action": "skipped"}))
        return 0

    # Get live state
    positions = get_positions()
    account = get_account()
    orders = get_open_orders()

    live_symbols = {p["symbol"] for p in positions}

    # Classify positions as pipeline vs non-pipeline
    pipeline_positions = []
    non_pipeline_positions = []
    for p in positions:
        sym = p["symbol"]
        asset_class = p.get("asset_class", "").lower()
        is_crypto = asset_class in NON_PIPELINE_CLASSES or sym in CRYPTO_SYMBOLS
        if is_crypto:
            non_pipeline_positions.append(p)
        else:
            pipeline_positions.append(p)

    # Calculate exposure only from pipeline positions
    equity = account["equity"]
    pipeline_exposure = sum(
        abs(p["market_value"]) for p in pipeline_positions
    ) / equity * 100 if equity > 0 else 0
    total_exposure = sum(
        abs(p["market_value"]) for p in positions
    ) / equity * 100 if equity > 0 else 0

    # Read progress.md and check for stale position references
    content = progress_path.read_text()

    # Extract symbols mentioned in position tables
    stale_symbols = set()
    for match in re.finditer(r"\|\s*([A-Z]{1,5})\s*\|.*\|\s*(LONG|SHORT)\s*\|", content):
        sym = match.group(1)
        if sym not in live_symbols and sym not in ("Symbol", "Ticker"):
            stale_symbols.add(sym)

    # Rebuild the positions section (pipeline only in main table)
    pos_lines = []
    if pipeline_positions:
        pos_lines.append("| Symbol | Qty | Side | Entry Price | Current Price | Unrealized P&L | P&L % |")
        pos_lines.append("|--------|-----|------|------------|--------------|----------------|-------|")
        for p in pipeline_positions:
            side = "LONG" if "LONG" in p["side"].upper() else "SHORT"
            plpc = p["unrealized_plpc"] * 100
            pos_lines.append(
                f"| {p['symbol']} | {int(abs(p['qty']))} | {side} | "
                f"${p['avg_entry_price']:.2f} | ${p['current_price']:.2f} | "
                f"${p['unrealized_pl']:.2f} | {plpc:+.2f}% |"
            )
    else:
        pos_lines.append("None.")

    # Add non-pipeline section if any exist
    if non_pipeline_positions:
        pos_lines.append("")
        pos_lines.append("**Non-pipeline positions (excluded from exposure calc):**")
        for p in non_pipeline_positions:
            side = "LONG" if "LONG" in p["side"].upper() else "SHORT"
            pos_lines.append(
                f"- {p['symbol']} {side} {int(abs(p['qty']))} @ ${p['avg_entry_price']:.2f} "
                f"(P&L: ${p['unrealized_pl']:.2f})"
            )

    needs_update = stale_symbols or non_pipeline_positions or pipeline_positions

    if needs_update:
        # Replace the Open Positions section
        new_section = "## Open Positions\n\n" + "\n".join(pos_lines)
        content = re.sub(
            r"## Open Positions\n\n.*?(?=\n## |\Z)",
            new_section + "\n\n",
            content,
            flags=re.DOTALL,
        )

        # Update account section
        cash = account["cash"]
        buying_power = account["buying_power"]
        content = re.sub(
            r"\| Equity \| \$[\d,.]+ \|",
            f"| Equity | ${equity:,.2f} |",
            content,
        )
        content = re.sub(
            r"\| Buying Power \| \$[\d,.]+ \|",
            f"| Buying Power | ${buying_power:,.2f} |",
            content,
        )
        content = re.sub(
            r"\| Cash \| \$[\d,.]+ \|",
            f"| Cash | ${cash:,.2f} |",
            content,
        )
        progress_path.write_text(content)

    result = {
        "status": "reconciled" if needs_update else "in_sync",
        "stale_symbols_removed": sorted(stale_symbols),
        "pipeline_positions": [p["symbol"] for p in pipeline_positions],
        "non_pipeline_positions": [p["symbol"] for p in non_pipeline_positions],
        "pipeline_exposure_pct": round(pipeline_exposure, 2),
        "total_exposure_pct": round(total_exposure, 2),
        "action": "progress.md updated" if needs_update else "none",
    }
    if non_pipeline_positions:
        result["warning"] = (
            f"Found {len(non_pipeline_positions)} non-pipeline position(s) "
            f"({', '.join(p['symbol'] for p in non_pipeline_positions)}). "
            f"Excluded from pipeline exposure ({pipeline_exposure:.1f}% vs {total_exposure:.1f}% total)."
        )
    print(json.dumps(result, indent=2))
    return 0


def cmd_check_protection(args):
    """Check if all open positions have OCO/bracket protection orders.

    Returns unprotected positions that need OCO orders placed.
    This is critical for profit protection — no naked positions allowed.
    """
    positions = get_positions()
    orders = get_open_orders()

    if not positions:
        print(json.dumps({"status": "no_positions", "unprotected": []}))
        return 0

    # Build set of symbols that have protection (OCO legs or bracket legs)
    protected_symbols = set()
    partial_protection: dict[str, set] = {}  # symbol -> set of order types
    for order in orders:
        sym = order["symbol"]
        order_class = order.get("order_class", "").lower()
        # Full protection: OCO or bracket order class
        # Alpaca returns "OrderClass.OCO" — use substring match
        if "oco" in order_class or "bracket" in order_class:
            protected_symbols.add(sym)
        if order.get("legs"):
            protected_symbols.add(sym)
        # Track standalone stop/limit orders as partial protection
        order_type = order.get("type", "").lower()
        if order_type in ("stop", "stop_limit"):
            partial_protection.setdefault(sym, set()).add("stop")
        elif order_type == "limit":
            partial_protection.setdefault(sym, set()).add("limit")
    # Standalone stop + limit pair = effective protection
    for sym, types in partial_protection.items():
        if "stop" in types and "limit" in types:
            protected_symbols.add(sym)

    unprotected = []
    for pos in positions:
        if pos["symbol"] not in protected_symbols:
            side = "LONG" if "LONG" in pos["side"].upper() else "SHORT"
            unprotected.append({
                "symbol": pos["symbol"],
                "qty": int(abs(pos["qty"])),
                "side": side,
                "entry_price": pos["avg_entry_price"],
                "current_price": pos["current_price"],
                "unrealized_pl": pos["unrealized_pl"],
                "oco_side": "sell" if side == "LONG" else "buy",
            })

    status = "all_protected" if not unprotected else "UNPROTECTED_POSITIONS"
    print(json.dumps({
        "status": status,
        "total_positions": len(positions),
        "protected": len(positions) - len(unprotected),
        "unprotected": unprotected,
    }, indent=2))
    return 0 if not unprotected else 1


# ---------------------------------------------------------------------------
# Fill watcher — polls for OPG fills and auto-places OCO
# ---------------------------------------------------------------------------

def cmd_watch_fills(args):
    """Poll for OPG fills and auto-place OCO protection orders.

    Reads pending_protections.json, waits for positions to appear,
    then places OCO immediately on fill. Replaces the timing-dependent
    protector.py cron by running right after market open.
    """
    import time as _time
    from trading.protector import load_protections, save_protections, has_oco_for_symbol, find_position

    protections = load_protections(args.file)
    if not protections:
        print(json.dumps({"status": "no_pending_protections", "action": "none"}))
        return 0

    print(json.dumps({
        "status": "watching",
        "pending": len(protections),
        "timeout_s": args.timeout,
        "interval_s": args.interval,
    }))

    completed = []
    remaining = list(protections)
    start = _time.time()

    while remaining and (_time.time() - start) < args.timeout:
        positions = get_positions()
        orders = get_open_orders()
        still_pending = []

        for prot in remaining:
            symbol = prot["symbol"]

            if has_oco_for_symbol(symbol, orders):
                prot["oco_status"] = "already_exists"
                completed.append(prot)
                continue

            pos = find_position(symbol, positions)
            if pos is None:
                still_pending.append(prot)
                continue

            # Position filled — place OCO immediately
            actual_qty = abs(int(float(pos["qty"])))
            result = place_oco_order(
                symbol=symbol,
                qty=actual_qty,
                take_profit_price=prot["tp"],
                stop_loss_price=prot["sl"],
                side=prot["oco_side"],
            )

            if result.success:
                prot["oco_order_id"] = result.order_id
                prot["oco_status"] = "placed"
                print(json.dumps({"event": "oco_placed", "symbol": symbol,
                                  "order_id": result.order_id, "qty": actual_qty}))
            else:
                prot["oco_status"] = "error"
                prot["oco_error"] = result.error
                print(json.dumps({"event": "oco_failed", "symbol": symbol,
                                  "error": result.error}))
            completed.append(prot)

        remaining = still_pending
        if remaining:
            _time.sleep(args.interval)

    # Save only remaining (unfilled) items
    save_protections(args.file, remaining)

    report = {
        "completed": len(completed),
        "remaining": len(remaining),
        "details": {p["symbol"]: p.get("oco_status", "timeout") for p in completed},
        "still_pending": [p["symbol"] for p in remaining],
        "elapsed_s": round(_time.time() - start, 1),
    }
    print(json.dumps(report, indent=2))
    return 0 if not remaining else 1


# ---------------------------------------------------------------------------
# Trailing stops — adjusts SL for profitable positions
# ---------------------------------------------------------------------------

def cmd_trail_stops(args):
    """Adjust stop-losses for positions that have moved in our favor.

    Logic:
    - If position P&L >= breakeven_pct: move SL to breakeven (entry price)
    - If position P&L >= breakeven_pct + trail_pct: trail SL at current - trail_pct
    - Only adjusts existing OCO orders (cancels old, places new)
    """
    positions = get_positions()
    orders = get_open_orders()

    if not positions:
        print(json.dumps({"status": "no_positions"}))
        return 0

    adjustments = []

    for pos in positions:
        symbol = pos["symbol"]
        entry = pos["avg_entry_price"]
        current = pos["current_price"]
        qty = abs(int(float(pos["qty"])))
        is_long = "LONG" in pos["side"].upper()
        pnl_pct = pos["unrealized_plpc"] * 100

        # Find existing OCO for this position
        existing_oco = None
        for order in orders:
            if order["symbol"] != symbol:
                continue
            oc = order.get("order_class", "")
            if "OCO" in oc.upper() or order.get("legs"):
                existing_oco = order
                break

        if existing_oco is None:
            continue  # No OCO to adjust

        # Extract current SL and TP from legs
        current_sl = None
        current_tp = None
        for leg in existing_oco.get("legs", []):
            if leg.get("stop_price"):
                current_sl = float(leg["stop_price"])
            if leg.get("limit_price"):
                current_tp = float(leg["limit_price"])

        # Also check parent order
        if current_tp is None and existing_oco.get("limit_price"):
            current_tp = float(existing_oco["limit_price"])

        if current_sl is None:
            continue

        # Calculate new SL
        new_sl = current_sl
        reason = None

        if is_long:
            if pnl_pct >= args.breakeven_pct:
                # At minimum, SL at breakeven
                breakeven_sl = round(entry * 1.001, 2)  # tiny buffer above entry
                if current_sl < breakeven_sl:
                    new_sl = breakeven_sl
                    reason = f"breakeven (P&L {pnl_pct:+.1f}% >= {args.breakeven_pct}%)"

                # Trail: SL at current - trail_pct
                trail_sl = round(current * (1 - args.trail_pct / 100), 2)
                if trail_sl > new_sl:
                    new_sl = trail_sl
                    reason = f"trailing {args.trail_pct}% below ${current:.2f}"
        else:
            # SHORT: SL is above entry, lower = better
            if pnl_pct >= args.breakeven_pct:
                breakeven_sl = round(entry * 0.999, 2)
                if current_sl > breakeven_sl:
                    new_sl = breakeven_sl
                    reason = f"breakeven (P&L {pnl_pct:+.1f}% >= {args.breakeven_pct}%)"

                trail_sl = round(current * (1 + args.trail_pct / 100), 2)
                if trail_sl < new_sl:
                    new_sl = trail_sl
                    reason = f"trailing {args.trail_pct}% above ${current:.2f}"

        if new_sl == current_sl or reason is None:
            continue

        adjustment = {
            "symbol": symbol,
            "side": "LONG" if is_long else "SHORT",
            "entry": entry,
            "current": current,
            "pnl_pct": round(pnl_pct, 2),
            "old_sl": current_sl,
            "new_sl": new_sl,
            "tp": current_tp,
            "reason": reason,
        }

        if args.dry_run:
            adjustment["action"] = "DRY_RUN"
            adjustments.append(adjustment)
            continue

        # Cancel old OCO and place new one
        cancel_ok = cancel_order(existing_oco["id"])
        if not cancel_ok:
            adjustment["action"] = "CANCEL_FAILED"
            adjustments.append(adjustment)
            continue

        # Place new OCO with adjusted SL
        oco_side = "sell" if is_long else "buy"
        tp = current_tp if current_tp else (
            round(entry * 1.10, 2) if is_long else round(entry * 0.90, 2)
        )
        result = place_oco_order(
            symbol=symbol,
            qty=qty,
            take_profit_price=tp,
            stop_loss_price=new_sl,
            side=oco_side,
        )

        if result.success:
            adjustment["action"] = "ADJUSTED"
            adjustment["new_order_id"] = result.order_id
        else:
            # RACE CONDITION FIX: Old OCO cancelled but new one failed.
            # Position is now NAKED. Attempt to restore original protection.
            restore = place_oco_order(
                symbol=symbol,
                qty=qty,
                take_profit_price=tp,
                stop_loss_price=current_sl,  # restore ORIGINAL SL
                side=oco_side,
            )
            if restore.success:
                adjustment["action"] = "REPLACE_FAILED_RESTORED"
                adjustment["restored_order_id"] = restore.order_id
                adjustment["original_error"] = result.error
            else:
                adjustment["action"] = "REPLACE_FAILED_NAKED"
                adjustment["error"] = (
                    f"New OCO failed: {result.error}. "
                    f"Restore also failed: {restore.error}. "
                    f"POSITION IS UNPROTECTED — manual intervention needed."
                )

        adjustments.append(adjustment)

    print(json.dumps({
        "status": "done",
        "dry_run": args.dry_run,
        "adjustments": adjustments,
        "positions_checked": len(positions),
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Time stops — enforce max holding period
# ---------------------------------------------------------------------------

def cmd_time_stops(args):
    """Identify positions held longer than max_days for forced exit.

    Enforces the 10-day time stop rule from CLAUDE.md.
    Fail-closed: if created_at is missing or unparseable, flag the position.
    """
    from datetime import datetime as _dt, timezone as _tz

    positions = get_positions()
    if not positions:
        print(json.dumps({"status": "no_positions", "expired": []}))
        return 0

    now = _dt.now(_tz.utc)
    expired = []
    active = []

    for pos in positions:
        symbol = pos["symbol"]
        side = "LONG" if "LONG" in pos.get("side", "").upper() else "SHORT"
        qty = int(abs(float(pos.get("qty", 0))))
        created_str = pos.get("created_at", "")

        if not created_str:
            expired.append({
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "days_held": None,
                "reason": "no created_at — cannot determine age, flagged for review",
            })
            continue

        try:
            created = _dt.fromisoformat(created_str.replace("Z", "+00:00"))
            days_held = (now - created).days
        except (ValueError, TypeError):
            days_held = args.max_days + 1  # fail-closed

        info = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "days_held": days_held,
            "entry_price": pos.get("avg_entry_price"),
            "current_price": pos.get("current_price"),
            "unrealized_pl": pos.get("unrealized_pl"),
        }

        if days_held >= args.max_days:
            info["reason"] = f"held {days_held} days >= {args.max_days} max"
            expired.append(info)
        else:
            info["days_remaining"] = args.max_days - days_held
            active.append(info)

    status = "EXPIRED_POSITIONS" if expired else "all_within_limits"
    print(json.dumps({
        "status": status,
        "max_days": args.max_days,
        "expired": expired,
        "active": active,
    }, indent=2))
    return 0 if not expired else 1


# ---------------------------------------------------------------------------
# R/R validation — re-check with live bid/ask before order placement
# ---------------------------------------------------------------------------

def cmd_validate_rr(args):
    """Validate risk/reward ratio using live bid/ask prices.

    Prevents Disaster 5 (OPG gap risk): planned R/R can collapse
    when actual execution price differs from analysis-time price.
    Must be run BEFORE placing any order.
    """
    symbol = args.symbol
    entry = args.entry
    tp = args.tp
    sl = args.sl
    is_short = args.side == "sell"
    min_rr = args.min_rr

    # Get live quote
    try:
        quote = get_latest_quote(symbol)
    except Exception as e:
        print(json.dumps({
            "valid": False,
            "symbol": symbol,
            "error": f"Cannot get live quote: {e}",
        }, indent=2))
        return 1

    # Determine realistic entry based on side
    if is_short:
        live_entry = quote["bid_price"]  # shorts enter at bid
        reward = entry - tp if tp < entry else 0  # planned reward
        risk = sl - entry if sl > entry else 0  # planned risk
        live_reward = live_entry - tp
        live_risk = sl - live_entry
    else:
        live_entry = quote["ask_price"]  # longs enter at ask
        reward = tp - entry if tp > entry else 0
        risk = entry - sl if sl < entry else 0
        live_reward = tp - live_entry
        live_risk = live_entry - sl

    # Calculate R/R ratios
    planned_rr = reward / risk if risk > 0 else 0
    live_rr = live_reward / live_risk if live_risk > 0 else 0

    # Entry drift from planned
    drift_pct = abs(live_entry - entry) / entry * 100 if entry > 0 else 0

    valid = live_rr >= min_rr and live_reward > 0 and live_risk > 0
    status = "PASS" if valid else "FAIL"

    result = {
        "valid": valid,
        "status": status,
        "symbol": symbol,
        "side": "SHORT" if is_short else "LONG",
        "planned": {
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "rr": round(planned_rr, 2),
        },
        "live": {
            "bid": quote["bid_price"],
            "ask": quote["ask_price"],
            "spread_pct": quote["spread_pct"],
            "effective_entry": live_entry,
            "rr": round(live_rr, 2),
            "reward": round(live_reward, 2),
            "risk": round(live_risk, 2),
        },
        "drift_pct": round(drift_pct, 2),
        "min_rr": min_rr,
    }

    if not valid:
        reasons = []
        if live_rr < min_rr:
            reasons.append(f"R/R {live_rr:.2f} < min {min_rr}")
        if live_reward <= 0:
            reasons.append(f"negative reward ({live_reward:.2f})")
        if live_risk <= 0:
            reasons.append(f"invalid risk ({live_risk:.2f})")
        if drift_pct > 2:
            reasons.append(f"entry drift {drift_pct:.1f}% from plan")
        result["rejection_reasons"] = reasons

    print(json.dumps(result, indent=2))
    return 0 if valid else 1


# ---------------------------------------------------------------------------
# Sector lookup
# ---------------------------------------------------------------------------

def cmd_sector(args):
    """Show GICS sector for one or more symbols."""
    results = {}
    for sym in args.symbols:
        sector = get_sector(sym)
        results[sym] = sector
    print(json.dumps(results, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Claude Trading CLI — all Alpaca operations via alpaca-py SDK"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- Data queries ---

    p = sub.add_parser("account", help="Show account info (equity, buying power)")
    p.set_defaults(func=cmd_account)

    p = sub.add_parser("positions", help="Show all open positions")
    p.set_defaults(func=cmd_positions)

    p = sub.add_parser("orders", help="Show all open orders")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("clock", help="Show market clock (open/close times)")
    p.set_defaults(func=cmd_clock)

    p = sub.add_parser("quote", help="Get latest bid/ask quote")
    p.add_argument("symbols", nargs="+", help="Ticker symbol(s)")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("bars", help="Fetch OHLCV bars")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("--timeframe", default="1Day", choices=["1Min", "5Min", "15Min", "1Hour", "1Day", "1Week"])
    p.add_argument("--days", type=int, default=60, help="Days of history (default 60)")
    p.add_argument("--last", type=int, default=None, help="Only show last N bars")
    p.set_defaults(func=cmd_bars)

    p = sub.add_parser("latest-trade", help="Get latest trade for a symbol")
    p.add_argument("symbol", help="Ticker symbol")
    p.set_defaults(func=cmd_latest_trade)

    p = sub.add_parser("latest-bar", help="Get latest bar for a symbol")
    p.add_argument("symbol", help="Ticker symbol")
    p.set_defaults(func=cmd_latest_bar)

    p = sub.add_parser("asset", help="Get asset info (tradability, exchange)")
    p.add_argument("symbol", help="Ticker symbol")
    p.set_defaults(func=cmd_asset)

    p = sub.add_parser("status", help="Show account, positions, orders, clock")
    p.set_defaults(func=cmd_status)

    # --- Order placement ---

    p = sub.add_parser("bracket", help="Place bracket order (entry + TP + SL)")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("qty", type=int, help="Number of shares")
    p.add_argument("tp", type=float, help="Take-profit price")
    p.add_argument("sl", type=float, help="Stop-loss price")
    p.add_argument("--side", default="buy", choices=["buy", "sell"], help="buy=long, sell=short (default: buy)")
    p.add_argument("--limit", type=float, default=None, help="Limit entry price (STRONGLY recommended)")
    p.add_argument("--tif", default="gtc", choices=["day", "gtc"], help="Time-in-force (default: gtc)")
    p.add_argument("--no-validate", dest="validate", action="store_false", help="Skip R/R validation (NOT recommended)")
    p.add_argument("--min-rr", type=float, default=1.5, help="Min R/R for validation (default 1.5, use 1.3 for VIX>28)")
    p.set_defaults(func=cmd_bracket, validate=True)

    p = sub.add_parser("opg", help="Place OPG (market-on-open) order")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("qty", type=int, help="Number of shares")
    p.add_argument("--side", default="buy", choices=["buy", "sell"], help="buy=long, sell=short (default: buy)")
    p.add_argument("--limit", type=float, default=None, help="Limit price for LOO")
    p.set_defaults(func=cmd_opg)

    p = sub.add_parser("oco", help="Place OCO protection order")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("qty", type=int, help="Number of shares")
    p.add_argument("tp", type=float, help="Take-profit price")
    p.add_argument("sl", type=float, help="Stop-loss price")
    p.add_argument("--side", default="sell", choices=["buy", "sell"], help="sell=close long, buy=cover short (default: sell)")
    p.set_defaults(func=cmd_oco)

    # --- Position management ---

    p = sub.add_parser("close", help="Close a position (long or short)")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("--pct", type=float, default=None, help="Partial close percentage")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("cancel", help="Cancel an order")
    p.add_argument("order_id", help="Order UUID")
    p.set_defaults(func=cmd_cancel)

    # --- Technical analysis ---

    p = sub.add_parser("analyze", help="Technical analysis on symbol(s)")
    p.add_argument("symbols", nargs="+", help="Ticker symbol(s)")
    p.add_argument("--days", type=int, default=60, help="Days of history (default 60)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_analyze)

    # --- Auto-improve (history & diagnostics) ---

    p = sub.add_parser("closed-orders", help="Show closed orders (last N days)")
    p.add_argument("--days", type=int, default=30, help="Lookback period (default 30)")
    p.set_defaults(func=cmd_closed_orders)

    p = sub.add_parser("portfolio-history", help="Show portfolio equity history")
    p.add_argument("--days", type=int, default=30, help="Lookback period (default 30)")
    p.set_defaults(func=cmd_portfolio_history)

    p = sub.add_parser("analyze-trades", help="Full trade performance analysis")
    p.add_argument("--days", type=int, default=30, help="Lookback period (default 30)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_analyze_trades)

    p = sub.add_parser("diagnose", help="Infrastructure diagnostics")
    p.add_argument("--days", type=int, default=30, help="Lookback period (default 30)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("collect", help="Collect data from all sources (Scaleway + Alpaca + reports)")
    p.add_argument("--days", type=int, default=30, help="Lookback period (default 30)")
    p.set_defaults(func=cmd_collect)

    # --- Reconciliation & protection ---

    p = sub.add_parser("reconcile", help="Reconcile progress.md with live Alpaca positions")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("check-protection", help="Check all positions have SL/TP protection")
    p.set_defaults(func=cmd_check_protection)

    p = sub.add_parser("watch-fills", help="Poll for OPG fills and auto-place OCO protection")
    p.add_argument("--file", default="pending_protections.json", help="Pending protections JSON")
    p.add_argument("--timeout", type=int, default=300, help="Max seconds to poll (default 300)")
    p.add_argument("--interval", type=int, default=15, help="Poll interval in seconds (default 15)")
    p.set_defaults(func=cmd_watch_fills)

    p = sub.add_parser("trail-stops", help="Adjust stop-losses for profitable positions")
    p.add_argument("--breakeven-pct", type=float, default=3.0, help="Move SL to breakeven at this %% gain (default 3.0)")
    p.add_argument("--trail-pct", type=float, default=2.0, help="Trail SL this %% below high watermark after breakeven-pct (default 2.0)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without modifying orders")
    p.set_defaults(func=cmd_trail_stops)

    p = sub.add_parser("time-stops", help="Check positions held > N days (time stop rule)")
    p.add_argument("--max-days", type=int, default=10, help="Max trading days to hold (default 10)")
    p.set_defaults(func=cmd_time_stops)

    p = sub.add_parser("validate-rr", help="Validate R/R with live bid/ask before placing order")
    p.add_argument("symbol", help="Ticker symbol")
    p.add_argument("entry", type=float, help="Planned entry price")
    p.add_argument("tp", type=float, help="Take-profit price")
    p.add_argument("sl", type=float, help="Stop-loss price")
    p.add_argument("--side", default="buy", choices=["buy", "sell"], help="buy=long, sell=short (default: buy)")
    p.add_argument("--min-rr", type=float, default=1.5, help="Minimum R/R ratio (default 1.5, use 1.3 for VIX>28)")
    p.set_defaults(func=cmd_validate_rr)

    p = sub.add_parser("sector", help="Show GICS sector for symbol(s)")
    p.add_argument("symbols", nargs="+", help="Ticker symbol(s)")
    p.set_defaults(func=cmd_sector)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
