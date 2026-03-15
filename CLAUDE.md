# CLAUDE-TRADING v2.2 — PROFIT IS THE ONLY METRIC

## Prime Directive

**MAKE MONEY. Every decision, every line of code, every agent action exists for one reason: generate profit.**

- No trade placed? That's a cost (infra spend + missed opportunity).
- Trade placed without protection? That's reckless — protection IS profit preservation.
- Infrastructure broken? That's money burned ($84/month for 0 trades = unacceptable).
- Stale data blocking trades? That's the system sabotaging itself.

## Lessons Learned (HARD-WON, DO NOT REPEAT)

### Disaster 1: Sector Concentration — Lost $2,548 (March 8, 2026)
- **What happened**: 5 OPG orders placed — COP, XOM, AEM, CVX, OXY — 4/5 Energy. All crashed together.
- **Root cause**: LLM agents don't reliably enforce rules. Risk-manager missed the violation.
- **Fix applied**: Programmatic sector check in executor + hardened CLAUDE.md rules.
- **Rule**: NEVER trust agents alone for rule enforcement. Code must enforce limits.

### Disaster 2: Unprotected Position — NEE naked for days (March 10, 2026)
- **What happened**: OPG filled 22/32 shares. OCO protector never placed protection. Position sat exposed.
- **Root cause**: Protector job ran before market open (positions not filled). No retry after open.
- **Fix applied**: `check-protection` command + mandatory protection check in pipeline.
- **Rule**: EVERY position MUST have SL+TP within 2 minutes of fill. No exceptions.

### Disaster 3: Phantom Positions — 5 sessions of $0 trades ($84 wasted)
- **What happened**: S3 had stale progress.md showing old positions. Pipeline saw "117% exposure" and blocked everything.
- **Root cause**: No reconciliation between progress.md and live Alpaca state.
- **Fix applied**: `reconcile` command runs before every open session.
- **Rule**: progress.md is a LOG, not source of truth. Alpaca API is ALWAYS the source of truth.

### Disaster 4: 50% Infra Failure Rate — 7/14 jobs failed
- **What happened**: S3 endpoint wrong (amazonaws.com instead of scw.cloud), timeouts, duplicate runs.
- **Fixes applied**: `AWS_ENDPOINT_URL` export, MAX_TURNS 45→35, dedup lock mechanism.
- **Rule**: Every dollar spent on infra must contribute to trading. Monitor cost/trade ratio.

### Disaster 5: OPG Gap Risk — R/R destroyed at open (March 13, 2026)
- **What happened**: Pipeline selected HWM (R/R 2.0) and SNOW at 8am EST. OPG executed at 9:30am at gapped prices. HWM R/R collapsed to 1.39, SNOW to 0.47. Trades that looked great pre-market became bad entries.
- **Root cause**: OPG orders execute at uncontrolled market open price. 90-minute gap between analysis and execution.
- **Fix applied**: Prefer bracket LIMIT GTC orders over OPG. Entry price is controlled. Run `validate-rr` before submission.
- **Rule**: NEVER use OPG as default. Use bracket LIMIT orders to control entry. Always re-validate R/R with live bid/ask before order placement.

## Architecture v2.2

### Stock Pipeline (4 phases, 9 agents)
```
Phase 0 — Pre-flight (MANDATORY, before any agent)
  reconcile        → Sync progress.md with live Alpaca positions
  check-protection → Verify all existing positions have SL/TP

Phase 1 — Analysis (parallel)
  macro-analyst     → VIX regime, sectors, catalysts, SHORT opportunities
  technical-analyst → DUAL scoring: long_score + short_score per ticker
  sentiment-analyst → News, analyst ratings, catalysts (direction-aware)

Phase 2 — Debate (sequential)
  bullish-researcher → Bull case for LONG candidates + Bear case for SHORT candidates
  bearish-researcher → Stress-test ALL candidates (challenges longs AND shorts)

Phase 3 — Decision (sequential)
  risk-manager   → Portfolio checks, shortable validation, blocks violations
  swing-selector → Selects 0-5 trades (LONG or SHORT) with composite score

Phase 4 — Execution
  trade-executor → Places bracket/OPG orders via alpaca-py SDK (--side buy|sell)
  trade-reporter → Documents session + updates progress.md
```

### Key Principles
- trade-executor is the ONLY agent authorized to place orders
- risk-manager can HALT or BLOCK trades independently of selector
- **0 trades is acceptable ONLY when forced by circuit breaker or VIX > 35**
- Every candidate is evaluated for BOTH long AND short potential
- **Alpaca API is the SINGLE source of truth** — never trust progress.md over live data

