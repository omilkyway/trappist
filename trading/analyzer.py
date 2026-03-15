#!/usr/bin/env python3
"""Trade performance analyzer for auto-improve pipeline.

Analyzes closed trades from Alpaca to answer:
- Which trades made money? Which lost?
- Why? (entry timing, SL too tight, TP too ambitious, wrong direction)
- Composite score vs actual outcome correlation
- Sector/direction performance breakdown
- What data was missing that could have improved decisions?

Usage:
    python trading/analyzer.py [--days 30] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Trade reconstruction from Alpaca closed orders
# ---------------------------------------------------------------------------

def reconstruct_trades(closed_orders: list[dict]) -> list[dict]:
    """Reconstruct complete trades (entry + exit) from closed orders.

    Groups orders by symbol and client_order_id prefix to match
    entries with their exits (OCO legs, manual closes).
    """
    # Group by symbol
    by_symbol = defaultdict(list)
    for order in closed_orders:
        if "error" in order:
            continue
        by_symbol[order["symbol"]].append(order)

    trades = []

    for symbol, orders in by_symbol.items():
        # Sort by submitted_at
        orders.sort(key=lambda o: o.get("submitted_at", ""))

        # Find entry orders (OPG, bracket parent, market/limit buys)
        entries = []
        exits = []

        for o in orders:
            cid = o.get("client_order_id", "") or ""
            order_class = o.get("order_class", "")
            status = o.get("status", "")

            if "filled" not in status.lower():
                continue

            is_entry = (
                "opg" in cid.lower()
                or "bracket" in cid.lower()
                or "ct_" in cid.lower()
            )

            # OCO orders are exits
            if "oco" in cid.lower() or "oco" in order_class.lower():
                exits.append(o)
            elif is_entry:
                entries.append(o)
            else:
                # Legs of bracket/OCO orders
                if o.get("legs"):
                    for leg in o["legs"]:
                        if "filled" in leg.get("status", "").lower():
                            exits.append(leg)

        # Match entries with exits
        for entry in entries:
            entry_price = entry.get("filled_avg_price")
            entry_qty = float(entry.get("filled_qty", 0))
            entry_side = entry.get("side", "")
            entry_time = entry.get("filled_at", entry.get("submitted_at", ""))

            if not entry_price or entry_qty == 0:
                continue

            is_long = "buy" in entry_side.lower()
            is_short = "sell" in entry_side.lower()

            # Find matching exit
            exit_order = _find_exit(symbol, entry, exits)

            trade = {
                "symbol": symbol,
                "direction": "LONG" if is_long else "SHORT",
                "entry_price": entry_price,
                "entry_qty": entry_qty,
                "entry_time": entry_time,
                "entry_order_id": entry.get("id", "")[:8],
                "exit_price": None,
                "exit_time": None,
                "exit_type": None,  # tp_hit, sl_hit, manual, time_stop, open
                "pnl_dollars": None,
                "pnl_percent": None,
                "hold_days": None,
                "notional": round(entry_price * entry_qty, 2),
            }

            if exit_order:
                exit_price = exit_order.get("filled_avg_price")
                if exit_price:
                    trade["exit_price"] = exit_price
                    trade["exit_time"] = exit_order.get("filled_at", "")
                    trade["exit_type"] = _determine_exit_type(entry, exit_order)

                    if is_long:
                        trade["pnl_dollars"] = round((exit_price - entry_price) * entry_qty, 2)
                    else:  # SHORT
                        trade["pnl_dollars"] = round((entry_price - exit_price) * entry_qty, 2)

                    trade["pnl_percent"] = round(trade["pnl_dollars"] / trade["notional"] * 100, 2)
                    trade["hold_days"] = _calc_hold_days(trade["entry_time"], trade["exit_time"])
            else:
                trade["exit_type"] = "open"

            trades.append(trade)

    return trades


def _find_exit(symbol: str, entry: dict, exits: list[dict]) -> dict | None:
    """Find the exit order matching an entry."""
    entry_time = entry.get("filled_at", entry.get("submitted_at", ""))

    # Look for exits after entry time
    candidates = []
    for ex in exits:
        ex_sym = ex.get("symbol", symbol)
        if ex_sym != symbol:
            continue
        ex_time = ex.get("filled_at", ex.get("submitted_at", ""))
        if ex_time and entry_time and ex_time > entry_time:
            candidates.append(ex)

    if not candidates:
        return None

    # Return earliest exit
    candidates.sort(key=lambda o: o.get("filled_at", o.get("submitted_at", "")))
    return candidates[0]


def _determine_exit_type(entry: dict, exit_order: dict) -> str:
    """Determine how a trade was exited."""
    cid = exit_order.get("client_order_id", "") or ""
    order_type = exit_order.get("type", "")

    if "oco" in cid.lower():
        # Check if it hit limit (TP) or stop (SL)
        if exit_order.get("limit_price") and exit_order.get("filled_avg_price"):
            return "tp_hit"
        if exit_order.get("stop_price"):
            return "sl_hit"
        return "oco_exit"

    if "stop" in order_type.lower():
        return "sl_hit"
    if "limit" in order_type.lower():
        return "tp_hit"
    if "close" in cid.lower():
        return "manual"

    return "unknown"


def _calc_hold_days(entry_time: str, exit_time: str) -> int | None:
    """Calculate trading days held."""
    if not entry_time or not exit_time:
        return None
    try:
        e = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        x = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
        return max(1, (x - e).days)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Performance analysis
# ---------------------------------------------------------------------------

def analyze_performance(trades: list[dict]) -> dict:
    """Comprehensive performance analysis across all trades."""
    if not trades:
        return {"error": "no trades to analyze"}

    closed = [t for t in trades if t["exit_type"] != "open"]
    open_trades = [t for t in trades if t["exit_type"] == "open"]

    if not closed:
        return {
            "total_trades": len(trades),
            "open_trades": len(open_trades),
            "closed_trades": 0,
            "note": "No closed trades yet — all positions still open",
        }

    winners = [t for t in closed if t["pnl_dollars"] and t["pnl_dollars"] > 0]
    losers = [t for t in closed if t["pnl_dollars"] and t["pnl_dollars"] < 0]
    breakeven = [t for t in closed if t["pnl_dollars"] == 0]

    total_pnl = sum(t["pnl_dollars"] for t in closed if t["pnl_dollars"])
    avg_win = sum(t["pnl_dollars"] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t["pnl_dollars"] for t in losers) / len(losers) if losers else 0

    # Direction breakdown
    longs = [t for t in closed if t["direction"] == "LONG"]
    shorts = [t for t in closed if t["direction"] == "SHORT"]

    # Exit type breakdown
    tp_hits = [t for t in closed if t["exit_type"] == "tp_hit"]
    sl_hits = [t for t in closed if t["exit_type"] == "sl_hit"]
    manual = [t for t in closed if t["exit_type"] == "manual"]

    # Sector analysis (from symbol patterns)
    by_symbol = defaultdict(list)
    for t in closed:
        by_symbol[t["symbol"]].append(t)

    # Hold time analysis
    hold_days = [t["hold_days"] for t in closed if t["hold_days"] is not None]

    result = {
        "summary": {
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(open_trades),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(len(winners) / len(closed) * 100, 1) if closed else 0,
            "winners": len(winners),
            "losers": len(losers),
            "breakeven": len(breakeven),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else float("inf"),
            "expectancy": round(total_pnl / len(closed), 2) if closed else 0,
        },
        "by_direction": {
            "LONG": _direction_stats(longs),
            "SHORT": _direction_stats(shorts),
        },
        "by_exit_type": {
            "tp_hit": len(tp_hits),
            "sl_hit": len(sl_hits),
            "manual": len(manual),
            "tp_hit_rate": round(len(tp_hits) / len(closed) * 100, 1) if closed else 0,
        },
        "by_symbol": {
            sym: _direction_stats(tds) for sym, tds in by_symbol.items()
        },
        "hold_time": {
            "avg_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else None,
            "max_days": max(hold_days) if hold_days else None,
            "min_days": min(hold_days) if hold_days else None,
        },
        "open_positions": [
            {
                "symbol": t["symbol"],
                "direction": t["direction"],
                "entry_price": t["entry_price"],
                "notional": t["notional"],
            }
            for t in open_trades
        ],
    }

    # Add trade-by-trade detail
    result["trades"] = [
        {
            "symbol": t["symbol"],
            "direction": t["direction"],
            "entry": t["entry_price"],
            "exit": t["exit_price"],
            "pnl": t["pnl_dollars"],
            "pnl_pct": t["pnl_percent"],
            "exit_type": t["exit_type"],
            "hold_days": t["hold_days"],
        }
        for t in closed
    ]

    return result


def _direction_stats(trades: list[dict]) -> dict:
    """Compute stats for a set of trades."""
    if not trades:
        return {"count": 0, "pnl": 0, "win_rate": 0}
    pnl_vals = [t["pnl_dollars"] for t in trades if t["pnl_dollars"] is not None]
    winners = [p for p in pnl_vals if p > 0]
    return {
        "count": len(trades),
        "pnl": round(sum(pnl_vals), 2) if pnl_vals else 0,
        "win_rate": round(len(winners) / len(pnl_vals) * 100, 1) if pnl_vals else 0,
        "avg_pnl": round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else 0,
    }


# ---------------------------------------------------------------------------
# Diagnosis: what went wrong and how to improve
# ---------------------------------------------------------------------------

def diagnose_trades(trades: list[dict], reports: list[dict] = None) -> list[dict]:
    """Produce actionable diagnoses for each trade.

    Crosses trade outcomes with report projections to identify:
    - SL too tight (hit SL but price reversed to TP zone)
    - TP too ambitious (never reached, eventually hit SL)
    - Wrong direction (LONG when should have been SHORT or vice versa)
    - Timing issue (right direction but entered too late)
    - Sector rotation miss
    """
    diagnoses = []

    for trade in trades:
        if trade["exit_type"] == "open":
            continue

        diag = {
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "pnl": trade["pnl_dollars"],
            "exit_type": trade["exit_type"],
            "issues": [],
            "suggestions": [],
        }

        # Issue 1: SL hit = loss
        if trade["exit_type"] == "sl_hit":
            diag["issues"].append("Stop-loss triggered — trade moved against position")

            # Check if SL was too tight
            if trade["pnl_percent"] and abs(trade["pnl_percent"]) < 3:
                diag["issues"].append(f"SL was tight ({trade['pnl_percent']}%) — consider wider ATR-based stops")
                diag["suggestions"].append("WIDEN_SL: Use 2x ATR instead of fixed percentage for stop-loss")

            if trade["hold_days"] and trade["hold_days"] <= 1:
                diag["issues"].append("Stopped out same day — possible gap or volatility spike")
                diag["suggestions"].append("GAP_PROTECTION: Add pre-market gap check before entry, skip if gap > 2%")

        # Issue 2: TP hit = win, but check if we left money on table
        if trade["exit_type"] == "tp_hit":
            if trade["pnl_percent"] and trade["pnl_percent"] < 3:
                diag["issues"].append(f"TP hit but small gain ({trade['pnl_percent']}%) — may be leaving money on table")
                diag["suggestions"].append("TRAILING_STOP: Consider trailing stop after 50% of TP distance reached")

        # Issue 3: Hold time analysis
        if trade["hold_days"]:
            if trade["hold_days"] > 7 and trade["exit_type"] == "sl_hit":
                diag["issues"].append(f"Held {trade['hold_days']} days before SL — slow bleed, could have cut earlier")
                diag["suggestions"].append("TIME_DECAY: Add time-based tightening of SL after 5 days if not progressing")

        # Issue 4: Direction analysis
        if trade["pnl_dollars"] and trade["pnl_dollars"] < -100:
            diag["issues"].append(f"Significant loss (${trade['pnl_dollars']}) — review entry thesis")
            diag["suggestions"].append("THESIS_REVIEW: Cross-check entry signals with actual price action post-entry")

        # Cross with report data if available
        if reports:
            report_match = _find_report_for_trade(trade, reports)
            if report_match:
                # Check if improvements mentioned relate to this trade
                for imp in report_match.get("improvements", []):
                    if trade["symbol"].lower() in imp.get("description", "").lower():
                        diag["issues"].append(f"Report noted: {imp['title']}")

        if diag["issues"]:
            diagnoses.append(diag)

    return diagnoses


def _find_report_for_trade(trade: dict, reports: list[dict]) -> dict | None:
    """Find the session report that corresponds to a trade."""
    entry_time = trade.get("entry_time", "")
    if not entry_time:
        return None

    try:
        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    for report in reports:
        try:
            report_dt = datetime.strptime(report["date"], "%Y-%m-%d %H:%M")
            # Match if within same day
            if abs((entry_dt.replace(tzinfo=None) - report_dt).days) <= 1:
                # Check if symbol is in this report
                for t in report.get("tickers", []):
                    if t["symbol"] == trade["symbol"]:
                        return report
        except (ValueError, TypeError):
            continue

    return None


# ---------------------------------------------------------------------------
# Recurring patterns detection
# ---------------------------------------------------------------------------

def detect_patterns(trades: list[dict]) -> list[dict]:
    """Detect recurring patterns across all trades."""
    patterns = []

    if not trades:
        return patterns

    closed = [t for t in trades if t["exit_type"] != "open"]
    if not closed:
        return patterns

    # Pattern 1: Consistent direction bias
    longs = [t for t in closed if t["direction"] == "LONG"]
    shorts = [t for t in closed if t["direction"] == "SHORT"]
    long_wr = sum(1 for t in longs if t["pnl_dollars"] and t["pnl_dollars"] > 0) / len(longs) * 100 if longs else 0
    short_wr = sum(1 for t in shorts if t["pnl_dollars"] and t["pnl_dollars"] > 0) / len(shorts) * 100 if shorts else 0

    if longs and shorts and abs(long_wr - short_wr) > 30:
        better = "LONG" if long_wr > short_wr else "SHORT"
        worse = "SHORT" if better == "LONG" else "LONG"
        patterns.append({
            "pattern": "DIRECTION_BIAS",
            "severity": "high",
            "description": f"{better} trades win {max(long_wr, short_wr):.0f}% vs {worse} at {min(long_wr, short_wr):.0f}%",
            "action": f"Review {worse} entry criteria — may need stricter filters or different indicators",
        })

    # Pattern 2: Same-day stops
    same_day_stops = [t for t in closed if t["exit_type"] == "sl_hit" and t["hold_days"] and t["hold_days"] <= 1]
    if len(same_day_stops) >= 2:
        patterns.append({
            "pattern": "FREQUENT_SAME_DAY_STOPS",
            "severity": "high",
            "description": f"{len(same_day_stops)} trades stopped out same day — entry timing or gap risk",
            "action": "Add pre-market gap filter and avoid OPG entries when overnight futures move > 1%",
        })

    # Pattern 3: SL hit rate too high
    sl_hits = [t for t in closed if t["exit_type"] == "sl_hit"]
    if len(sl_hits) > len(closed) * 0.6 and len(closed) >= 3:
        patterns.append({
            "pattern": "HIGH_SL_HIT_RATE",
            "severity": "critical",
            "description": f"{len(sl_hits)}/{len(closed)} trades hit stop-loss ({len(sl_hits)/len(closed)*100:.0f}%)",
            "action": "Widen stops (use 2.5x ATR), improve entry timing, add confirmation signals",
        })

    # Pattern 4: Average loss > average win (bad R/R)
    wins_pnl = [t["pnl_dollars"] for t in closed if t["pnl_dollars"] and t["pnl_dollars"] > 0]
    loss_pnl = [abs(t["pnl_dollars"]) for t in closed if t["pnl_dollars"] and t["pnl_dollars"] < 0]
    if wins_pnl and loss_pnl:
        avg_w = sum(wins_pnl) / len(wins_pnl)
        avg_l = sum(loss_pnl) / len(loss_pnl)
        if avg_l > avg_w * 1.2:
            patterns.append({
                "pattern": "BAD_RISK_REWARD",
                "severity": "critical",
                "description": f"Avg loss (${avg_l:.0f}) > avg win (${avg_w:.0f}) — R/R ratio is inverted",
                "action": "Tighten stop-losses OR widen take-profits. Current system loses more per trade than it gains.",
            })

    # Pattern 5: Sector concentration losses
    by_symbol = defaultdict(list)
    for t in closed:
        by_symbol[t["symbol"]].append(t)
    for sym, sym_trades in by_symbol.items():
        sym_pnl = sum(t["pnl_dollars"] for t in sym_trades if t["pnl_dollars"])
        if sym_pnl < -200 and len(sym_trades) >= 2:
            patterns.append({
                "pattern": "REPEAT_LOSER",
                "severity": "medium",
                "description": f"{sym}: {len(sym_trades)} trades, total P&L ${sym_pnl:.0f} — recurring losses on same name",
                "action": f"Blacklist {sym} for 2 weeks or add stricter entry criteria",
            })

    # Pattern 6: Most trades are low conviction
    low_pnl = [t for t in closed if t["pnl_percent"] and abs(t["pnl_percent"]) < 1]
    if len(low_pnl) > len(closed) * 0.5 and len(closed) >= 4:
        patterns.append({
            "pattern": "LOW_CONVICTION_TRADES",
            "severity": "medium",
            "description": f"{len(low_pnl)}/{len(closed)} trades moved < 1% — low edge",
            "action": "Raise composite score threshold from 55 to 65, only take highest conviction setups",
        })

    return patterns


# ---------------------------------------------------------------------------
# Full analysis pipeline
# ---------------------------------------------------------------------------

def full_analysis(days: int = 30) -> dict:
    """Run complete trade analysis pipeline."""
    from trading.collector import collect_trades, collect_reports

    # Collect data
    trade_data = collect_trades(days)
    reports = collect_reports(days)

    # Reconstruct trades
    trades = reconstruct_trades(trade_data.get("closed_orders", []))

    # Analyze
    performance = analyze_performance(trades)
    diagnoses = diagnose_trades(trades, reports)
    patterns = detect_patterns(trades)

    return {
        "analyzed_at": datetime.now().isoformat(),
        "days_lookback": days,
        "performance": performance,
        "diagnoses": diagnoses,
        "patterns": patterns,
        "report_count": len(reports),
        "report_improvements": _aggregate_improvements(reports),
    }


def _aggregate_improvements(reports: list[dict]) -> list[dict]:
    """Aggregate improvement suggestions across all reports."""
    seen = set()
    improvements = []
    for report in reports:
        for imp in report.get("improvements", []):
            key = imp["title"].lower()
            if key not in seen:
                seen.add(key)
                improvements.append({
                    "title": imp["title"],
                    "description": imp["description"],
                    "from_session": report["date"],
                })
    return improvements


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Trade performance analyzer")
    parser.add_argument("--days", type=int, default=30, help="Lookback period")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = full_analysis(args.days)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_analysis(result)


def _print_analysis(result: dict):
    """Human-readable analysis output."""
    print(f"\n{'='*60}")
    print(f" TRADE PERFORMANCE ANALYSIS")
    print(f"{'='*60}\n")

    perf = result.get("performance", {})
    s = perf.get("summary", {})

    print(f"  Total P&L: ${s.get('total_pnl', 0):+,.2f}")
    print(f"  Win Rate: {s.get('win_rate', 0)}% ({s.get('winners', 0)}W / {s.get('losers', 0)}L)")
    print(f"  Profit Factor: {s.get('profit_factor', 0)}")
    print(f"  Expectancy: ${s.get('expectancy', 0):+,.2f} per trade")
    print(f"  Avg Win: ${s.get('avg_win', 0):+,.2f} | Avg Loss: ${s.get('avg_loss', 0):+,.2f}")

    # Direction breakdown
    by_dir = perf.get("by_direction", {})
    for d in ["LONG", "SHORT"]:
        ds = by_dir.get(d, {})
        if ds.get("count", 0) > 0:
            print(f"\n  {d}: {ds['count']} trades | P&L ${ds['pnl']:+,.2f} | Win rate {ds['win_rate']}%")

    # Patterns
    patterns = result.get("patterns", [])
    if patterns:
        print(f"\n  --- PATTERNS DETECTED ({len(patterns)}) ---")
        for p in patterns:
            icon = "!!!" if p["severity"] == "critical" else "!!" if p["severity"] == "high" else "!"
            print(f"  {icon} [{p['pattern']}] {p['description']}")
            print(f"      Action: {p['action']}")

    # Diagnoses
    diagnoses = result.get("diagnoses", [])
    if diagnoses:
        print(f"\n  --- TRADE DIAGNOSES ({len(diagnoses)}) ---")
        for d in diagnoses[:10]:
            pnl = d.get("pnl", 0) or 0
            print(f"  {d['symbol']} {d['direction']} (${pnl:+,.2f}): {d['exit_type']}")
            for issue in d["issues"][:2]:
                print(f"    - {issue}")
            for sug in d["suggestions"][:1]:
                print(f"    → {sug}")

    # Recurring report improvements
    imps = result.get("report_improvements", [])
    if imps:
        print(f"\n  --- RECURRING IMPROVEMENT THEMES ---")
        for imp in imps[:5]:
            print(f"  * {imp['title']} (from {imp['from_session']})")

    print()


if __name__ == "__main__":
    main()
