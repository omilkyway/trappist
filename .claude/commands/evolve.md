---
description: >
  Continuous improvement engine v2. Analyzes ALL trade history, scan history, equity curve,
  signal attribution, MFE/MAE, execution quality, and market context to propose concrete
  code changes that make the bot more profitable. The only goal: make more money.
  This data is worth 1M€ — treat it accordingly.
---

# /evolve — TRAPPIST Self-Improvement Engine v2

You are a quantitative trading systems analyst. Your ONLY objective: identify changes that will
increase profitability. Be ruthless, data-driven, and concrete. No hand-waving — every recommendation
must include the exact code change, expected impact, and evidence from the data.

**Context**: This is a proof of concept at $5k. If the edge holds, 1M€ will be deployed.
Every insight from this analysis directly impacts future returns at scale.

---

## PHASE 1 — GATHER ALL DATA (be exhaustive)

### 1a. Full state from S3 (ground truth)
```bash
source .venv/bin/activate

# Download ALL state from S3
export AWS_ENDPOINT_URL="https://s3.fr-par.scw.cloud"
aws s3 cp $AWS_ENDPOINT_URL s3://trappist/state.json /tmp/trappist_state.json 2>/dev/null
aws s3 cp $AWS_ENDPOINT_URL s3://trappist/scan_history.json /tmp/trappist_scan_history.json 2>/dev/null
aws s3 sync $AWS_ENDPOINT_URL s3://trappist/logs/ /tmp/trappist_logs/ 2>/dev/null
aws s3 sync $AWS_ENDPOINT_URL s3://trappist/reports/ /tmp/trappist_reports/ 2>/dev/null

# Current live state
python trading/executor.py status
```

### 1b. Parse state.json — the richest data source
```bash
python3 -c "
import json
with open('/tmp/trappist_state.json') as f:
    d = json.load(f)

trades = d.get('trades', [])
closed = d.get('closed_trades', [])
equity_curve = d.get('equity_curve', [])
print(f'=== STATE OVERVIEW ===')
print(f'Open trades: {len(trades)}')
print(f'Closed trades: {len(closed)}')
print(f'Equity snapshots: {len(equity_curve)}')
print(f'Initial balance: {d.get(\"initial_balance\", 0)}')
print(f'Killed: {d.get(\"killed\", False)}')
print()

# Closed trade details
if closed:
    print(f'=== CLOSED TRADES (last 20) ===')
    for t in closed[-20:]:
        sig = t.get('signal_outcome', {}) or {}
        entry_sig = sig.get('entry_signals', {}) or {}
        print(f'  {t.get(\"ts\",\"\")[:16]} | {t.get(\"symbol\",\"\")} | {t.get(\"side\")} | '
              f'PnL: {t.get(\"pnl_pct\",0):+.2f}% | \${t.get(\"unrealized_pnl\",0):+.2f} | '
              f'{t.get(\"close_reason\",\"?\")} | lev={t.get(\"leverage\",1)}x | '
              f'regime={entry_sig.get(\"regime\",\"?\")} | squeeze={entry_sig.get(\"squeeze\",\"?\")}')
print()

# Open trade details with MFE/MAE
if trades:
    print(f'=== OPEN TRADES (with MFE/MAE) ===')
    for t in trades:
        print(f'  {t.get(\"symbol\",\"\")} | {t.get(\"side\")} | entry={t.get(\"entry\")} | '
              f'lev={t.get(\"leverage\",1)}x | cat={t.get(\"category\",\"?\")} | '
              f'MFE={t.get(\"mfe_pct\",0):+.2f}% | MAE={t.get(\"mae_pct\",0):+.2f}%')
    print()
    # Execution quality
    print('=== EXECUTION QUALITY ===')
    for t in trades[-10:]:
        ex = t.get('execution', {})
        if ex:
            print(f'  {t.get(\"symbol\",\"\")} | slip={ex.get(\"slippage_pct\",0):+.4f}% | '
                  f'spread={ex.get(\"spread_pct\",0):.4f}% | type={ex.get(\"order_type\",\"?\")} | '
                  f'vol24h=\${ex.get(\"volume_24h_at_entry\",0):,.0f}')
print()

# Equity curve summary
if equity_curve:
    equities = [e['equity'] for e in equity_curve if 'equity' in e]
    if equities:
        print(f'=== EQUITY CURVE ===')
        print(f'  Start: \${equities[0]:,.2f} | Current: \${equities[-1]:,.2f}')
        print(f'  Max: \${max(equities):,.2f} | Min: \${min(equities):,.2f}')
        peak = equities[0]
        max_dd = 0
        for eq in equities:
            if eq > peak: peak = eq
            dd = (eq - peak) / peak * 100
            if dd < max_dd: max_dd = dd
        print(f'  Max Drawdown: {max_dd:.2f}%')
        print(f'  Snapshots: {len(equities)} over {equity_curve[0].get(\"ts\",\"?\")} to {equity_curve[-1].get(\"ts\",\"?\")}')
"
```