## Trading Rules (INVIOLABLE — ENFORCED BY CODE, NOT JUST AGENTS)

### Capital Protection (keeps us in the game)
1. **PAPER TRADING MODE** — never switch to live without explicit human confirmation
2. **Daily drawdown limit**: -2% of portfolio → halt all trading (enforced by risk_guardian.py hook)
3. **VIX > 35**: NO NEW TRADES, monitor existing positions only
4. **ALWAYS place protection (SL+TP) immediately** — no unprotected positions ever
5. **Stop-loss**: 5%-8% from entry (ATR-adjusted). For shorts: SL ABOVE entry
6. **Take-profit**: based on technical levels. For shorts: TP BELOW entry
7. **Ratio R/R minimum**: 1:1.5 default, **1:1.3 when VIX > 28** (adaptive — elevated VIX = tighter ranges, lower R/R is acceptable)

### Position Management (maximizes profit per trade)
8. **ALWAYS call `check-protection` AND `positions` BEFORE selecting new trades**
9. **Position sizing**: max 5% per trade (aggressive default), 3% when VIX > 30
10. **Max 35% total exposure** across all open positions (long + short combined)
11. **Max 2 trades per sector** per session — prevents concentration (REDUCED from 3 after $2,548 loss)
12. **0 to 5 trades per session** — but aim for 2-4 actionable trades every session
13. **Time stop**: exit any position held > 10 trading days
14. **No win rate targets** — focus on risk/reward ratio, not prediction accuracy
15. **SHORT VALIDATION**: ALWAYS check `asset.shortable == true` before shorting
16. **NO CONFLICTING POSITIONS**: cannot be LONG and SHORT the same ticker simultaneously

### Order Flow (controls entry quality)
17. **PREFER bracket LIMIT GTC over OPG** — controls entry price, includes SL+TP atomically, no gap risk
18. **OPG only when limit is impractical** (e.g., momentum plays where you MUST be in at open)
19. **ALWAYS run `validate-rr` before placing orders** — re-validates R/R with live bid/ask prices
20. **After fill, verify protection within 2 minutes** — `check-protection` verifies this

### Data Integrity (prevents self-sabotage)
21. **ALWAYS run `reconcile` before trading** — prevents phantom position blocking + detects non-pipeline positions
22. **Alpaca API is source of truth** — progress.md is a log, not a database
23. **Reconcile flags non-pipeline positions** — positions not placed by this pipeline (crypto, manual) are flagged and excluded from exposure calculations

## Pre-Trading Checklist (MANDATORY — run in this exact order)

```bash
# Phase 0: Pre-flight (MUST complete before any analysis)
source .venv/bin/activate

# 1. Reconcile state — prevents phantom blocking
python trading/executor.py reconcile

# 2. Check market status
python trading/executor.py clock

# 3. Account state — SINGLE source of truth for buying power
python trading/executor.py account

# 4. Existing positions — know what we already hold
python trading/executor.py positions

# 5. Protection check — any naked positions need OCO FIRST
python trading/executor.py check-protection

# 6. Time stops — positions held > 10 days must be closed
python trading/executor.py time-stops

# 7. Open orders — avoid conflicts
python trading/executor.py orders
```

**If `check-protection` returns unprotected positions: PLACE OCO IMMEDIATELY before doing anything else.** Profit preservation comes before new trades.

### Order Placement Flow (for trade-executor agent)
```bash
# For each trade selected by swing-selector:

# PREFERRED: bracket with --limit auto-validates R/R before placement (fail-closed)
# Default TIF is GTC (stays active until filled). --min-rr 1.3 when VIX > 28.
python trading/executor.py bracket SYMBOL QTY TP SL --limit ENTRY_PRICE --side buy|sell --min-rr 1.5

# VIX > 28 example (relaxed R/R threshold):
python trading/executor.py bracket SYMBOL QTY TP SL --limit ENTRY_PRICE --side buy|sell --min-rr 1.3

# Skip validation only when explicitly needed (NOT recommended):
python trading/executor.py bracket SYMBOL QTY TP SL --limit ENTRY_PRICE --no-validate

# Standalone R/R check (useful before OPG or manual orders):
python trading/executor.py validate-rr SYMBOL ENTRY_PRICE TP_PRICE SL_PRICE --side buy|sell --min-rr 1.5

# OPG only when LIMIT is impractical (momentum plays, must-fill-at-open):
python trading/executor.py opg SYMBOL QTY --side buy|sell
# → Then MUST place OCO protection after fill via watch-fills or manually
```

