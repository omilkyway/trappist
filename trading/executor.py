#!/usr/bin/env python3
"""Trade executor & data CLI for crypto futures — CCXT + Binance Futures.

Usage (called by agents via Bash):

  # Data queries (read-only)
  python trading/executor.py account
  python trading/executor.py positions
  python trading/executor.py orders
  python trading/executor.py quote BTC/USDT:USDT
  python trading/executor.py quote BTC/USDT:USDT ETH/USDT:USDT
  python trading/executor.py bars BTC/USDT:USDT --timeframe 4h --limit 200
  python trading/executor.py asset BTC/USDT:USDT
  python trading/executor.py funding BTC/USDT:USDT
  python trading/executor.py analyze BTC/USDT:USDT ETH/USDT:USDT --json
  python trading/executor.py status

  # Order placement (--side buy|sell, default buy)
  python trading/executor.py bracket BTC/USDT:USDT 0.01 95000 88000
  python trading/executor.py bracket BTC/USDT:USDT 0.01 85000 92000 --side sell
  python trading/executor.py bracket BTC/USDT:USDT 0.01 95000 88000 --limit 90000 --leverage 5
  python trading/executor.py bracket BTC/USDT:USDT 0.01 95000 88000 --min-rr 1.5

  # Position management
  python trading/executor.py close BTC/USDT:USDT
  python trading/executor.py cancel ORDER_ID BTC/USDT:USDT
  python trading/executor.py cancel-all BTC/USDT:USDT
  python trading/executor.py set-leverage BTC/USDT:USDT 10
  python trading/executor.py set-margin BTC/USDT:USDT isolated

  # Portfolio management
  python trading/executor.py reconcile
  python trading/executor.py check-protection
  python trading/executor.py validate-rr BTC/USDT:USDT 90000 95000 88000 --side buy --min-rr 1.5
  python trading/executor.py time-stops --max-days 10
  python trading/executor.py trail-stops --dry-run
  python trading/executor.py category BTC ETH SOL

  # History
  python trading/executor.py closed-orders --days 30
  python trading/executor.py trades BTC/USDT:USDT --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import (
    OrderResult,
    cancel_all_orders,
    cancel_order,
    close_position,
    format_symbol,
    get_account,
    get_balance,
    get_bars,
    get_closed_orders,
    get_funding_rate,
    get_market_info,
    get_open_orders,
    get_positions,
    get_ticker,
    get_trades,
    is_sandbox,
    place_bracket_order,
    place_market_order,
    place_stop_order,
    place_tp_order,
    set_leverage,
    set_margin_mode,
)
from trading.categories import check_category_limit, get_category, normalize_symbol
from trading.indicators import compute_signals


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
    symbol = args.symbol if hasattr(args, "symbol") and args.symbol else None
    print(json.dumps(get_open_orders(symbol), indent=2))
    return 0


def cmd_quote(args):
    """Get ticker info for one or more symbols."""
    symbols = [format_symbol(s) for s in args.symbols]
    if len(symbols) == 1:
        ticker = get_ticker(symbols[0])
        # Also fetch funding rate
        fr = get_funding_rate(symbols[0])
        ticker["funding_rate"] = fr.get("funding_rate", 0)
        ticker["funding_rate_pct"] = fr.get("funding_rate_pct", 0)
        ticker["next_funding"] = fr.get("next_funding_time")
        print(json.dumps(ticker, indent=2))
    else:
        results = {}
        for sym in symbols:
            try:
                ticker = get_ticker(sym)
                fr = get_funding_rate(sym)
                ticker["funding_rate"] = fr.get("funding_rate", 0)
                ticker["funding_rate_pct"] = fr.get("funding_rate_pct", 0)
                results[sym] = ticker
            except Exception as e:
                results[sym] = {"error": str(e)}
        print(json.dumps(results, indent=2))
    return 0


def cmd_bars(args):
    """Fetch OHLCV bars as JSON."""
    symbol = format_symbol(args.symbol)
    df = get_bars(symbol, timeframe=args.timeframe, limit=args.limit)
    records = []
    for ts, row in df.iterrows():
        records.append({
            "timestamp": str(ts),
            "open": round(float(row["open"]), 8),
            "high": round(float(row["high"]), 8),
            "low": round(float(row["low"]), 8),
            "close": round(float(row["close"]), 8),
            "volume": float(row["volume"]),
        })
    output = {"symbol": symbol, "timeframe": args.timeframe, "count": len(records), "bars": records}
    if args.last:
        output["bars"] = records[-args.last:]
        output["count"] = len(output["bars"])
    print(json.dumps(output, indent=2))
    return 0


def cmd_asset(args):
    """Get market info for a symbol."""
    symbol = format_symbol(args.symbol)
    print(json.dumps(get_market_info(symbol), indent=2))
    return 0


def cmd_funding(args):
    """Get funding rate for one or more symbols."""
    symbols = [format_symbol(s) for s in args.symbols]
    if len(symbols) == 1:
        print(json.dumps(get_funding_rate(symbols[0]), indent=2))
    else:
        results = {}
        for sym in symbols:
            try:
                results[sym] = get_funding_rate(sym)
            except Exception as e:
                results[sym] = {"error": str(e)}
        print(json.dumps(results, indent=2))
    return 0


def cmd_status(args):
    """Print account, positions, orders — full dashboard."""
    output = {
        "mode": "TESTNET" if is_sandbox() else "LIVE",
        "account": get_account(),
        "positions": get_positions(),
        "open_orders": get_open_orders(),
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_analyze(args):
    """Run dual technical analysis on one or more symbols."""
    symbols = [format_symbol(s) for s in args.symbols]
    results = {}
    for sym in symbols:
        try:
            df = get_bars(sym, timeframe=args.timeframe, limit=500)
            if len(df) < 50:
                results[sym] = {"error": f"Insufficient data: {len(df)} bars (need 50+)"}
                continue
            signals = compute_signals(df)
            # Add funding rate if available
            try:
                fr = get_funding_rate(sym)
                signals["funding_rate"] = fr.get("funding_rate", 0)
                signals["funding_rate_pct"] = fr.get("funding_rate_pct", 0)
            except Exception:
                pass
            # Add category
            signals["category"] = get_category(sym)
            results[sym] = signals
        except Exception as e:
            results[sym] = {"error": str(e)}

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        # Human-readable output
        for sym, data in results.items():
            if "error" in data:
                print(f"\n{sym}: ERROR — {data['error']}")
                continue
            signals = data.get("signals", {})
            print(f"\n{'='*50}")
            print(f"  {sym} | {data.get('category', '?')}")
            print(f"  Price: {data.get('price', 0):,.2f}")
            print(f"  Long score:  {signals.get('long_score', 0)}/100 ({signals.get('long_strength', '?')})")
            print(f"  Short score: {signals.get('short_score', 0)}/100 ({signals.get('short_strength', '?')})")
            fr = data.get("funding_rate_pct", 0)
            print(f"  Funding: {fr:+.4f}%")
            ind = data.get("indicators", {})
            print(f"  RSI: {ind.get('rsi14', 0):.1f} | ATR: {ind.get('atr14', 0):.2f}")
            print(f"  EMA20: {ind.get('ema20', 0):,.2f} | EMA50: {ind.get('ema50', 0):,.2f} | Trend: {ind.get('ema_trend', '?')}")
    return 0


# ---------------------------------------------------------------------------
# Order placement commands
# ---------------------------------------------------------------------------

def _check_category_before_order(symbol: str) -> dict | None:
    """Check category limit before placing an order. Returns error dict or None.

    Fail-closed: if the check itself errors, BLOCK the order.
    """
    try:
        positions = get_positions()
        orders = get_open_orders()
        allowed, reason = check_category_limit(symbol, positions, orders)
        if not allowed:
            return {"success": False, "error": reason, "category": get_category(symbol)}
    except Exception as e:
        return {
            "success": False,
            "error": f"Category check failed (fail-closed): {e}",
            "category": get_category(symbol),
        }
    return None


def cmd_bracket(args):
    """Place a bracket order (long or short) with SL + TP protection.

    When --validate is set (default), runs R/R validation with live bid/ask
    BEFORE placing the order.
    """
    symbol = format_symbol(args.symbol)
    side = args.side.lower()

    # Category check
    cat_err = _check_category_before_order(symbol)
    if cat_err:
        print(json.dumps(cat_err, indent=2))
        return 1

    # R/R validation if enabled (default)
    if not args.no_validate:
        entry = args.limit if args.limit else None
        if not entry:
            # Use current price for validation
            try:
                ticker = get_ticker(symbol)
                entry = ticker["ask"] if side == "buy" else ticker["bid"]
            except Exception as e:
                print(json.dumps({"success": False, "error": f"Cannot get price for R/R validation: {e}"}, indent=2))
                return 1

        rr_result = _validate_rr(symbol, entry, args.tp, args.sl, side, args.min_rr)
        if not rr_result["valid"]:
            print(json.dumps({
                "success": False,
                "error": f"R/R validation FAILED: {rr_result.get('status')}",
                "validation": rr_result,
            }, indent=2))
            return 1

    # Place the order
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


def cmd_close(args):
    """Close a position."""
    symbol = format_symbol(args.symbol)

    # Cancel related open orders first
    try:
        cancel_all_orders(symbol)
    except Exception:
        pass  # Best effort

    result = close_position(symbol)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def cmd_cancel(args):
    """Cancel an order by ID."""
    success = cancel_order(args.order_id, format_symbol(args.symbol))
    print(json.dumps({"success": success, "order_id": args.order_id}, indent=2))
    return 0 if success else 1


def cmd_cancel_all(args):
    """Cancel all open orders for a symbol."""
    symbol = format_symbol(args.symbol)
    result = cancel_all_orders(symbol)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


def cmd_set_leverage(args):
    """Set leverage for a symbol."""
    symbol = format_symbol(args.symbol)
    result = set_leverage(symbol, args.leverage)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


def cmd_set_margin(args):
    """Set margin mode for a symbol."""
    symbol = format_symbol(args.symbol)
    result = set_margin_mode(symbol, args.mode)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


# ---------------------------------------------------------------------------
# Portfolio management commands
# ---------------------------------------------------------------------------

def _validate_rr(
    symbol: str, entry: float, tp: float, sl: float,
    side: str, min_rr: float = 1.5,
) -> dict:
    """Validate risk/reward ratio using live prices."""
    try:
        ticker = get_ticker(symbol)
        live_bid = ticker["bid"]
        live_ask = ticker["ask"]
        last_price = ticker["last"]

        # Testnet often returns 0 for bid/ask — fall back to last price
        if live_ask <= 0:
            live_ask = last_price
        if live_bid <= 0:
            live_bid = last_price

        if side == "buy":
            live_entry = live_ask  # buying at ask
            live_reward = tp - live_entry
            live_risk = live_entry - sl
        else:
            live_entry = live_bid  # selling at bid
            live_reward = live_entry - tp
            live_risk = sl - live_entry

        live_rr = round(live_reward / live_risk, 3) if live_risk > 0 else 0
        valid = live_rr >= min_rr and live_reward > 0 and live_risk > 0

        rejection_reasons = []
        if live_reward <= 0:
            rejection_reasons.append(f"Negative reward: {live_reward:.2f}")
        if live_risk <= 0:
            rejection_reasons.append(f"Negative risk: {live_risk:.2f}")
        if live_rr < min_rr:
            rejection_reasons.append(f"R/R {live_rr:.2f} < min {min_rr}")

        return {
            "valid": valid,
            "status": "PASS" if valid else "FAIL",
            "planned": {"entry": entry, "tp": tp, "sl": sl, "side": side},
            "live": {
                "entry": live_entry,
                "bid": live_bid,
                "ask": live_ask,
                "reward": round(live_reward, 4),
                "risk": round(live_risk, 4),
                "rr": live_rr,
            },
            "min_rr": min_rr,
            "rejection_reasons": rejection_reasons,
        }
    except Exception as e:
        return {"valid": False, "status": f"ERROR: {e}", "rejection_reasons": [str(e)]}


def cmd_validate_rr(args):
    """Validate R/R ratio with live prices before order placement."""
    symbol = format_symbol(args.symbol)
    result = _validate_rr(symbol, args.entry, args.tp, args.sl, args.side, args.min_rr)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


def cmd_reconcile(args):
    """Sync progress.md with live exchange state.

    This is MANDATORY before every trading session to prevent phantom blocking.
    """
    positions = get_positions()
    account = get_account()
    orders = get_open_orders()

    equity = account["equity"]
    exposure = account["total_exposure"]
    exposure_pct = account["exposure_pct"]

    # Read current progress.md if it exists
    progress_path = Path("progress.md")
    stale_symbols = []

    if progress_path.exists():
        content = progress_path.read_text()
        # Simple check: if progress mentions positions that don't exist live
        for line in content.split("\n"):
            if "|" in line and "USDT" in line:
                parts = [p.strip() for p in line.split("|")]
                for part in parts:
                    if "USDT" in part and "/" in part:
                        # This looks like a symbol
                        sym = part.strip()
                        live_syms = {p["symbol"] for p in positions}
                        if sym and sym not in live_syms and sym not in ["/USDT:USDT"]:
                            stale_symbols.append(sym)

    # Build new progress.md
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# TRAPPIST Portfolio State",
        f"",
        f"> Last reconciled: {now}",
        f"> Mode: {'TESTNET' if is_sandbox() else 'LIVE'}",
        f"",
        f"## Account",
        f"- Equity: {equity:,.2f} USDT",
        f"- Free: {account['free']:,.2f} USDT",
        f"- Exposure: {exposure:,.2f} USDT ({exposure_pct:.1f}%)",
        f"- Positions: {len(positions)}",
        f"- Open orders: {len(orders)}",
        f"",
    ]

    if positions:
        lines.extend([
            f"## Open Positions",
            f"| Symbol | Side | Size | Entry | Mark | PnL | PnL% | Leverage |",
            f"|--------|------|------|-------|------|-----|------|----------|",
        ])
        for p in positions:
            pnl_sign = "+" if p["unrealized_pnl"] >= 0 else ""
            lines.append(
                f"| {p['symbol']} | {p['side']} | {p['contracts']} | "
                f"{p['entry_price']:,.2f} | {p['mark_price']:,.2f} | "
                f"{pnl_sign}{p['unrealized_pnl']:,.4f} | {pnl_sign}{p['pnl_pct']:.2f}% | "
                f"{p['leverage']}x |"
            )
        lines.append("")

    if orders:
        lines.extend([
            f"## Open Orders",
            f"| Symbol | Side | Type | Amount | Price | Stop | Status |",
            f"|--------|------|------|--------|-------|------|--------|",
        ])
        for o in orders:
            lines.append(
                f"| {o['symbol']} | {o['side']} | {o['type']} | "
                f"{o['amount']} | {o['price']} | {o['stop_price']} | {o['status']} |"
            )
        lines.append("")

    progress_path.write_text("\n".join(lines))

    result = {
        "status": "reconciled",
        "equity": equity,
        "exposure_pct": exposure_pct,
        "positions": len(positions),
        "open_orders": len(orders),
        "stale_symbols_removed": stale_symbols,
        "mode": "TESTNET" if is_sandbox() else "LIVE",
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_check_protection(args):
    """Verify all positions have SL/TP orders (protection check)."""
    positions = get_positions()
    if not positions:
        result = {"status": "no_positions", "protected": 0, "unprotected": []}
        print(json.dumps(result, indent=2))
        return 0

    all_orders = get_open_orders()

    protected = []
    unprotected = []

    for pos in positions:
        sym = pos["symbol"]
        side = pos["side"]
        has_sl = False
        has_tp = False

        for order in all_orders:
            if order["symbol"] != sym or not order.get("reduce_only", False):
                continue
            order_type = order["type"].upper()
            if "STOP" in order_type:
                has_sl = True
            if "TAKE_PROFIT" in order_type or "PROFIT" in order_type:
                has_tp = True

        if has_sl and has_tp:
            protected.append(sym)
        else:
            unprotected.append({
                "symbol": sym,
                "side": side,
                "contracts": pos["contracts"],
                "entry_price": pos["entry_price"],
                "mark_price": pos["mark_price"],
                "unrealized_pnl": pos["unrealized_pnl"],
                "has_sl": has_sl,
                "has_tp": has_tp,
                "required_close_side": "sell" if side == "long" else "buy",
            })

    status = "all_protected" if not unprotected else "UNPROTECTED_POSITIONS"
    result = {
        "status": status,
        "protected_count": len(protected),
        "protected": protected,
        "unprotected_count": len(unprotected),
        "unprotected": unprotected,
    }
    print(json.dumps(result, indent=2))
    return 1 if unprotected else 0


def cmd_time_stops(args):
    """Check positions held longer than max-days (time stop enforcement)."""
    positions = get_positions()
    if not positions:
        print(json.dumps({"status": "no_positions", "expired": [], "active": []}, indent=2))
        return 0

    max_days = args.max_days
    now = datetime.now(timezone.utc)
    expired = []
    active = []

    for pos in positions:
        ts = pos.get("timestamp")
        if not ts:
            # Can't determine age — flag for review
            expired.append({**pos, "days_held": "unknown", "reason": "no timestamp — flag for review"})
            continue

        try:
            opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            days_held = (now - opened).days
        except Exception:
            expired.append({**pos, "days_held": "parse_error", "reason": "timestamp parse failed"})
            continue

        entry = {**pos, "days_held": days_held}
        if days_held > max_days:
            entry["reason"] = f"held {days_held} days > max {max_days}"
            expired.append(entry)
        else:
            active.append(entry)

    status = "EXPIRED_POSITIONS" if expired else "all_within_limits"
    print(json.dumps({"status": status, "max_days": max_days, "expired": expired, "active": active}, indent=2))
    return 1 if expired else 0


def cmd_trail_stops(args):
    """Adjust stop-losses for profitable positions (trailing stop)."""
    positions = get_positions()
    orders = get_open_orders()
    adjustments = []

    breakeven_pct = args.breakeven_pct
    trail_pct = args.trail_pct

    for pos in positions:
        sym = pos["symbol"]
        side = pos["side"]
        entry = pos["entry_price"]
        current = pos["mark_price"]
        pnl_pct = pos["pnl_pct"]
        contracts = pos["contracts"]

        # Find current SL order
        current_sl = None
        sl_order_id = None
        for o in orders:
            if o["symbol"] == sym and o.get("reduce_only") and "STOP" in o["type"].upper():
                current_sl = o["stop_price"]
                sl_order_id = o["id"]
                break

        if not current_sl:
            adjustments.append({
                "symbol": sym, "action": "SKIP",
                "reason": "no existing SL order found",
            })
            continue

        new_sl = None
        reason = ""

        if side == "long":
            if pnl_pct >= breakeven_pct + trail_pct:
                # Trail: SL at current - trail_pct%
                new_sl = round(current * (1 - trail_pct / 100), 2)
                reason = f"trailing ({pnl_pct:.1f}% profit)"
            elif pnl_pct >= breakeven_pct:
                # Move SL to breakeven
                new_sl = entry
                reason = f"breakeven ({pnl_pct:.1f}% profit)"

            # Only move SL up, never down
            if new_sl and new_sl <= current_sl:
                new_sl = None
                reason = "new SL would be lower than current"
        else:  # short
            if pnl_pct >= breakeven_pct + trail_pct:
                new_sl = round(current * (1 + trail_pct / 100), 2)
                reason = f"trailing ({pnl_pct:.1f}% profit)"
            elif pnl_pct >= breakeven_pct:
                new_sl = entry
                reason = f"breakeven ({pnl_pct:.1f}% profit)"

            # Only move SL down (tighter) for shorts
            if new_sl and new_sl >= current_sl:
                new_sl = None
                reason = "new SL would be higher than current"

        if new_sl and not args.dry_run:
            # Cancel old SL and place new one
            close_side = "sell" if side == "long" else "buy"
            cancel_ok = cancel_order(sl_order_id, sym)
            if cancel_ok:
                sl_result = place_stop_order(sym, contracts, new_sl, side=close_side)
                action = "ADJUSTED" if sl_result.success else "FAILED"
            else:
                action = "CANCEL_FAILED"
        elif new_sl:
            action = "WOULD_ADJUST (dry-run)"
        else:
            action = "NO_CHANGE"

        adjustments.append({
            "symbol": sym,
            "side": side,
            "entry": entry,
            "current": current,
            "pnl_pct": pnl_pct,
            "old_sl": current_sl,
            "new_sl": new_sl,
            "action": action,
            "reason": reason,
        })

    print(json.dumps({"adjustments": adjustments, "dry_run": args.dry_run}, indent=2))
    return 0


def cmd_category(args):
    """Look up crypto categories for symbols."""
    results = {}
    for sym in args.symbols:
        base = normalize_symbol(sym)
        results[base] = get_category(sym)
    print(json.dumps(results, indent=2))
    return 0


# ---------------------------------------------------------------------------
# History commands
# ---------------------------------------------------------------------------

def cmd_closed_orders(args):
    """Show closed/filled orders."""
    symbol = format_symbol(args.symbol) if args.symbol else None
    orders = get_closed_orders(symbol, days=args.days)
    print(json.dumps({"count": len(orders), "orders": orders}, indent=2))
    return 0


def cmd_trades(args):
    """Show trade history (fills) for a symbol."""
    symbol = format_symbol(args.symbol)
    trades = get_trades(symbol, days=args.days)
    print(json.dumps({"count": len(trades), "trades": trades}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TRAPPIST — Crypto Futures Trade Executor CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- Data queries ---
    sub.add_parser("account", help="Account balance and equity")
    sub.add_parser("positions", help="Open positions with PnL")

    p = sub.add_parser("orders", help="Open orders")
    p.add_argument("symbol", nargs="?", default=None, help="Filter by symbol")

    p = sub.add_parser("quote", help="Current ticker + funding rate")
    p.add_argument("symbols", nargs="+", help="Symbols (BTC, ETH, BTC/USDT:USDT)")

    p = sub.add_parser("bars", help="OHLCV candle data")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("--timeframe", "-t", default="4h",
                   choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
                   help="Timeframe (default: 4h)")
    p.add_argument("--limit", "-n", type=int, default=500, help="Number of candles")
    p.add_argument("--last", type=int, default=0, help="Show only last N bars")

    p = sub.add_parser("asset", help="Market info (precision, limits, fees)")
    p.add_argument("symbol", help="Symbol")

    p = sub.add_parser("funding", help="Current funding rate")
    p.add_argument("symbols", nargs="+", help="Symbols")

    sub.add_parser("status", help="Full dashboard (account + positions + orders)")

    p = sub.add_parser("analyze", help="Dual technical analysis (long + short scores)")
    p.add_argument("symbols", nargs="+", help="Symbols to analyze")
    p.add_argument("--timeframe", "-t", default="4h", help="Timeframe for analysis")
    p.add_argument("--json", action="store_true", help="JSON output")

    # --- Order placement ---
    p = sub.add_parser("bracket", help="Bracket order (entry + SL + TP)")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("qty", type=float, help="Position size in base currency")
    p.add_argument("tp", type=float, help="Take-profit price")
    p.add_argument("sl", type=float, help="Stop-loss price")
    p.add_argument("--side", default="buy", choices=["buy", "sell"],
                   help="buy=LONG, sell=SHORT (default: buy)")
    p.add_argument("--limit", type=float, default=None, help="Limit entry price (None=market)")
    p.add_argument("--leverage", type=int, default=5, help="Leverage (default: 5)")
    p.add_argument("--min-rr", type=float, default=1.5, help="Minimum R/R ratio (default: 1.5)")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip R/R validation (NOT recommended)")

    p = sub.add_parser("close", help="Close a position")
    p.add_argument("symbol", help="Symbol")

    p = sub.add_parser("cancel", help="Cancel an order")
    p.add_argument("order_id", help="Order ID")
    p.add_argument("symbol", help="Symbol")

    p = sub.add_parser("cancel-all", help="Cancel all orders for a symbol")
    p.add_argument("symbol", help="Symbol")

    p = sub.add_parser("set-leverage", help="Set leverage")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("leverage", type=int, help="Leverage multiplier")

    p = sub.add_parser("set-margin", help="Set margin mode")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("mode", choices=["isolated", "cross"], help="Margin mode")

    # --- Portfolio management ---
    sub.add_parser("reconcile", help="Sync progress.md with live state (MANDATORY before trading)")

    sub.add_parser("check-protection", help="Verify all positions have SL/TP orders")

    p = sub.add_parser("validate-rr", help="Validate R/R with live prices")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("entry", type=float, help="Entry price")
    p.add_argument("tp", type=float, help="Take-profit price")
    p.add_argument("sl", type=float, help="Stop-loss price")
    p.add_argument("--side", default="buy", choices=["buy", "sell"])
    p.add_argument("--min-rr", type=float, default=1.5)

    p = sub.add_parser("time-stops", help="Check positions exceeding max hold time")
    p.add_argument("--max-days", type=int, default=10, help="Max days to hold (default: 10)")

    p = sub.add_parser("trail-stops", help="Adjust trailing stops for profitable positions")
    p.add_argument("--breakeven-pct", type=float, default=3.0, help="Move SL to breakeven at this PnL%")
    p.add_argument("--trail-pct", type=float, default=2.0, help="Trail distance in %%")
    p.add_argument("--dry-run", action="store_true", help="Show adjustments without executing")

    p = sub.add_parser("category", help="Look up crypto categories")
    p.add_argument("symbols", nargs="+", help="Base symbols (BTC, ETH, SOL)")

    # --- History ---
    p = sub.add_parser("closed-orders", help="Historical closed orders")
    p.add_argument("--symbol", default=None, help="Filter by symbol")
    p.add_argument("--days", type=int, default=30, help="Lookback days")

    p = sub.add_parser("trades", help="Trade history (fills)")
    p.add_argument("symbol", help="Symbol")
    p.add_argument("--days", type=int, default=30, help="Lookback days")

    return parser


COMMAND_MAP = {
    "account": cmd_account,
    "positions": cmd_positions,
    "orders": cmd_orders,
    "quote": cmd_quote,
    "bars": cmd_bars,
    "asset": cmd_asset,
    "funding": cmd_funding,
    "status": cmd_status,
    "analyze": cmd_analyze,
    "bracket": cmd_bracket,
    "close": cmd_close,
    "cancel": cmd_cancel,
    "cancel-all": cmd_cancel_all,
    "set-leverage": cmd_set_leverage,
    "set-margin": cmd_set_margin,
    "reconcile": cmd_reconcile,
    "check-protection": cmd_check_protection,
    "validate-rr": cmd_validate_rr,
    "time-stops": cmd_time_stops,
    "trail-stops": cmd_trail_stops,
    "category": cmd_category,
    "closed-orders": cmd_closed_orders,
    "trades": cmd_trades,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