### 1c. Parse scan_history.json — what we traded AND skipped
```bash
python3 -c "
import json
try:
    with open('/tmp/trappist_scan_history.json') as f:
        scans = json.load(f)
except: scans = []

print(f'=== SCAN HISTORY: {len(scans)} cycles ===')
if scans:
    trade_count = sum(s.get('trade_signals', 0) for s in scans)
    skip_count = sum(s.get('skip_signals', 0) for s in scans)
    print(f'  Total TRADE signals: {trade_count} | Total SKIP: {skip_count}')
    print(f'  Trade rate: {trade_count/(trade_count+skip_count)*100:.1f}%' if trade_count+skip_count else '')
    print()

    # Most common skipped pairs with highest scores (missed opportunities?)
    missed = {}
    for scan in scans:
        for sym, data in scan.get('pairs', {}).items():
            if data.get('action') == 'SKIP':
                score = data.get('score', 0) or 0
                if score >= 45:  # near-threshold skips
                    missed.setdefault(sym, []).append(score)
    if missed:
        print('=== NEAR-MISS SKIPS (score >= 45 but not traded) ===')
        for sym, scores in sorted(missed.items(), key=lambda x: -max(x[1]))[:10]:
            print(f'  {sym}: {len(scores)}x skipped, max score={max(scores)}, avg={sum(scores)/len(scores):.1f}')
    print()

    # Score distribution of TRADE vs SKIP
    trade_scores = []
    skip_scores = []
    for scan in scans:
        for sym, data in scan.get('pairs', {}).items():
            score = data.get('score', 0) or 0
            if data.get('action', '').startswith('TRADE'):
                trade_scores.append(score)
            elif data.get('action') == 'SKIP' and score > 0:
                skip_scores.append(score)
    if trade_scores:
        print(f'TRADE scores: min={min(trade_scores)} avg={sum(trade_scores)/len(trade_scores):.1f} max={max(trade_scores)}')
    if skip_scores:
        print(f'SKIP scores:  min={min(skip_scores)} avg={sum(skip_scores)/len(skip_scores):.1f} max={max(skip_scores)}')

    # Time-of-day pattern in scans
    print()
    print('=== SCAN TIME-OF-DAY PATTERN ===')
    for scan in scans[-10:]:
        tod = scan.get('time_of_day', {})
        thresh = scan.get('dynamic_threshold', {})
        print(f'  {scan.get(\"ts\",\"\")[:16]} | window={tod.get(\"window\",\"?\")} | '
              f'threshold={thresh.get(\"threshold\",55)} | WR={thresh.get(\"win_rate\",\"?\")}% | '
              f'trades={scan.get(\"trade_signals\",0)} skips={scan.get(\"skip_signals\",0)}')
"
```

### 1d. Signal attribution analysis — which indicators ACTUALLY predict correctly?
```bash
python3 -c "
import json
with open('/tmp/trappist_state.json') as f:
    d = json.load(f)

closed = d.get('closed_trades', [])
attributed = [t for t in closed if t.get('signal_outcome')]
print(f'=== SIGNAL ATTRIBUTION ({len(attributed)}/{len(closed)} trades have signal data) ===')
if not attributed:
    print('  No signal attribution data yet. Need more closed trades with signal_outcome.')
else:
    # Per-indicator win rate
    indicator_results = {}
    for t in attributed:
        outcome = t['signal_outcome']
        profitable = outcome.get('profitable', False)
        signals = outcome.get('entry_signals', {})
        for key, val in signals.items():
            if val is None: continue
            indicator_results.setdefault(key, {'wins': 0, 'losses': 0, 'values_win': [], 'values_loss': []})
            if profitable:
                indicator_results[key]['wins'] += 1
                if isinstance(val, (int, float)):
                    indicator_results[key]['values_win'].append(val)
            else:
                indicator_results[key]['losses'] += 1
                if isinstance(val, (int, float)):
                    indicator_results[key]['values_loss'].append(val)

    print()
    for key, data in sorted(indicator_results.items(), key=lambda x: -(x[1]['wins']+x[1]['losses'])):
        total = data['wins'] + data['losses']
        wr = data['wins'] / total * 100 if total > 0 else 0
        avg_win = sum(data['values_win'])/len(data['values_win']) if data['values_win'] else 'N/A'
        avg_loss = sum(data['values_loss'])/len(data['values_loss']) if data['values_loss'] else 'N/A'
        print(f'  {key:20s} | WR={wr:5.1f}% ({data[\"wins\"]}W/{data[\"losses\"]}L) | avg_win={avg_win} | avg_loss={avg_loss}')

    # Regime analysis
    print()
    print('=== REGIME PERFORMANCE ===')
    regime_perf = {}
    for t in attributed:
        regime = t['signal_outcome'].get('entry_signals', {}).get('regime', 'unknown')
        profitable = t['signal_outcome'].get('profitable', False)
        regime_perf.setdefault(regime, {'wins': 0, 'losses': 0})
        if profitable: regime_perf[regime]['wins'] += 1
        else: regime_perf[regime]['losses'] += 1
    for regime, data in regime_perf.items():
        total = data['wins'] + data['losses']
        wr = data['wins'] / total * 100 if total > 0 else 0
        print(f'  {regime:15s} | WR={wr:5.1f}% ({data[\"wins\"]}W/{data[\"losses\"]}L)')
"
```

