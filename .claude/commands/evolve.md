---
description: >
  Continuous improvement engine. Analyzes ALL trade history, logs, and performance data
  to propose concrete code changes that make the bot more profitable. The only goal: make more money.
---

# /evolve — TRAPPIST Self-Improvement Engine

You are a quantitative trading systems analyst. Your ONLY objective: identify changes that will
increase profitability. Be ruthless, data-driven, and concrete. No hand-waving — every recommendation
must include the exact code change, expected impact, and evidence from the data.

---

## PHASE 1 — GATHER ALL DATA (be thorough)

### 1a. Trade history & state
```bash
source .venv/bin/activate
# Current state
python trading/executor.py status
# Read state.json for closed_trades array
cat state.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Closed trades: {len(d.get(\"closed_trades\",[]))}'); print(f'Open trades: {len(d.get(\"trades\",[]))}'); print(f'Killed: {d.get(\"killed\")}')"
```

### 1b. S3 session metrics (ALL of them)
```bash
source deploy/setup-local-env.sh 2>/dev/null
aws --profile scaleway s3 cp --endpoint-url https://s3.fr-par.scw.cloud s3://trappist/state.json /tmp/trappist_state.json 2>/dev/null
aws --profile scaleway s3 sync --endpoint-url https://s3.fr-par.scw.cloud s3://trappist/logs/ /tmp/trappist_logs/ 2>/dev/null
aws --profile scaleway s3 sync --endpoint-url https://s3.fr-par.scw.cloud s3://trappist/reports/ /tmp/trappist_reports/ 2>/dev/null
```

### 1c. Binance trade history (ground truth)
Use the MCP binance-futures tools:
- `get_trade_history` for each symbol that appears in state
- `get_order_history` to see filled vs cancelled orders
- `get_positions` for current state

### 1d. Read ALL session metrics
```bash
python3 -c "
import json, glob, os
metrics = []
for f in sorted(glob.glob('/tmp/trappist_logs/session_metrics_*.json')):
    with open(f) as fh:
        m = json.load(fh)
        metrics.append(m)
total_cost = sum(m.get('total_cost_usd', 0) for m in metrics)
total_turns = sum(m.get('total_turns', 0) for m in metrics)
cycle_count = sum(1 for m in metrics if m.get('run_type') == 'cycle')
print(f'Sessions: {len(metrics)} ({cycle_count} cycles)')
print(f'Total cost: \${total_cost:.2f}')
print(f'Avg turns/cycle: {total_turns/max(cycle_count,1):.1f}')
for m in metrics[-5:]:
    print(f'  {m[\"timestamp\"]} | {m[\"run_type\"]} | exit={m[\"exit_code\"]} | \${m.get(\"total_cost_usd\",0):.2f} | {m.get(\"total_turns\",0)} turns')
"
```

### 1e. Read recent cycle logs for trade reasoning
```bash
# Read the last 3 cycle logs to understand what Claude decided and why
ls -t /tmp/trappist_logs/cycle-*.log 2>/dev/null | head -3
```
For each log, extract: trades placed, reasoning, scores, pairs scanned.

---

## PHASE 2 — PERFORMANCE ANALYSIS (be forensic)

Compute these metrics from the data gathered:

### Overall P&L
- **Total realized P&L** (sum of closed trades)
- **Total unrealized P&L** (current positions)
- **Win rate** (wins / total closed trades)
- **Average winner** vs **average loser** (in $ and %)
- **Profit factor** (gross profit / gross loss)
- **Sharpe ratio approximation** (mean return / std deviation)
- **Max drawdown** (peak to trough)
- **Total API cost** vs **total trading profit** (are we profitable net of costs?)

### Per-category breakdown
- Which categories make money? Which lose?
- Which categories have best win rate?
- Which should be removed or added?

### Per-pair breakdown
- Top 5 most profitable symbols
- Top 5 biggest losers
- Any symbol that was traded but NEVER won?

### Temporal patterns
- Are certain hours more profitable?
- Weekend vs weekday performance?
- How long do winning trades last vs losing trades?

### Entry quality
- Average score of winning trades vs losing trades
- Are Tier A trades actually better than Tier B?
- Multi-TF confirmation: does it actually improve win rate?

### Risk management
- How many trades hit SL vs TP vs manual close?
- Average R/R achieved vs target R/R
- Trail effectiveness: how much extra profit does trailing capture?

---

## PHASE 3 — CHALLENGE EVERY PARAMETER

For each parameter, ask: "Does the data support this value, or is there a better one?"

