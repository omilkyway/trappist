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
    get_open_interest,
    get_long_short_ratio,
    get_positions,
    get_recent_liquidations,
    get_ticker,
    get_trades,
    is_sandbox,
    place_bracket_order,
    place_stop_order,
    place_tp_order,
    cancel_order,
)
from trading.categories import check_category_limit, get_category
from trading.indicators import (
    compute_signals,
    compute_multi_timeframe,
    chandelier_exit,
    kelly_risk_pct,
    open_interest_signal,
    time_of_day_adjustment,
    dynamic_score_threshold,
    liquidation_signal,
    suggest_limit_entry,
)


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

    # Reconcile: detect trades closed by SL/TP on exchange
    _reconcile_stale_trades(state, positions, state_path)

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

    # Equity curve snapshot (every status call = every cycle)
    state.setdefault("equity_curve", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "equity": equity,
        "exposure_pct": account["exposure_pct"],
        "positions_count": len(positions),
        "unrealized_pnl": account["unrealized_pnl"],
        "event": "status_check",
    })
    state["equity_curve"] = state["equity_curve"][-2000:]
    try:
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

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


def _reconcile_stale_trades(state: dict, live_positions: list[dict], state_path: Path):
    """Detect trades in state.json that were closed by SL/TP on the exchange.

    Compares state trades with live Binance positions. Any tracked trade
    whose symbol has no matching live position is assumed closed.
    Uses Binance trade history (get_trades) to find the ACTUAL close price
    instead of guessing SL hit. Falls back to SL estimate if history unavailable.
    """
    tracked = state.get("trades", [])
    if not tracked:
        return

    live_symbols = {p["symbol"] for p in live_positions}
    still_open = []
    reconciled = 0

    for trade in tracked:
        sym = trade.get("symbol", "")
        if sym in live_symbols:
            still_open.append(trade)
        else:
            entry = trade.get("entry") or 0
            sl = trade.get("sl") or 0
            tp = trade.get("tp") or 0
            side = trade.get("side", "buy")
            leverage = trade.get("leverage", 1) or 1
            qty = trade.get("qty", 0) or 0

            # Try to get ACTUAL close price from Binance trade history
            close_price = None
            close_reason = "sl_tp_triggered"
            try:
                recent_trades = get_trades(sym, days=7, limit=50)
                # Look for reduce-only trades (SL/TP fills) matching our qty
                close_side = "sell" if side == "buy" else "buy"
                fills = [t for t in recent_trades if t["side"] == close_side]
                if fills:
                    # Use the most recent fill as close price
                    last_fill = fills[-1]
                    close_price = last_fill["price"]
                    # Determine if it was SL or TP based on price
                    if side == "buy":
                        close_reason = "tp_hit" if close_price >= tp * 0.98 else "sl_hit"
                    else:
                        close_reason = "tp_hit" if close_price <= tp * 1.02 else "sl_hit"
            except Exception:
                pass

            # Fallback: estimate from SL (conservative)
            if close_price is None:
                close_price = sl
                close_reason = "sl_assumed"

            if side == "buy":
                pnl_pct = round((close_price - entry) / entry * 100, 2) if entry else 0
                pnl_usd = round((close_price - entry) * qty, 4) if entry else 0
            else:
                pnl_pct = round((entry - close_price) / entry * 100, 2) if entry else 0
                pnl_usd = round((entry - close_price) * qty, 4) if entry else 0

            # Signal attribution: tag which entry signals were correct
            entry_signals = trade.get("signals")  # logged at entry time
            signal_outcome = None
            if entry_signals and pnl_pct != 0:
                was_profitable = pnl_pct > 0
                signal_outcome = {
                    "entry_signals": entry_signals,
                    "profitable": was_profitable,
                    "pnl_pct": pnl_pct,
                }

            state.setdefault("closed_trades", []).append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": sym,
                "side": "long" if side == "buy" else "short",
                "entry_price": entry,
                "close_price": close_price,
                "pnl_pct": pnl_pct,
                "unrealized_pnl": pnl_usd,
                "close_reason": close_reason,
                "leverage": leverage,
                "qty": qty,
                "signal_outcome": signal_outcome,
            })
            reconciled += 1

    if reconciled > 0:
        state["trades"] = still_open
        state["closed_trades"] = state.get("closed_trades", [])[-200:]
        try:
            with open(state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass
        print(f"[reconcile] Moved {reconciled} stale trades to closed_trades (actual prices when available)")


def _get_active_cooldowns(state: dict) -> dict:
    """Return symbols still in cooldown with minutes remaining."""
    cooldowns = state.get("cooldowns", {})
    now = datetime.now(timezone.utc)
    active = {}
    for base, ts in cooldowns.items():
        try:
            last_dt = datetime.fromisoformat(ts)
            minutes_since = (now - last_dt).total_seconds() / 60
            if minutes_since < 30:
                active[base] = round(30 - minutes_since)
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

    # Pre-compute dynamic threshold from recent trades (once for all pairs)
    _state_path = Path("state.json")
    try:
        _scan_state = json.load(open(_state_path)) if _state_path.exists() else {}
    except Exception:
        _scan_state = {}
    _scan_dynamic_threshold = dynamic_score_threshold(_scan_state.get("closed_trades", []))

    print(f"[scan] Universe: {len(pairs)} pairs | Threshold: {_scan_dynamic_threshold['reason']}", flush=True)

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

            # Position sizing — Kelly criterion per category (Half-Kelly)
            # AI category gets 8% risk, others 2-5% based on historical edge
            sl_tp = signals.get("suggested_sl_tp", {})
            price = signals["price"]
            category = signals.get("category", "Other")
            risk_pct = kelly_risk_pct(category)
            signals["kelly_risk_pct"] = round(risk_pct * 100, 1)

            if equity > 0 and price > 0 and sl_tp:
                risk_amount = equity * risk_pct
                for direction in ("long", "short"):
                    levels = sl_tp.get(direction, {})
                    sl_dist = abs(price - levels.get("sl", price))
                    if sl_dist > 0:
                        suggested_qty = round(risk_amount / sl_dist, 6)
                        max_qty = round(equity * 0.20 / price, 6)  # 20% max notional
                        levels["suggested_qty"] = min(suggested_qty, max_qty)
                        levels["notional"] = round(levels["suggested_qty"] * price, 2)

            # Open Interest + Long/Short ratio — contrarian signals
            try:
                oi = get_open_interest(sym)
                lsr = get_long_short_ratio(sym)
                signals["open_interest"] = oi.get("open_interest_value", 0)
                signals["long_short_ratio"] = lsr.get("long_short_ratio", 1.0)

                # Contrarian: extreme long/short ratio = signal
                ratio = lsr.get("long_short_ratio", 1.0)
                if ratio > 2.0:
                    # Too many longs → short bias
                    signals["signals"]["short_score"] = min(100, signals["signals"]["short_score"] + 3)
                    signals["signals"]["short_raw"] = signals["signals"].get("short_raw", 0) + 2
                elif ratio < 0.5:
                    # Too many shorts → long bias (short squeeze potential)
                    signals["signals"]["long_score"] = min(100, signals["signals"]["long_score"] + 3)
                    signals["signals"]["long_raw"] = signals["signals"].get("long_raw", 0) + 2
            except Exception:
                pass

            # Best direction score for ranking
            # FIX SHORT BIAS: strict score comparison — no long preference
            ls = signals["signals"]["long_score"]
            ss = signals["signals"]["short_score"]
            signals["best_score"] = max(ls, ss)
            # SHORT wins when short_score > long_score (no tie-breaking to long)
            if ss > ls:
                signals["best_direction"] = "SHORT"
            elif ls > ss:
                signals["best_direction"] = "LONG"
            else:
                # Tie: use funding rate edge to break
                funding_edge = signals.get("signals", {}).get("funding_rate", {})
                if isinstance(funding_edge, dict) and funding_edge.get("funding_edge") == "SHORT_PAID":
                    signals["best_direction"] = "SHORT"
                else:
                    signals["best_direction"] = "LONG"

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

            # --- LIQUIDATION SIGNAL ---
            try:
                liqs = get_recent_liquidations(sym, limit=20)
                liq_sig = liquidation_signal(liqs)
                signals["liquidations"] = liq_sig
                if liq_sig["long_score"]:
                    signals["signals"]["long_score"] = min(100, signals["signals"]["long_score"] + liq_sig["long_score"])
                if liq_sig["short_score"]:
                    signals["signals"]["short_score"] = min(100, signals["signals"]["short_score"] + liq_sig["short_score"])
            except Exception:
                pass

            # --- TIME-OF-DAY ADJUSTMENT ---
            tod = time_of_day_adjustment()
            signals["time_of_day"] = tod

            # --- DYNAMIC SCORE THRESHOLD (based on recent performance) ---
            dyn_thresh = _scan_dynamic_threshold
            signals["dynamic_threshold"] = dyn_thresh

            # --- SUGGESTED LIMIT ENTRY (better fills) ---
            dir_key = "long" if signals.get("best_direction") == "LONG" else "short"
            try:
                limit_entry = suggest_limit_entry(
                    price=signals["price"],
                    atr_last=signals["indicators"].get("atr14") or 0,
                    vwap_last=signals["indicators"].get("vwap"),
                    support_1=signals.get("levels", {}).get("support_1"),
                    resistance_1=signals.get("levels", {}).get("resistance_1"),
                    direction=signals.get("best_direction", "LONG"),
                )
                if limit_entry:
                    sl_tp_dir = sl_tp.get(dir_key, {})
                    if sl_tp_dir:
                        sl_tp_dir["suggested_limit_entry"] = limit_entry
            except Exception:
                pass

            # --- ACTION FLAG: explicit TRADE or SKIP ---
            # Apply time-of-day score adjustment
            best = signals["best_score"] + tod["score_adj"]
            direction = signals["best_direction"]
            has_sl_tp = bool(sl_tp.get(dir_key, {}).get("sl"))
            rr = sl_tp.get(dir_key, {}).get("rr", 0)

            # Apply time-of-day size multiplier to suggested_qty
            if tod["size_mult"] != 1.0:
                for d in ("long", "short"):
                    levels = sl_tp.get(d, {})
                    if "suggested_qty" in levels:
                        levels["suggested_qty"] = round(levels["suggested_qty"] * tod["size_mult"], 6)
                        levels["notional"] = round(levels["suggested_qty"] * signals["price"], 2)

            # Use dynamic threshold instead of fixed 55/65
            trade_thresh = dyn_thresh["threshold"]
            high_thresh = dyn_thresh["high_threshold"]

            # HIGH conviction (score >= high_thresh): accept R/R >= 1.0
            # MEDIUM conviction: require R/R >= 1.2
            # Data: BR/USDT SKIPped at score 62.5 because R/R was 1.09
            min_rr = 1.0 if best >= high_thresh else 1.2

            if best >= trade_thresh and has_sl_tp and rr >= min_rr:
                if best >= high_thresh:
                    signals["action"] = f"TRADE_{direction}"
                    signals["conviction"] = "HIGH"
                else:
                    signals["action"] = f"TRADE_{direction}"
                    signals["conviction"] = "MEDIUM"
            else:
                skip_reasons = []
                if best < trade_thresh:
                    skip_reasons.append(f"score {best}<{trade_thresh}")
                if not has_sl_tp:
                    skip_reasons.append("no SL/TP")
                if rr < min_rr:
                    skip_reasons.append(f"R/R {rr}<{min_rr}")
                signals["action"] = "SKIP"
                signals["skip_reason"] = ", ".join(skip_reasons)

            # Store adjusted score for ranking
            signals["adjusted_score"] = best

            results[sym] = signals
        except Exception as e:
            results[sym] = {"error": str(e)}

    # Sort by best score descending
    sorted_results = dict(
        sorted(results.items(),
               key=lambda x: x[1].get("best_score", 0) if "error" not in x[1] else 0,
               reverse=True)
    )

    # --- SCAN HISTORY: persist ALL results for /evolve analysis ---
    # This lets evolve see what we traded AND what we skipped
    scan_history_path = Path("scan_history.json")
    try:
        scan_history = json.load(open(scan_history_path)) if scan_history_path.exists() else []
    except Exception:
        scan_history = []

    scan_summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "timeframe": args.timeframe,
        "pairs_scanned": len(results),
        "trade_signals": sum(1 for v in results.values() if v.get("action", "").startswith("TRADE")),
        "skip_signals": sum(1 for v in results.values() if v.get("action") == "SKIP"),
        "dynamic_threshold": _scan_dynamic_threshold,
        "time_of_day": time_of_day_adjustment(),
        # Compact per-pair summary (only key fields to keep file size reasonable)
        "pairs": {
            sym: {
                "action": v.get("action"),
                "score": v.get("adjusted_score") or v.get("best_score"),
                "direction": v.get("best_direction"),
                "conviction": v.get("conviction"),
                "category": v.get("category"),
                "rr": v.get("suggested_sl_tp", {}).get(
                    "long" if v.get("best_direction") == "LONG" else "short", {}
                ).get("rr"),
                "funding_pct": v.get("funding_rate_pct"),
                "regime": v.get("regime", {}).get("regime") if isinstance(v.get("regime"), dict) else None,
                "squeeze": v.get("squeeze", {}).get("is_squeeze") if isinstance(v.get("squeeze"), dict) else None,
            }
            for sym, v in sorted_results.items() if "error" not in v
        },
    }
    scan_history.append(scan_summary)
    scan_history = scan_history[-500:]  # keep last ~10 days at 48 cycles/day
    try:
        with open(scan_history_path, "w") as f:
            json.dump(scan_history, f, indent=1)
    except Exception:
        pass

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

    # Hard limits: leverage floor (7x minimum) and cap (20x max)
    # Leverage below 7x wastes our edge — PF 2.87 MUST be amplified
    if args.leverage < 7:
        print(json.dumps({
            "success": False,
            "error": f"Leverage {args.leverage}x < min 7x. Use scan's suggested_leverage (min 7x). Resubmit with --leverage >= 7.",
        }, indent=2))
        return 1
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

    # Global circuit breaker: if lost >3% equity in last 2 hours, pause all trading
    closed_trades = _state.get("closed_trades", [])
    now = datetime.now(timezone.utc)
    recent_losses = 0
    for t in reversed(closed_trades):
        try:
            t_dt = datetime.fromisoformat(t.get("ts", ""))
            if (now - t_dt).total_seconds() > 7200:  # only last 2 hours
                break
            pnl = t.get("unrealized_pnl", 0)
            if pnl < 0:
                recent_losses += abs(pnl)
        except Exception:
            pass
    try:
        _equity = get_account()["equity"]
        if _equity > 0 and recent_losses / _equity > 0.03:
            print(json.dumps({
                "success": False,
                "error": f"GLOBAL CIRCUIT BREAKER: Lost ${recent_losses:.2f} ({recent_losses/_equity*100:.1f}% of equity) in last 2h. Pausing 1h.",
            }, indent=2))
            return 1
    except Exception:
        pass

    # Dynamic cooldown: 24h for persistent losers (3+ consecutive losses)
    cooldown_minutes = 30
    recent_for_sym = [t for t in closed_trades if base in t.get("symbol", "")]
    if len(recent_for_sym) >= 3:
        last_3 = recent_for_sym[-3:]
        if all(t.get("pnl_pct", 0) < 0 or t.get("unrealized_pnl", 0) < 0 for t in last_3):
            cooldown_minutes = 1440  # 24 hours

    if last_ts:
        try:
            last_dt = datetime.fromisoformat(last_ts)
            minutes_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            if minutes_since < cooldown_minutes:
                # EMERGENCY REVERSAL EXEMPTION: if last trade was opposite direction,
                # allow immediate re-entry (SL hit + signals flipped = reversal opportunity)
                last_trade_for_sym = [t for t in closed_trades if base in t.get("symbol", "")]
                is_reversal = False
                if last_trade_for_sym:
                    last_side = last_trade_for_sym[-1].get("side", "")
                    current_side = "long" if side == "buy" else "short"
                    if last_side != current_side:
                        is_reversal = True

                if not is_reversal:
                    remaining = round(cooldown_minutes - minutes_since)
                    reason = f"COOLDOWN: {base} was traded {int(minutes_since)}m ago. Wait {remaining}m more."
                    if cooldown_minutes > 60:
                        reason += f" (EXTENDED: 3+ consecutive losses → 24h cooldown)"
                    print(json.dumps({
                        "success": False,
                        "error": reason,
                    }, indent=2))
                    return 1
                else:
                    print(f"[bracket] REVERSAL EXEMPTION: {base} cooldown bypassed (direction flipped from {last_trade_for_sym[-1].get('side')} to {'long' if side == 'buy' else 'short'})")
        except Exception:
            pass

    # Hard limits: positions count + exposure
    try:
        positions = get_positions()
        orders = get_open_orders()
        account = get_account()

        # Max 10 concurrent positions
        if len(positions) >= 10:
            print(json.dumps({"success": False, "error": f"Max 10 positions ({len(positions)} open)"}, indent=2))
            return 1

        # Max 90% gross exposure
        if account["exposure_pct"] > 90:
            print(json.dumps({
                "success": False,
                "error": f"Exposure {account['exposure_pct']:.1f}% > max 90%",
            }, indent=2))
            return 1

        # Category check
        allowed, reason = check_category_limit(symbol, positions, orders)
        if not allowed:
            print(json.dumps({"success": False, "error": reason}, indent=2))
            return 1

        # Correlation check — prevent hidden concentration risk
        # If 3+ positions already open, check if new symbol is too correlated
        if len(positions) >= 3:
            try:
                new_df = get_bars(symbol, timeframe="4h", limit=100)
                if len(new_df) >= 50:
                    import numpy as _np
                    new_returns = new_df["close"].pct_change().dropna().values
                    high_corr_count = 0
                    for pos in positions[:5]:  # check max 5 to limit API calls
                        try:
                            pos_df = get_bars(pos["symbol"], timeframe="4h", limit=100)
                            if len(pos_df) >= 50:
                                pos_returns = pos_df["close"].pct_change().dropna().values
                                min_len = min(len(new_returns), len(pos_returns))
                                if min_len > 20:
                                    corr = _np.corrcoef(new_returns[-min_len:], pos_returns[-min_len:])[0, 1]
                                    if abs(corr) > 0.85:
                                        high_corr_count += 1
                        except Exception:
                            continue
                    if high_corr_count >= 3:
                        print(json.dumps({
                            "success": False,
                            "error": f"CORRELATION BLOCK: {symbol} correlated >0.85 with {high_corr_count} open positions. Hidden concentration risk.",
                        }, indent=2))
                        return 1
            except Exception:
                pass  # Non-fatal: allow trade if correlation check fails

        # Projected max loss check — prevent catastrophic correlated losses
        # Sum all existing positions' SL distances + this new trade's SL distance
        equity = account["equity"]
        if equity > 0:
            projected_loss = 0
            for pos in positions:
                # Estimate each position's max loss as 5% of equity (risk per trade)
                projected_loss += equity * 0.05
            # Add this new trade's projected loss
            new_risk = abs(args.tp - args.sl) * args.qty if args.sl else equity * 0.05
            projected_loss += new_risk
            max_loss_pct = projected_loss / equity * 100
            if max_loss_pct > 30:
                print(json.dumps({
                    "success": False,
                    "error": f"PROJECTED MAX LOSS {max_loss_pct:.1f}% > 30% with {len(positions)} positions + this trade. Reduce exposure first.",
                }, indent=2))
                return 1
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Pre-trade checks failed: {e}"}, indent=2))
        return 1

    # R/R validation with live price (always enforced — no bypass)
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
    """Persist COMPREHENSIVE trade data to state.json for /evolve analysis.

    Every field logged here feeds the self-improvement engine.
    At 1M€ capital, each data point helps optimize for real edge.
    """
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

    # --- SIGNAL ATTRIBUTION: every indicator at time of entry ---
    signal_attribution = None
    market_context = None
    try:
        df = get_bars(symbol, timeframe="4h", limit=500)
        if len(df) >= 50:
            fr = get_funding_rate(symbol)
            from trading.indicators import compute_signals as _cs, time_of_day_adjustment as _tod
            sig = _cs(df, funding_rate=fr.get("funding_rate_pct", 0))
            tod = _tod()
            signal_attribution = {
                "long_score": sig["signals"]["long_score"],
                "short_score": sig["signals"]["short_score"],
                "long_raw": sig["signals"]["long_raw"],
                "short_raw": sig["signals"]["short_raw"],
                "regime": sig.get("regime", {}).get("regime"),
                "adx": sig.get("regime", {}).get("adx"),
                "squeeze": sig.get("squeeze", {}).get("is_squeeze"),
                "atr_pct": sig["indicators"].get("atr_pct"),
                "atr14": sig["indicators"].get("atr14"),
                "rsi": sig["indicators"].get("rsi14"),
                "volume_ratio": sig["indicators"].get("volume_ratio"),
                "ema_trend": sig["indicators"].get("ema_trend"),
                "macd_histogram": sig["indicators"].get("macd_histogram"),
                "bollinger_pct_b": sig["indicators"].get("bollinger_pct_b"),
                "vwap": sig["indicators"].get("vwap"),
                "funding_rate_pct": fr.get("funding_rate_pct", 0),
                "time_of_day_window": tod.get("window"),
            }
            # Market context for macro analysis
            market_context = {
                "price_at_entry": sig["price"],
                "sma200": sig["indicators"].get("sma200"),
                "above_sma200": sig["price"] > sig["indicators"].get("sma200", 0) if sig["indicators"].get("sma200") else None,
                "bandwidth_pctile": sig.get("squeeze", {}).get("bandwidth_percentile"),
            }
    except Exception:
        pass

    # --- EXECUTION QUALITY: slippage, spread, fees ---
    execution_quality = {}
    try:
        ticker = get_ticker(symbol)
        requested_price = args.limit or ticker.get("last", 0)
        fill_price = result.details.get("entry_price") or requested_price
        execution_quality = {
            "requested_price": requested_price,
            "fill_price": fill_price,
            "slippage_pct": round((fill_price - requested_price) / requested_price * 100, 4) if requested_price else 0,
            "spread_at_entry": round(ticker.get("ask", 0) - ticker.get("bid", 0), 6),
            "spread_pct": round((ticker.get("ask", 0) - ticker.get("bid", 0)) / ticker.get("bid", 1) * 100, 4) if ticker.get("bid") else 0,
            "volume_24h_at_entry": ticker.get("volume_24h", 0),
            "order_type": "limit" if args.limit else "market",
        }
    except Exception:
        pass

    entry_price = args.limit or result.details.get("entry_price", 0)

    state.setdefault("trades", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "order_id": result.order_id,
        "symbol": symbol,
        "side": side,
        "qty": args.qty,
        "entry": entry_price,
        "sl": args.sl,
        "tp": args.tp,
        "rr": round(
            abs(args.tp - entry_price) / abs(entry_price - args.sl), 2
        ) if entry_price and args.sl != entry_price else 0,
        "leverage": args.leverage,
        "category": get_category(symbol),
        "sl_order_id": result.details.get("sl_order_id"),
        "tp_order_id": result.details.get("tp_order_id"),
        "protection_errors": result.details.get("protection_errors"),
        "signals": signal_attribution,
        "market_context": market_context,
        "execution": execution_quality,
        # MFE/MAE tracking: initialized here, updated by protect on each cycle
        "mfe_pct": 0,  # Max Favorable Excursion — best unrealized P&L during trade
        "mae_pct": 0,  # Max Adverse Excursion — worst unrealized P&L during trade
    })

    # Keep last 500 trades (increased from 200 for better /evolve analysis)
    state["trades"] = state["trades"][-500:]
    state["killed"] = state.get("killed", False)

    # --- EQUITY CURVE: snapshot on every trade ---
    try:
        account = get_account()
        state.setdefault("equity_curve", []).append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "equity": account["equity"],
            "exposure_pct": account["exposure_pct"],
            "positions_count": account["positions_count"],
            "event": "trade_entry",
        })
        state["equity_curve"] = state["equity_curve"][-2000:]  # ~40 days at 48 cycles/day
    except Exception:
        pass

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
    state["closed_trades"] = state["closed_trades"][-500:]  # increased for /evolve depth

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

    # Reconcile stale trades first
    state_path = Path("state.json")
    try:
        _state = json.load(open(state_path)) if state_path.exists() else {}
    except Exception:
        _state = {}
    _reconcile_stale_trades(_state, positions, state_path)

    # --- MFE/MAE tracking: update max favorable/adverse excursion for open trades ---
    tracked_trades = _state.get("trades", [])
    mfe_mae_updated = False
    for pos in positions:
        pnl = pos.get("pnl_pct", 0)
        for trade in tracked_trades:
            if trade.get("symbol") == pos["symbol"]:
                if pnl > trade.get("mfe_pct", 0):
                    trade["mfe_pct"] = round(pnl, 2)
                    mfe_mae_updated = True
                if pnl < trade.get("mae_pct", 0):
                    trade["mae_pct"] = round(pnl, 2)
                    mfe_mae_updated = True
                break
    if mfe_mae_updated:
        try:
            with open(state_path, "w") as f:
                json.dump(_state, f, indent=2)
        except Exception:
            pass

    # --- Equity curve snapshot (protect runs every 5 min = high-res curve) ---
    _state.setdefault("equity_curve", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "equity": account["equity"],
        "exposure_pct": account["exposure_pct"],
        "positions_count": len(positions),
        "unrealized_pnl": account["unrealized_pnl"],
        "event": "protect_check",
    })
    _state["equity_curve"] = _state["equity_curve"][-2000:]
    try:
        with open(state_path, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception:
        pass

    actions = []

    # --- Cleanup orphaned orders (SL/TP for positions that no longer exist) ---
    position_symbols = {p["symbol"] for p in positions}
    orphaned = [o for o in orders if o["symbol"] not in position_symbols and o.get("reduce_only")]
    if orphaned:
        orphan_symbols = set()
        for o in orphaned:
            orphan_symbols.add(o["symbol"])
        for sym in orphan_symbols:
            try:
                cancel_all_orders(sym)
                count = sum(1 for o in orphaned if o["symbol"] == sym)
                actions.append({"symbol": sym, "action": "CANCEL_ORPHANED",
                               "count": count, "reason": "no open position"})
            except Exception as e:
                actions.append({"symbol": sym, "action": "CANCEL_ORPHANED_FAILED",
                               "error": str(e)})
        print(f"[protect] Cleaned {len(orphaned)} orphaned orders across {len(orphan_symbols)} symbols")

    if not positions:
        print(json.dumps({"status": "no_positions", "orphaned_cleaned": len(orphaned),
                          "actions": actions}, indent=2))
        return 0

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

        # Partial profit-taking — close 50% when PnL reaches ~2x ATR distance
        # This is where most wins close (before 3x ATR TP). Lock in profit.
        # Also trigger at +5% PnL as fallback (for positions without ATR data)
        partial_trigger = 5.0  # default fallback
        try:
            df_pp = get_bars(sym, timeframe="4h", limit=100)
            if len(df_pp) >= 50:
                from trading.indicators import atr as _atr
                atr_val = _atr(df_pp["high"], df_pp["low"], df_pp["close"])
                atr_last = float(atr_val.iloc[-1])
                if entry > 0 and atr_last > 0:
                    # 2x ATR as percentage of entry = partial profit threshold
                    partial_trigger = (2 * atr_last / entry) * 100
        except Exception:
            pass

        if pnl_pct >= partial_trigger and contracts > 0:
            half_qty = round(contracts / 2, 6)
            if half_qty > 0:
                try:
                    from trading.client import place_market_order
                    result = place_market_order(sym, half_qty, side=close_side, reduce_only=True)
                    if result.success:
                        actions.append({"symbol": sym, "action": "PARTIAL_PROFIT_50%",
                                       "qty_closed": half_qty, "pnl_pct": pnl_pct,
                                       "trigger_pct": round(partial_trigger, 2)})
                        # Move SL to breakeven on remaining position
                        sl_order = next(
                            (o for o in orders if o["symbol"] == sym and o.get("reduce_only")
                             and "STOP" in o.get("type", "").upper()), None)
                        if sl_order and ((side == "long" and entry > sl_order["stop_price"]) or
                                        (side == "short" and entry < sl_order["stop_price"])):
                            try:
                                cancel_order(sl_order["id"], sym)
                                remaining_qty = round(contracts - half_qty, 6)
                                place_stop_order(sym, remaining_qty, entry, side=close_side)
                                actions.append({"symbol": sym, "action": "SL_TO_BREAKEVEN",
                                               "new_sl": entry})
                            except Exception:
                                pass
                except Exception as e:
                    actions.append({"symbol": sym, "action": "PARTIAL_PROFIT_FAILED",
                                   "error": str(e)})

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
                                                 lookback=22, multiplier=3.5)
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
# funding-farm — Pure funding rate income strategy
# ---------------------------------------------------------------------------