### 1e. MFE/MAE analysis — are our SL/TP levels optimal?
```bash
python3 -c "
import json
with open('/tmp/trappist_state.json') as f:
    d = json.load(f)

closed = d.get('closed_trades', [])
trades_with_mfe = [t for t in d.get('trades', []) if t.get('mfe_pct', 0) != 0 or t.get('mae_pct', 0) != 0]

print(f'=== MFE/MAE ANALYSIS ===')
print(f'Trades with MFE/MAE data: {len(trades_with_mfe)}')
for t in trades_with_mfe:
    print(f'  {t.get(\"symbol\",\"\")} | MFE={t.get(\"mfe_pct\",0):+.2f}% | MAE={t.get(\"mae_pct\",0):+.2f}% | '
          f'entry={t.get(\"entry\")} | SL={t.get(\"sl\")} | TP={t.get(\"tp\")}')

# Key questions for MFE/MAE:
print()
print('KEY QUESTIONS:')
print('  1. If MAE is close to SL → SL is too tight (getting stopped out at the worst point)')
print('  2. If MFE is much higher than close PnL → TP is too tight or trailing too aggressive')
print('  3. If MFE is always small → entries are poorly timed')
print('  4. If MAE is always close to 0 → SL could be tighter (reducing risk)')
"
```

### 1f. Execution quality analysis — slippage and fees at scale
```bash
python3 -c "
import json
with open('/tmp/trappist_state.json') as f:
    d = json.load(f)

trades = d.get('trades', [])
with_exec = [t for t in trades if t.get('execution')]
print(f'=== EXECUTION QUALITY ({len(with_exec)} trades with data) ===')
if with_exec:
    slippages = [t['execution'].get('slippage_pct', 0) for t in with_exec]
    spreads = [t['execution'].get('spread_pct', 0) for t in with_exec]
    market_orders = sum(1 for t in with_exec if t['execution'].get('order_type') == 'market')
    limit_orders = sum(1 for t in with_exec if t['execution'].get('order_type') == 'limit')
    print(f'  Avg slippage: {sum(slippages)/len(slippages):.4f}%')
    print(f'  Max slippage: {max(slippages):.4f}%')
    print(f'  Avg spread: {sum(spreads)/len(spreads):.4f}%')
    print(f'  Market orders: {market_orders} | Limit orders: {limit_orders}')
    print()
    print('  AT 1M EUR SCALE:')
    avg_slip = sum(slippages)/len(slippages)/100
    print(f'    Slippage cost per trade: EUR {1000000 * 0.10 * avg_slip:,.2f} (10% position)')
    print(f'    Annual slippage (500 trades): EUR {500 * 1000000 * 0.10 * avg_slip:,.2f}')
    avg_spread = sum(spreads)/len(spreads)/100
    print(f'    Spread cost per trade: EUR {1000000 * 0.10 * avg_spread:,.2f}')
"
```

### 1g. Session metrics and infra costs
```bash
python3 -c "
import json, glob
metrics = []
for f in sorted(glob.glob('/tmp/trappist_logs/session_metrics_*.json')):
    with open(f) as fh:
        m = json.load(fh)
        metrics.append(m)
total_cost = sum(m.get('total_cost_usd', 0) for m in metrics)
total_turns = sum(m.get('total_turns', 0) for m in metrics)
cycle_count = sum(1 for m in metrics if m.get('run_type') == 'cycle')
success = sum(1 for m in metrics if m.get('exit_code', 1) == 0)
print(f'=== INFRA METRICS ===')
print(f'  Total sessions: {len(metrics)} ({cycle_count} cycles)')
print(f'  Success rate: {success/len(metrics)*100:.1f}%' if metrics else '')
print(f'  Total API cost: \${total_cost:.2f} (unlimited plan = \$0 actual)')
print(f'  Avg turns/cycle: {total_turns/max(cycle_count,1):.1f}')
"
```