### Signal weights (_SIGNAL_WEIGHTS in indicators.py)
```python
# Current weights:
"ema_trend": (+2, -2), "ema_crossover": (+3, -3), "macd_hist": (+2, -2),
"macd_cross": (+3, -3), "rsi_zone": (+1, -1), "bollinger": (+1, -2),
"volume": (+2, -1), "price_sma200": (+2, -2)
```
- Are MACD signals actually predictive? Or just noise?
- Is RSI weight too low? Too high?
- Should Bollinger have more weight given squeeze setups are best R/R?

### Score thresholds
- Tier A: >60 — is this too conservative? Too aggressive?
- Tier C: <50 — are we skipping profitable setups?
- What score did the ACTUAL winning trades have?

### ATR multipliers
- SL at 2.0× ATR — are we getting stopped out too often? Or not tight enough?
- TP at 4.0× ATR — are we leaving money on the table? Or is this never hit?
- Chandelier 3.0× ATR — is this optimal for crypto volatility?

### Regime thresholds (ADX)
- ADX > 25 = trending — should this be 20? 30?
- ADX < 20 = skip — are we missing profitable ranging setups?

### Risk per trade
- 2% Half-Kelly — should it be 1.5%? 3%? What does the win rate suggest?
- Optimal Kelly% = WinRate - (1 - WinRate) / AvgWin:AvgLoss

### Leverage formula
- Safety factor 0.5 — should it be more aggressive (0.7) or conservative (0.3)?
- Are the suggested leverages correlating with trade outcomes?

### Cooldown
- 60 minutes — too long? Too short?
- Are there cases where re-entry would have been profitable?

### Position limits
- 8 max positions — could we handle more with proper sizing?
- 75% max exposure — is this ever reached?
- 3 per category — too restrictive?

---

## PHASE 4 — RESEARCH & INNOVATION

### Web search for latest edge
Search for:
- "crypto futures trading strategy 2026"
- "binance futures profitable indicators"
- "quantitative crypto trading signals"
- "best technical indicators crypto futures"
- "machine learning crypto trading signals"

### Community intelligence
Use CryptoPanic and Gloria news to understand:
- What narratives are driving markets RIGHT NOW?
- Are there new categories emerging (AI agents, RWA, DePIN)?
- Should we add narrative-based scoring?

### Skills & tools check
- Are there new MCP servers or tools that could give us an edge?
- New data sources? On-chain metrics? Whale tracking?
- Sentiment analysis improvements?

---

## PHASE 5 — RANKED RECOMMENDATIONS

Produce a **ranked list** of proposed changes. For each:

```
### [RANK] [TITLE]
**Expected impact**: +X% win rate / +$Y per month / -Z% drawdown
**Evidence**: [data from Phase 2]
**Confidence**: HIGH / MEDIUM / LOW
**Effort**: [files to change, lines affected]
**Change**:
- File: `trading/indicators.py`
- Current: `"macd_cross": (+3, -3)`
- Proposed: `"macd_cross": (+4, -4)`
- Rationale: MACD cross signals predicted 72% of winning trades
```

### Priority rules:
1. **HIGH confidence + HIGH impact** → implement immediately
2. **HIGH confidence + LOW impact** → implement if easy
3. **LOW confidence + HIGH impact** → A/B test first (paper trade)
4. **LOW confidence + LOW impact** → skip

---

## PHASE 6 — IMPLEMENT TOP CHANGES

For the top 3-5 highest-ranked changes:
1. Make the code change
2. Explain what changed and why
3. Update CLAUDE.md if strategy/limits changed

**DO NOT** change multiple parameters at once without clear evidence.
One change at a time = one clear signal about what works.

**DO NOT** over-optimize on small sample sizes.
Need ≥20 trades before drawing conclusions about a parameter.

---

## PHASE 7 — WRITE EVOLUTION REPORT

Save a report to `reports/evolve-{date}.md` with:
- Date and data coverage period
- Key metrics snapshot
- Top findings
- Changes implemented
- Changes proposed for next review
- Open questions / things to monitor

This report becomes input for the NEXT /evolve run. Cumulative learning.

---

## GOLDEN RULES

1. **Net profitability is ALL that matters** — win rate, Sharpe, drawdown are means, not ends
2. **The market changes** — what worked last month may not work now. Adapt.
3. **Cost matters** — $0.60/cycle × 24 cycles/day = $14.40/day. Must make more than that.
4. **Small sample = low confidence** — don't overfit to 5 trades
5. **The best trade is often no trade** — reducing bad trades > adding good trades
6. **Compound improvements** — 1% better per week = 67% better per year
7. **Challenge EVERYTHING** — sacred cows get slaughtered by the market