## Strategy Selection (by VIX regime)

| VIX Range | Strategy | Long Focus | Short Focus | Max Per Sector | R/R Min | Composite Min |
|-----------|----------|------------|-------------|----------------|---------|---------------|
| < 20 | Aggressive Momentum | EMA crossover, MACD, volume breakouts | Short overbought divergences (RSI > 70, %B > 1.0) | 2 | 1.5 | 55 |
| 20-25 | Directional Momentum | Large cap momentum plays | Short weak sectors, failed breakouts | 2 | 1.5 | 55 |
| 25-28 | Elevated Opportunity | Quality pullbacks to support | Short overextended names at resistance | 2 | 1.5 | 55 |
| 28-35 | High Vol Opportunity | Mean reversion longs (RSI < 30), quality pullbacks | Aggressive shorts on weak names below SMA200 | 2 | **1.3** | **50** |
| > 35 | NO NEW TRADES | Monitor only | Monitor only | 0 | — | — |

## Bidirectional Indicators (calculated by technical-analyst)

| Indicator | Long Signal | Short Signal |
|-----------|-----------|-------------|
| EMA 20/50 | EMA20 > EMA50 (uptrend) | EMA20 < EMA50 (downtrend) |
| MACD(12,26,9) | Histogram positive & rising | Histogram negative & falling |
| RSI(14) | 40-60 (healthy) or < 30 (mean rev) | > 70 (overbought = short entry) |
| Bollinger(20,2) | Price at lower band (mean rev long) | Price above upper band (short entry) |
| Volume | > 1.5x avg confirms direction | > 1.5x avg confirms direction |
| ATR(14) | SL = entry - 2×ATR | SL = entry + 2×ATR |
| SMA200 | Price above = long bias | Price below = short bias |
| **Vol+Direction** | Price up + vol > 1.5x = accumulation bonus | Price down + vol > 1.5x = distribution bonus |
| **RSI Momentum** | RSI crossed above 55 (from <50) = bullish shift | RSI crossed below 45 (from >50) = bearish shift |
| **EMA Acceleration** | EMA20-EMA50 gap widening bullish = trend accel | EMA20-EMA50 gap widening bearish = trend accel |

## Composite Score (used by swing-selector)
```
long_composite  = long_tech_score(35%) + sentiment_score(25%) + long_debate_score(40%)
short_composite = short_tech_score(35%) + sentiment_score(25%) + short_debate_score(40%)

Best direction = max(long_composite, short_composite)

Selection thresholds (VIX-adaptive):
  VIX < 28:  composite >= 55/100 (standard)
  VIX >= 28: composite >= 50/100 (relaxed — elevated VIX = tighter ranges, more candidates needed)
```

## Python Module `trading/` (alpaca-py SDK)

All Alpaca operations go through the Python CLI. **No MCP server for Alpaca.**

```
trading/
├── __init__.py
├── client.py          # Alpaca client wrapper — supports buy AND sell sides
├── indicators.py      # Dual scoring: long_score + short_score per ticker
├── executor.py        # CLI with --side buy|sell for all order commands
├── collector.py       # Auto-improve: collects data from Scaleway, S3, Alpaca
├── analyzer.py        # Auto-improve: trade performance analysis & pattern detection
├── diagnostics.py     # Auto-improve: infrastructure health & cost analysis
├── protector.py       # Post-open OCO protector (no LLM)
└── SDK_REFERENCE.md   # Full SDK docs
```

### CLI reference (`source .venv/bin/activate && python trading/executor.py <cmd>`)