### 1h. Web research for market context and new edges
Search for:
- WebSearch "crypto futures trading strategy 2026"
- WebSearch "binance futures most profitable indicators"
- WebSearch "crypto liquidation cascade trading strategy"
- WebSearch "funding rate arbitrage strategy"
- Use `mcp__gloria-news__get_news_recap` for narrative shifts

---

## PHASE 2 — DEEP ANALYSIS (forensic level)

### Overall P&L
- Total realized P&L, unrealized, net
- Win rate, profit factor, Sharpe approximation
- Max drawdown from equity curve (not just endpoint)
- **Profit per cycle** — are we improving over time?

### Per-Category Performance (Kelly calibration)
- Win rate and payoff per category — does it match `_CATEGORY_EDGE` in indicators.py?
- If real data diverges from hardcoded values → **UPDATE _CATEGORY_EDGE**
- Calculate actual Half-Kelly for each category

### Signal Attribution (the gold mine)
- Per-indicator win rate: which signals actually predict correctly?
- If RSI > 55 at entry → what's the win rate? If squeeze = True → what's the win rate?
- **This directly translates to _SIGNAL_WEIGHTS adjustments**

### MFE/MAE Analysis (SL/TP optimization)
- Are trades hitting MAE close to SL before recovering? → SL too tight
- Are trades hitting large MFE then reverting? → TP too tight or trail not working
- What's the optimal TP based on actual MFE distribution?

### Execution Quality (critical at 1M€ scale)
- Average slippage per trade → project to 1M€
- Spread costs → project to 1M€
- Market vs limit order fill rates
- Volume requirements for 1M€ positions without market impact

### Time-of-Day Patterns
- Confirm/update golden hours (02:00-09:00 UTC)
- Confirm/update caution hours (12:00-18:00 UTC)
- Should we adjust score_adj and size_mult?

### Scan Efficiency
- What % of TRADE signals were actually executed?
- What happened to near-threshold SKIP signals — would they have been profitable?
- Should the threshold be higher or lower?

---

## PHASE 3 — PARAMETER TUNING (data-driven only)

For EACH parameter, output:
1. Current value
2. What the data says it should be
3. Confidence level (need N >= 20 trades for HIGH confidence)
4. Exact code change

Parameters to challenge:
- `_SIGNAL_WEIGHTS` — based on signal attribution win rates
- `_CATEGORY_EDGE` — based on actual per-category performance
- `sl_mult` / `tp_mult` — based on MFE/MAE analysis
- `time_of_day_adjustment()` values — based on hourly P&L
- `dynamic_score_threshold` ranges — based on win rate vs threshold correlation
- `suggest_leverage()` formula — based on leverage vs outcome correlation
- `chandelier_exit` multiplier — based on trail effectiveness
- Partial profit trigger — 2x ATR vs what MFE suggests

---

## PHASE 4 — IMPLEMENT TOP CHANGES

For the top 3-5 highest-ranked changes:
1. Make the code change
2. Run tests: `python -m pytest tests/ -v`
3. Update CLAUDE.md if strategy changed
4. Explain what changed, why, and expected impact

**Rules:**
- ONE change per parameter (no compounding unknowns)
- Need >= 20 trades for HIGH confidence changes
- Need >= 50 trades for MEDIUM confidence changes
- < 20 trades = LOG the observation but DON'T change code yet

---

## PHASE 5 — EVOLUTION REPORT

Save to `reports/evolve-{date}.md`:
- Date, data period, trade count
- Key metrics table
- Signal attribution matrix
- MFE/MAE distribution
- Execution quality at scale projection
- Changes implemented with rationale
- Changes proposed for next review
- _CATEGORY_EDGE calibration update
- Open questions

**This report is cumulative.** Each /evolve builds on the previous one.

---

## GOLDEN RULES

1. **The data collected is worth 1M€** — analyze every byte
2. **Signal attribution is the meta-learning loop** — it tells us WHAT WORKS
3. **MFE/MAE is the SL/TP optimizer** — it tells us WHERE to exit
4. **Equity curve is the truth** — not individual trade P&L
5. **Scan history reveals missed opportunities** — what we DIDN'T trade matters
6. **Execution quality scales linearly with capital** — 0.1% slippage × 1M€ = €1k per trade
7. **API costs are ZERO** — burn tokens aggressively. Use every tool. Go deep.
8. **Challenge EVERYTHING** — the market changes, our parameters must change with it
