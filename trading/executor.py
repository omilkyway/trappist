#!/usr/bin/env python3
"""TRAPPIST executor — 5 commands, no bloat.

  python trading/executor.py status
  python trading/executor.py scan [--pairs BTC,ETH,SOL] [--timeframe 4h]
  python trading/executor.py bracket BTC/USDT:USDT 0.002 76000 71000 --side buy [--limit 72700] [--leverage 5]
  python trading/executor.py close BTC/USDT:USDT
  python trading/executor.py protect [--trail] [--max-days 10]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import (
    cancel_all_orders,
    close_position,
    format_symbol,
    get_account,
    get_active_pairs,
    get_bars,
    get_funding_rate,
    get_open_orders,
    get_positions,
    get_ticker,
    is_sandbox,
    place_bracket_order,
    place_stop_order,
    place_tp_order,
    cancel_order,
)
from trading.categories import check_category_limit, get_category
from trading.indicators import compute_signals


# ---------------------------------------------------------------------------
# status — Everything in one shot
# ---------------------------------------------------------------------------

def cmd_status(args):
    """Full dashboard: account + positions + orders + exposure."""
    account = get_account()
    positions = get_positions()
    orders = get_open_orders()

    # Check protection
    unprotected = []
    for pos in positions:
        has_sl = any(
            o["symbol"] == pos["symbol"] and o.get("reduce_only") and "STOP" in o.get("type", "").upper()
            for o in orders
        )
        has_tp = any(
            o["symbol"] == pos["symbol"] and o.get("reduce_only") and "PROFIT" in o.get("type", "").upper()
            for o in orders
        )
        if not (has_sl and has_tp):
            unprotected.append(pos["symbol"])

    # Read state.json
    state = {}
    state_path = Path("state.json")
    if state_path.exists():
        try:
            state = json.load(open(state_path))
        except Exception:
            pass

    output = {
        "mode": "TESTNET" if is_sandbox() else "LIVE",
        "equity": account["equity"],
        "free": account["free"],
        "exposure_pct": account["exposure_pct"],
        "unrealized_pnl": account["unrealized_pnl"],
        "positions": positions,
        "open_orders": len(orders),
        "unprotected": unprotected,
        "killed": state.get("killed", False),
        "initial_balance": state.get("initial_balance", 0),
        "total_trades": len(state.get("trades", [])),
    }
    print(json.dumps(output, indent=2))
    return 0


# ---------------------------------------------------------------------------
# scan — Technical analysis + funding on all pairs
# ---------------------------------------------------------------------------

def cmd_scan(args):
    """Dual technical analysis + funding rate on all active pairs."""
    if args.pairs:
        pairs = [format_symbol(p.strip()) for p in args.pairs.split(",")]
    else:
        pairs = get_active_pairs()

    results = {}
    for sym in pairs:
        try:
            df = get_bars(sym, timeframe=args.timeframe, limit=500)
            if len(df) < 50:
                results[sym] = {"error": f"Only {len(df)} bars (need 50+)"}
                continue

            # Funding rate
            fr = get_funding_rate(sym)
            funding_pct = fr.get("funding_rate_pct", 0)

            # Technical signals with funding integrated
            signals = compute_signals(df, funding_rate=funding_pct)
            signals["funding_rate_pct"] = funding_pct
            signals["category"] = get_category(sym)

            # Current price from ticker for fresh data
            try:
                t = get_ticker(sym)
                signals["bid"] = t["bid"]
                signals["ask"] = t["ask"]
                signals["price"] = t["last"]
                signals["change_24h"] = t["change_pct"]
                signals["volume_24h"] = t["volume_24h"]
            except Exception:
                pass

            results[sym] = signals
        except Exception as e:
            results[sym] = {"error": str(e)}

    print(json.dumps(results, indent=2))
    return 0


# ---------------------------------------------------------------------------
# bracket — One command, auto-everything
# ---------------------------------------------------------------------------

def cmd_bracket(args):
    """Place bracket order with auto leverage + margin + R/R validation."""
    symbol = format_symbol(args.symbol)
    side = args.side.lower()

    # Category check
    try:
        positions = get_positions()
        orders = get_open_orders()
        allowed, reason = check_category_limit(symbol, positions, orders)
        if not allowed:
            print(json.dumps({"success": False, "error": reason}, indent=2))
            return 1
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Category check failed: {e}"}, indent=2))
        return 1

    # R/R validation with live price
    if not args.no_validate:
        try:
            ticker = get_ticker(symbol)
            live_price = ticker["last"]
            # Fallback for testnet (bid/ask may be 0)
            live_entry = ticker["ask"] if ticker["ask"] > 0 else live_price
            if side == "sell":
                live_entry = ticker["bid"] if ticker["bid"] > 0 else live_price

            if side == "buy":
                reward = args.tp - live_entry
                risk = live_entry - args.sl
            else:
                reward = live_entry - args.tp
                risk = args.sl - live_entry

            rr = round(reward / risk, 2) if risk > 0 else 0
            min_rr = args.min_rr

            if rr < min_rr or reward <= 0 or risk <= 0:
                print(json.dumps({
                    "success": False,
                    "error": f"R/R {rr} < {min_rr} (reward={reward:.2f}, risk={risk:.2f}, live_entry={live_entry:.2f})",
                }, indent=2))
                return 1
        except Exception as e:
            print(json.dumps({"success": False, "error": f"R/R validation failed: {e}"}, indent=2))
            return 1

    result = place_bracket_order(
        symbol=symbol,
        qty=args.qty,
        tp=args.tp,
        sl=args.sl,
        side=side,
        entry_price=args.limit,
        leverage=args.leverage,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# close — Close position + cancel related orders
# ---------------------------------------------------------------------------

def cmd_close(args):
    """Close a position and cancel all related orders."""
    symbol = format_symbol(args.symbol)

    # Cancel all orders for this symbol first
    try:
        cancel_all_orders(symbol)
    except Exception:
        pass

    result = close_position(symbol)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# protect — Check + fix protection + trail stops + time stops + reconcile
# ---------------------------------------------------------------------------

def cmd_protect(args):
    """All-in-one position management: reconcile, check protection, trail, time stops."""
    positions = get_positions()
    orders = get_open_orders()
    account = get_account()

    if not positions:
        print(json.dumps({"status": "no_positions", "actions": []}, indent=2))
        return 0

    actions = []

    for pos in positions:
        sym = pos["symbol"]
        side = pos["side"]
        entry = pos["entry_price"]
        current = pos["mark_price"]
        pnl_pct = pos["pnl_pct"]
        contracts = pos["contracts"]
        close_side = "sell" if side == "long" else "buy"

        # Check SL/TP existence
        has_sl = any(
            o["symbol"] == sym and o.get("reduce_only") and "STOP" in o.get("type", "").upper()
            for o in orders
        )
        has_tp = any(
            o["symbol"] == sym and o.get("reduce_only") and "PROFIT" in o.get("type", "").upper()
            for o in orders
        )

        # Fix missing protection (emergency 7% SL, 10% TP)
        if not has_sl:
            sl_price = round(entry * (0.93 if side == "long" else 1.07), 2)
            result = place_stop_order(sym, contracts, sl_price, side=close_side)
            actions.append({"symbol": sym, "action": "EMERGENCY_SL", "price": sl_price,
                           "success": result.success, "error": result.error})

        if not has_tp:
            tp_price = round(entry * (1.10 if side == "long" else 0.90), 2)
            result = place_tp_order(sym, contracts, tp_price, side=close_side)
            actions.append({"symbol": sym, "action": "EMERGENCY_TP", "price": tp_price,
                           "success": result.success, "error": result.error})

        # Time stop check
        ts = pos.get("timestamp")
        if ts and args.max_days:
            try:
                opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                days_held = (datetime.now(timezone.utc) - opened).days
                if days_held > args.max_days:
                    actions.append({"symbol": sym, "action": "TIME_STOP_WARNING",
                                   "days_held": days_held, "max": args.max_days})
            except Exception:
                pass

        # Trail stops (only if --trail flag)
        if args.trail and has_sl:
            sl_order = next(
                (o for o in orders
                 if o["symbol"] == sym and o.get("reduce_only")
                 and "STOP" in o.get("type", "").upper()),
                None,
            )
            if sl_order:
                current_sl = sl_order["stop_price"]
                new_sl = None

                if side == "long" and pnl_pct >= 5.0:
                    new_sl = round(current * 0.97, 2)  # Trail at 3%
                    if new_sl <= current_sl:
                        new_sl = None
                elif side == "long" and pnl_pct >= 3.0:
                    new_sl = entry  # Breakeven
                    if new_sl <= current_sl:
                        new_sl = None
                elif side == "short" and pnl_pct >= 5.0:
                    new_sl = round(current * 1.03, 2)
                    if new_sl >= current_sl:
                        new_sl = None
                elif side == "short" and pnl_pct >= 3.0:
                    new_sl = entry
                    if new_sl >= current_sl:
                        new_sl = None

                if new_sl and not args.dry_run:
                    cancel_order(sl_order["id"], sym)
                    result = place_stop_order(sym, contracts, new_sl, side=close_side)
                    actions.append({"symbol": sym, "action": "TRAIL_SL",
                                   "old_sl": current_sl, "new_sl": new_sl,
                                   "pnl_pct": pnl_pct, "success": result.success})
                elif new_sl:
                    actions.append({"symbol": sym, "action": "WOULD_TRAIL (dry-run)",
                                   "old_sl": current_sl, "new_sl": new_sl, "pnl_pct": pnl_pct})

    # Reconcile progress.md
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# TRAPPIST Portfolio — {now}",
        f"Equity: {account['equity']:,.2f} USDT | Exposure: {account['exposure_pct']:.1f}%",
        "",
    ]
    if positions:
        lines.append("| Symbol | Side | Size | Entry | Mark | PnL% | Leverage |")
        lines.append("|--------|------|------|-------|------|------|----------|")
        for p in positions:
            lines.append(
                f"| {p['symbol']} | {p['side']} | {p['contracts']} | "
                f"{p['entry_price']:,.2f} | {p['mark_price']:,.2f} | "
                f"{p['pnl_pct']:+.2f}% | {p['leverage']}x |"
            )
    Path("progress.md").write_text("\n".join(lines))

    print(json.dumps({"status": "done", "positions": len(positions), "actions": actions}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TRAPPIST — 5 commands, no bloat")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("status", help="Full dashboard")

    s = sub.add_parser("scan", help="Technical analysis + funding on all pairs")
    s.add_argument("--pairs", "-p", default=None, help="Comma-separated pairs (default: all active)")
    s.add_argument("--timeframe", "-t", default="4h", help="Timeframe (default: 4h)")

    b = sub.add_parser("bracket", help="Place bracket order (entry + SL + TP)")
    b.add_argument("symbol", help="Symbol (BTC, ETH, BTC/USDT:USDT)")
    b.add_argument("qty", type=float, help="Size in base currency")
    b.add_argument("tp", type=float, help="Take-profit price")
    b.add_argument("sl", type=float, help="Stop-loss price")
    b.add_argument("--side", default="buy", choices=["buy", "sell"], help="buy=LONG, sell=SHORT")
    b.add_argument("--limit", type=float, default=None, help="Limit entry price (None=market)")
    b.add_argument("--leverage", type=int, default=5, help="Leverage (default: 5)")
    b.add_argument("--min-rr", type=float, default=1.5, help="Min R/R ratio (default: 1.5)")
    b.add_argument("--no-validate", action="store_true", help="Skip R/R validation")

    c = sub.add_parser("close", help="Close position + cancel orders")
    c.add_argument("symbol", help="Symbol")

    pr = sub.add_parser("protect", help="Check/fix protection + trail + time stops")
    pr.add_argument("--trail", action="store_true", help="Enable trailing stops")
    pr.add_argument("--max-days", type=int, default=10, help="Max hold days (default: 10)")
    pr.add_argument("--dry-run", action="store_true", help="Show without executing")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "status": cmd_status,
        "scan": cmd_scan,
        "bracket": cmd_bracket,
        "close": cmd_close,
        "protect": cmd_protect,
    }

    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