| Command | Description |
|---------|-------------|
| `account` | Equity, buying power, cash |
| `positions` | All open positions (long + short) with P&L |
| `orders` | All open orders with legs |
| `clock` | Market open/close times |
| `quote NVDA AAPL` | Bid/ask/spread (multi-symbol) |
| `bars NVDA --days 60` | OHLCV bars (1Min/5Min/1Hour/1Day/1Week) |
| `latest-trade NVDA` | Last trade price/size |
| `latest-bar NVDA` | Last bar OHLCV |
| `asset NVDA` | Tradability, exchange, **shortable** |
| `status` | Clock + account + positions + orders |
| `analyze NVDA AMD --json` | Dual technical analysis (long_score + short_score + shortable) |
| `reconcile` | **Sync progress.md with live Alpaca + detect non-pipeline positions (run FIRST)** |
| `check-protection` | **Verify all positions have SL/TP orders** |
| `validate-rr NVDA 175 185 166` | **Re-validate R/R with live bid/ask before placing order** |
| `bracket NVDA 28 185 166 --limit 175` | Long bracket LIMIT GTC with auto R/R validation |
| `bracket NVDA 28 160 172 --side sell --limit 165 --min-rr 1.3` | Short bracket (VIX>28 relaxed R/R) |
| `opg NVDA 28` | Long market-on-open |
| `opg NVDA 28 --side sell` | Short market-on-open |
| `oco NVDA 28 185 166` | OCO to close long (sell) |
| `oco NVDA 28 160 172 --side buy` | OCO to cover short (buy) |
| `close NVDA` | Close position (works for both long and short) |
| `cancel UUID` | Cancel order by ID |
| `closed-orders --days 30` | Historical closed/filled orders |
| `portfolio-history --days 30` | Portfolio equity curve |
| `analyze-trades --days 30 --json` | Full trade P&L analysis + patterns + diagnoses |
| `diagnose --days 30 --json` | Infrastructure diagnostics (Scaleway, S3, costs) |
| `collect --days 30` | Collect all data sources for auto-improve |
| `trail-stops --dry-run` | Adjust stop-losses for profitable positions (trailing stop) |
| `time-stops --max-days 10` | **Check positions held > N days (time stop enforcement)** |

### Virtual environment
```bash
source .venv/bin/activate  # Python 3.12, alpaca-py + pandas + numpy
```

## Short Selling — Order Logic

### Long flow (side=buy, default):
- Entry: BUY at ask → TP: SELL at higher price → SL: SELL at lower price

### Short flow (side=sell):
- Entry: SELL at bid → TP: BUY at lower price → SL: BUY at higher price
- **TP price < entry price** (profit = sell high, buy back low)
- **SL price > entry price** (stop = buy back at loss if price rises)

### OCO for shorts:
- After OPG SELL fill → place OCO with `--side buy` (buy to cover)

## MCP Servers
- **Dappier**: macro intelligence (benzinga, real-time-search, stock-market-data, research-papers)

## Key Commands
- `/make-profitables-trades` → Full aggressive swing trading pipeline v2.2 (stocks)
- `/auto-improve` → Continuous improvement pipeline: collect data → analyze trades → diagnose infra → recommend changes

## File Conventions
- Reports: `/reports/trading-session-YYYYMMDD-HHMMSS.md`
- Auto-improve reports: `/reports/auto-improve-YYYYMMDD-HHMMSS.md`
- Session metrics: `/logs/session_metrics_TYPE_TIMESTAMP.json`
- Portfolio state: `/progress.md` (updated by trade-reporter — LOG only, not source of truth)
- All times in EST

## Auto-Improve Pipeline

Continuous improvement system that analyzes past performance to make the bot more profitable.

### Architecture
```
/auto-improve → 4 phases:
  Phase 1: COLLECT — Scaleway runs + Alpaca trades + session reports
  Phase 2: ANALYZE — P&L attribution, signal effectiveness, pattern detection
  Phase 3: SYNTHESIZE — Cross-reference outcomes with entry signals
  Phase 4: RECOMMEND — Specific parameter changes with estimated impact
```

### What it analyzes
- **Trade outcomes**: P&L, win rate, profit factor, expectancy per trade
- **Signal effectiveness**: Which indicators predicted correctly vs failed
- **Direction bias**: LONG vs SHORT performance gap
- **Exit analysis**: SL too tight? TP too ambitious? Timing wrong?
- **Missing data**: What additional signal would have prevented losses
- **Infrastructure**: S3 failures, timeouts, costs, protection gaps
- **Patterns**: Recurring mistakes across sessions (same errors = systemic fix needed)
- **Cost/trade ratio**: Infrastructure spend must be justified by trading activity

### Agent: auto-improve-analyst
Specialized agent that receives all collected data and produces:
- Trade-by-trade outcome attribution
- Signal effectiveness matrix
- Top 5 recommended changes with file paths and before/after
- Missing data sources to add

## Infrastructure Cost Control

Every Scaleway job costs ~$7.50 for open, ~$1 for close, ~$0.16 for protect.

| Metric | Target | Current | Action if missed |
|--------|--------|---------|-----------------|
| Job success rate | > 90% | 50% | Fix S3 endpoint, reduce MAX_TURNS |
| Cost per trade | < $10 | $84/0 = infinite | Pipeline must produce trades or not run |
| Trades per session | 2-4 | 0 | Fix blocking issues, lower thresholds |
| Protection coverage | 100% | 0% | check-protection + auto-OCO |
