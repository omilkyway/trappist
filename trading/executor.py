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
from trading.indicators import compute_signals, compute_multi_timeframe, chandelier_exit


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

    # Auto-init balance on first run
    initial = state.get("initial_balance", 0)
    equity = account["equity"]
    if initial == 0 and equity > 0:
        state["initial_balance"] = equity
        initial = equity
        try:
            with open(state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    # Drawdown calculation
    drawdown_pct = round((equity - initial) / initial * 100, 2) if initial > 0 else 0

    # Trade stats from history
    trades = state.get("trades", [])
    closed = state.get("closed_trades", [])
    recent_entries = trades[-20:] if trades else []

    # Win/loss from closed trades
    wins = [t for t in closed if t.get("pnl_pct", 0) > 0]
    losses = [t for t in closed if t.get("pnl_pct", 0) < 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    total_realized_pnl = round(sum(t.get("unrealized_pnl", 0) for t in closed), 2)
    avg_win = round(sum(t.get("pnl_pct", 0) for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t.get("pnl_pct", 0) for t in losses) / len(losses), 2) if losses else 0

    output = {
        "mode": "TESTNET" if is_sandbox() else "LIVE",
        "equity": equity,
        "free": account["free"],
        "exposure_pct": account["exposure_pct"],
        "unrealized_pnl": account["unrealized_pnl"],
        "drawdown_pct": drawdown_pct,
        "positions": positions,
        "open_orders": len(orders),
        "unprotected": unprotected,
        "killed": state.get("killed", False),
        "initial_balance": initial,
        "total_entries": len(trades),
        "total_closed": len(closed),
        "win_rate": win_rate,
        "total_realized_pnl": total_realized_pnl,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "recent_symbols": list({t.get("symbol", "") for t in recent_entries}),
        "cooldowns": _get_active_cooldowns(state),
    }
    print(json.dumps(output, indent=2))
    return 0


def _get_active_cooldowns(state: dict) -> dict:
    """Return symbols still in cooldown with minutes remaining."""
    cooldowns = state.get("cooldowns", {})
    now = datetime.now(timezone.utc)
    active = {}
    for base, ts in cooldowns.items():
        try:
            last_dt = datetime.fromisoformat(ts)
            minutes_since = (now - last_dt).total_seconds() / 60
            if minutes_since < 60:
                active[base] = round(60 - minutes_since)
        except Exception:
            pass
    return active


# ---------------------------------------------------------------------------
# scan — Technical analysis + funding on all pairs
# ---------------------------------------------------------------------------

def cmd_scan(args):
    """Dual technical analysis + funding rate on all active pairs.

    Two-pass approach:
    1. Quick pre-filter: fetch ticker for all pairs, keep only movers (>1% change or high volume)
    2. Deep analysis: full TA + funding only on filtered pairs

    Returns top candidates sorted by best score, with position sizing hints.
    """
    if args.pairs:
        pairs = [format_symbol(p.strip()) for p in args.pairs.split(",")]
    else:
        pairs = get_active_pairs()

    print(f"[scan] Universe: {len(pairs)} pairs", flush=True)

    # Pass 1: Quick pre-filter — only analyze pairs worth looking at
    # With dynamic discovery (80+ pairs), we need to be selective
    candidates = []
    core = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    for sym in pairs:
        try:
            t = get_ticker(sym)
            change = abs(t.get("change_pct", 0))
            vol = t.get("volume_24h", 0)
            spread_pct = 0
            bid, ask = t.get("bid", 0), t.get("ask", 0)
            if bid and ask and bid > 0:
                spread_pct = (ask - bid) / bid * 100

            # Keep: big movers, high volume, or core pairs
            # Skip: wide spreads (> 0.15%) = illiquid = dangerous with leverage
            if spread_pct > 0.15 and sym not in core:
                continue
            if change > 1.5 or vol > 50_000_000 or sym in core:
                candidates.append((sym, t))
        except Exception:
            if sym in core:
                candidates.append((sym, {}))

    # Cap candidates to avoid excessive API calls (top 25 by volume)
    candidates.sort(key=lambda x: x[1].get("volume_24h", 0), reverse=True)
    max_deep = getattr(args, "max_candidates", 25)
    # Always keep core pairs + top by volume
    core_candidates = [(s, t) for s, t in candidates if s in core]
    other_candidates = [(s, t) for s, t in candidates if s not in core]
    candidates = core_candidates + other_candidates[:max_deep - len(core_candidates)]
    print(f"[scan] Pass 1: {len(candidates)} candidates after pre-filter", flush=True)

    # Pass 2: Deep analysis on filtered candidates
    # Also read account for position sizing
    try:
        account = get_account()
        equity = account["equity"]
    except Exception:
        equity = 0

    results = {}
    for sym, ticker_data in candidates:
        try:
            df = get_bars(sym, timeframe=args.timeframe, limit=500)
            if len(df) < 50:
                results[sym] = {"error": f"Only {len(df)} bars (need 50+)"}
                continue

            fr = get_funding_rate(sym)
            funding_pct = fr.get("funding_rate_pct", 0)

            signals = compute_signals(df, funding_rate=funding_pct)
            signals["funding_rate_pct"] = funding_pct
            signals["category"] = get_category(sym)

            # Inject ticker data
            signals["bid"] = ticker_data.get("bid", 0)
            signals["ask"] = ticker_data.get("ask", 0)
            signals["price"] = ticker_data.get("last", signals.get("price", 0))
            signals["change_24h"] = ticker_data.get("change_pct", 0)
            signals["volume_24h"] = ticker_data.get("volume_24h", 0)

            # Position sizing — Half-Kelly: 2% risk per trade
            sl_tp = signals.get("suggested_sl_tp", {})
            price = signals["price"]
            if equity > 0 and price > 0 and sl_tp:
                risk_amount = equity * 0.02  # 2% risk per trade (Half-Kelly)
                for direction in ("long", "short"):
                    levels = sl_tp.get(direction, {})
                    sl_dist = abs(price - levels.get("sl", price))
                    if sl_dist > 0:
                        suggested_qty = round(risk_amount / sl_dist, 6)
                        max_qty = round(equity * 0.12 / price, 6)  # 12% max notional
                        levels["suggested_qty"] = min(suggested_qty, max_qty)
                        levels["notional"] = round(levels["suggested_qty"] * price, 2)

            # Best direction score for ranking
            ls = signals["signals"]["long_score"]
            ss = signals["signals"]["short_score"]
            signals["best_score"] = max(ls, ss)
            signals["best_direction"] = "LONG" if ls > ss else "SHORT"

            # Multi-timeframe confirmation for promising pairs
            if max(ls, ss) > 50:
                try:
                    timeframes = {args.timeframe: df}
                    if args.timeframe != "1h":
                        df_1h = get_bars(sym, timeframe="1h", limit=200)
                        if len(df_1h) >= 50:
                            timeframes["1h"] = df_1h
                    if args.timeframe != "1d":
                        df_1d = get_bars(sym, timeframe="1d", limit=200)
                        if len(df_1d) >= 50:
                            timeframes["1d"] = df_1d

                    if len(timeframes) > 1:
                        mtf = compute_multi_timeframe(timeframes, funding_rate=funding_pct)
                        signals["multi_tf"] = mtf
                        # Override ranking with multi-TF combined score
                        signals["best_score"] = max(mtf["combined_long_score"], mtf["combined_short_score"])
                        signals["best_direction"] = "LONG" if mtf["combined_long_score"] > mtf["combined_short_score"] else "SHORT"
                except Exception:
                    pass  # Fall back to single TF score

            results[sym] = signals
        except Exception as e:
            results[sym] = {"error": str(e)}

    # Sort by best score descending
    sorted_results = dict(
        sorted(results.items(),
               key=lambda x: x[1].get("best_score", 0) if "error" not in x[1] else 0,
               reverse=True)
    )

    print(json.dumps(sorted_results, indent=2))
    return 0


# ---------------------------------------------------------------------------
# bracket — One command, auto-everything
# ---------------------------------------------------------------------------

def cmd_bracket(args):
    """Place bracket order with auto leverage + margin + R/R validation.

    Hard limits (code-enforced, cannot be overridden):
    - Max leverage: 20x
    - Max 8 concurrent positions
    - Max 75% gross exposure
    - Category concentration: 3 per category
    """
    symbol = format_symbol(args.symbol)
    side = args.side.lower()

    # Hard limit: leverage cap
    if args.leverage > 20:
        print(json.dumps({"success": False, "error": f"Leverage {args.leverage}x > max 20x"}, indent=2))
        return 1

    # Cooldown check — prevent over-trading same symbol
    state_path = Path("state.json")
    try:
        _state = json.load(open(state_path)) if state_path.exists() else {}
    except Exception:
        _state = {}
    cooldowns = _state.get("cooldowns", {})
    base = symbol.split("/")[0]
    last_ts = cooldowns.get(base)
    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            minutes_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            cooldown_minutes = 60
            if minutes_since < cooldown_minutes:
                remaining = round(cooldown_minutes - minutes_since)
                print(json.dumps({
                    "success": False,
                    "error": f"COOLDOWN: {base} was traded {int(minutes_since)}m ago. Wait {remaining}m more.",
                }, indent=2))
                return 1
        except Exception:
            pass

    # Hard limits: positions count + exposure
    try:
        positions = get_positions()
        orders = get_open_orders()
        account = get_account()

        # Max 8 concurrent positions
        if len(positions) >= 8:
            print(json.dumps({"success": False, "error": f"Max 8 positions ({len(positions)} open)"}, indent=2))
            return 1

        # Max 75% gross exposure
        if account["exposure_pct"] > 75:
            print(json.dumps({
                "success": False,
                "error": f"Exposure {account['exposure_pct']:.1f}% > max 75%",
            }, indent=2))
            return 1

        # Category check
        allowed, reason = check_category_limit(symbol, positions, orders)
        if not allowed:
            print(json.dumps({"success": False, "error": reason}, indent=2))
            return 1
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Pre-trade checks failed: {e}"}, indent=2))
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

    # Persist trade to state.json for learning loop
    if result.success:
        _log_trade(result, symbol, side, args)

    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def _log_trade(result, symbol: str, side: str, args):
    """Persist trade to state.json for continuous improvement."""
    state_path = Path("state.json")
    try:
        state = json.load(open(state_path)) if state_path.exists() else {}
    except Exception:
        state = {}

    # Init balance on first trade
    if state.get("initial_balance", 0) == 0:
        try:
            account = get_account()
            state["initial_balance"] = account["equity"]
        except Exception:
            pass

    state.setdefault("trades", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "order_id": result.order_id,
        "symbol": symbol,
        "side": side,
        "qty": args.qty,
        "entry": args.limit or result.details.get("entry_price"),
        "sl": args.sl,
        "tp": args.tp,
        "rr": round(
            abs(args.tp - (args.limit or result.details.get("entry_price", 0)))
            / abs((args.limit or result.details.get("entry_price", 0)) - args.sl), 2
        ) if args.sl != (args.limit or result.details.get("entry_price", 0)) else 0,
        "leverage": args.leverage,
        "sl_order_id": result.details.get("sl_order_id"),
        "tp_order_id": result.details.get("tp_order_id"),
        "protection_errors": result.details.get("protection_errors"),
    })

    # Keep last 200 trades
    state["trades"] = state["trades"][-200:]
    state["killed"] = state.get("killed", False)

    # Update cooldown for this symbol
    base = symbol.split("/")[0]
    state.setdefault("cooldowns", {})[base] = datetime.now(timezone.utc).isoformat()

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# close — Close position + cancel related orders
# ---------------------------------------------------------------------------

def cmd_close(args):
    """Close a position and cancel all related orders."""
    symbol = format_symbol(args.symbol)

    # Capture position before closing for P&L logging
    pre_close_pnl = None
    try:
        positions = get_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if pos:
            pre_close_pnl = {
                "symbol": symbol,
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "close_price": pos["mark_price"],
                "pnl_pct": pos["pnl_pct"],
                "unrealized_pnl": pos["unrealized_pnl"],
            }
    except Exception:
        pass

    # Cancel all orders for this symbol first
    try:
        cancel_all_orders(symbol)
    except Exception:
        pass

    result = close_position(symbol)

    # Log the close with realized P&L
    if result.success and pre_close_pnl:
        _log_close(pre_close_pnl)

    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def _log_close(pnl_data: dict):
    """Log position close with P&L to state.json for learning."""
    state_path = Path("state.json")
    try:
        state = json.load(open(state_path)) if state_path.exists() else {}
    except Exception:
        state = {}

    state.setdefault("closed_trades", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        **pnl_data,
    })
    state["closed_trades"] = state["closed_trades"][-200:]

    # Update cooldown for this symbol
    base = pnl_data.get("symbol", "").split("/")[0]
    if base:
        state.setdefault("cooldowns", {})[base] = datetime.now(timezone.utc).isoformat()

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


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

        # Fix missing protection — leverage-aware SL/TP
        # At 10x, -10% = -100% margin = liquidation. Cap margin loss at 50%.
        leverage = pos.get("leverage", 1) or 1
        max_loss_pct = min(0.10, 0.50 / max(leverage, 1))
        tp_gain_pct = min(0.15, 0.80 / max(leverage, 1))

        if not has_sl:
            sl_price = round(entry * (1 - max_loss_pct) if side == "long" else entry * (1 + max_loss_pct), 2)
            result = place_stop_order(sym, contracts, sl_price, side=close_side)
            actions.append({"symbol": sym, "action": "EMERGENCY_SL", "price": sl_price,
                           "leverage": leverage, "loss_pct": round(max_loss_pct * 100, 1),
                           "success": result.success, "error": result.error})

        if not has_tp:
            tp_price = round(entry * (1 + tp_gain_pct) if side == "long" else entry * (1 - tp_gain_pct), 2)
            result = place_tp_order(sym, contracts, tp_price, side=close_side)
            actions.append({"symbol": sym, "action": "EMERGENCY_TP", "price": tp_price,
                           "leverage": leverage, "gain_pct": round(tp_gain_pct * 100, 1),
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

        # Trail stops — Chandelier Exit (ATR-based, volatility-adaptive)
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

                if pnl_pct >= 3.0:
                    # Try Chandelier Exit (ATR-based trail)
                    try:
                        df = get_bars(sym, timeframe="4h", limit=100)
                        if len(df) >= 50:
                            ce = chandelier_exit(df["high"], df["low"], df["close"],
                                                 lookback=22, multiplier=3.0)
                            ce_last = ce.iloc[-1]
                            if side == "long":
                                chandelier_sl = round(float(ce_last["long_exit"]), 2)
                                if chandelier_sl > current_sl and chandelier_sl < current:
                                    new_sl = chandelier_sl
                            else:
                                chandelier_sl = round(float(ce_last["short_exit"]), 2)
                                if chandelier_sl < current_sl and chandelier_sl > current:
                                    new_sl = chandelier_sl
                    except Exception:
                        pass

                    # Fallback: breakeven at +3% if Chandelier didn't trigger
                    if new_sl is None and pnl_pct >= 3.0:
                        be_sl = entry
                        if side == "long" and be_sl > current_sl:
                            new_sl = be_sl
                        elif side == "short" and be_sl < current_sl:
                            new_sl = be_sl

                if new_sl and not args.dry_run:
                    cancel_order(sl_order["id"], sym)
                    result = place_stop_order(sym, contracts, new_sl, side=close_side)
                    actions.append({"symbol": sym, "action": "CHANDELIER_TRAIL",
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
    b.add_argument("--leverage", type=int, default=10, help="Leverage (default: 10, max 20)")
    b.add_argument("--min-rr", type=float, default=1.5, help="Min R/R ratio (default: 1.5, aim for 2.0+)")
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