def cmd_funding_farm(args):
    """Find pairs with extreme funding rates for pure income.

    When funding is very negative → LONG (shorts pay you)
    When funding is very positive → SHORT (longs pay you)
    Risk is minimal: tight SL (1x ATR), no directional bet needed.
    Income: 0.1% per 8h = 0.3%/day = 9%/month at 10x leverage.
    """
    pairs = get_active_pairs()
    print(f"[funding-farm] Scanning {len(pairs)} pairs for extreme funding...", flush=True)

    try:
        account = get_account()
        equity = account["equity"]
    except Exception:
        equity = 0

    opportunities = []
    for sym in pairs:
        try:
            fr = get_funding_rate(sym)
            rate = fr.get("funding_rate_pct", 0)
            if abs(rate) < 0.02:
                continue  # not extreme enough

            ticker = get_ticker(sym)
            price = ticker.get("last", 0)
            if price <= 0:
                continue

            direction = "LONG" if rate < 0 else "SHORT"
            income_per_8h = abs(rate) / 100  # as fraction
            daily_income_pct = income_per_8h * 3  # 3 funding periods per day

            # Position sizing: 3% risk (conservative for farming)
            df = get_bars(sym, timeframe="4h", limit=100)
            atr_last = 0
            if len(df) >= 50:
                from trading.indicators import atr as _atr
                atr_val = _atr(df["high"], df["low"], df["close"])
                atr_last = float(atr_val.iloc[-1])

            sl_dist = atr_last if atr_last > 0 else price * 0.02
            risk_amount = equity * 0.03 if equity > 0 else 0
            suggested_qty = round(risk_amount / sl_dist, 6) if sl_dist > 0 else 0
            suggested_leverage = 10

            if direction == "LONG":
                sl_price = round(price - sl_dist, 6)
                tp_price = round(price + sl_dist * 2, 6)  # modest TP, main income is funding
            else:
                sl_price = round(price + sl_dist, 6)
                tp_price = round(price - sl_dist * 2, 6)

            opportunities.append({
                "symbol": sym,
                "funding_rate_pct": rate,
                "direction": direction,
                "daily_income_pct": round(daily_income_pct * 100, 4),
                "price": price,
                "suggested_qty": suggested_qty,
                "sl": sl_price,
                "tp": tp_price,
                "leverage": suggested_leverage,
                "action": f"FARM_{direction}",
                "category": get_category(sym),
            })
        except Exception:
            continue

    # Sort by absolute funding rate (most profitable first)
    opportunities.sort(key=lambda x: abs(x["funding_rate_pct"]), reverse=True)
    print(json.dumps({"funding_farm_opportunities": opportunities[:10]}, indent=2))
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
    s.add_argument("--funding-farm", action="store_true", help="Funding rate farming mode: find pairs with extreme funding for free income")

    sub.add_parser("funding-farm", help="Pure funding rate farming — find extreme funding for income")

    b = sub.add_parser("bracket", help="Place bracket order (entry + SL + TP)")
    b.add_argument("symbol", help="Symbol (BTC, ETH, BTC/USDT:USDT)")
    b.add_argument("qty", type=float, help="Size in base currency")
    b.add_argument("tp", type=float, help="Take-profit price")
    b.add_argument("sl", type=float, help="Stop-loss price")
    b.add_argument("--side", default="buy", choices=["buy", "sell"], help="buy=LONG, sell=SHORT")
    b.add_argument("--limit", type=float, default=None, help="Limit entry price (None=market)")
    b.add_argument("--leverage", type=int, default=10, help="Leverage (default: 10, max 20)")
    b.add_argument("--min-rr", type=float, default=1.2, help="Min R/R ratio (default: 1.2, aim for 2.0+)")

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
        "funding-farm": cmd_funding_farm,
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
