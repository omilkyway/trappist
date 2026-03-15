# TRAPPIST v2.0 — Crypto Futures Trading Bot

> **PROFIT IS THE ONLY METRIC. Every decision exists for one reason: generate profit.**

## Prime Directive

- No trade placed? Missed opportunity on a 24/7 market.
- Trade placed without protection? Reckless — protection IS profit preservation.
- Infrastructure broken? Money burned for nothing.
- If the market moves, we capture it — LONG or SHORT. Always.

## Architecture

### Exchange: Binance Futures (USDT-M) via CCXT
- **Testnet** by default (`--sandbox`). Set `LIVE_MODE=true` in `.env` for live.
- Auth: `keys.local.json` or env vars `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- All operations via Python `trading/executor.py` CLI

### MCP Servers (4 verified, available for interactive analysis)
- **binance-futures**: Binance USDT-M Futures API — prices, positions, orders, klines, leverage
  - Read: `get_ticker`, `get_positions`, `get_balance`, `get_klines`, `get_order_book`
  - Write: `place_order`, `cancel_order`, `set_leverage`, `set_margin_type`
- **fear-greed**: Crypto Fear & Greed Index (0-100) — regime detection
  - `get_current_fng_tool`, `get_historical_fng_tool`, `analyze_fng_trend`
- **gloria-news**: Gloria AI curated crypto news — 19 categories (bitcoin, ethereum, defi, ai, solana...)
  - `get_latest_news`, `get_news_recap`, `search_news`, `get_ticker_summary`
- **tradingview**: TradingView technical indicators — RSI, MACD, EMA, SMA, Bollinger, Stochastic, pivots
  - `get_indicators`, `get_specific_indicators`, `get_historical_data`

### Trading Pipeline (4 phases, 9 agents)
```
Phase 0 — Pre-flight (MANDATORY)
  reconcile        → Sync progress.md with live Binance positions
  check-protection → Verify all positions have SL/TP

Phase 1 — Analysis (parallel)
  macro-analyst     → Fear & Greed, BTC dominance, funding rates, regime
  technical-analyst → DUAL scoring: long_score + short_score per pair
  sentiment-analyst → News, social momentum, funding rate sentiment

Phase 2 — Debate (sequential)
  bullish-researcher → Bull case for LONG + Bear case for SHORT candidates
  bearish-researcher → Stress-test ALL candidates

Phase 3 — Decision (sequential)
  risk-manager   → Portfolio checks, category limits, blocks violations
  swing-selector → Selects 0-5 trades (LONG or SHORT) with composite score

Phase 4 — Execution
  trade-executor → Places bracket orders via executor.py CLI
  trade-reporter → Documents session + updates progress.md
```

## Trading Rules (INVIOLABLE — ENFORCED BY CODE)

### Capital Protection
1. **TESTNET MODE** by default — never switch to live without explicit confirmation
2. **Drawdown limit**: -5% from initial balance → halt all trading (risk_guardian.py hook)
3. **Fear & Greed < 10**: Reduce position size 50%, conservative entries only
4. **ALWAYS place protection (SL+TP)** — no unprotected positions ever
5. **Stop-loss**: 1-3 ATR from entry (volatility-adjusted)
6. **Take-profit**: Based on technical levels, min R/R 1:1.5
7. **R/R minimum**: 1:1.5 default, **1:1.3 when F&G < 25** (adaptive)

### Position Management
8. **ALWAYS run `reconcile` AND `check-protection` BEFORE trading**
9. **Position sizing**: max 2% risk per trade, max 5% capital per position
10. **Max 40% total exposure** across all positions (long + short combined)
11. **Max 2 trades per category** — prevents concentration
12. **0 to 5 trades per session** — aim for 2-4 every session
13. **Time stop**: exit positions held > 10 days
14. **No win rate targets** — focus on R/R ratio
15. **NO CONFLICTING POSITIONS**: cannot be LONG and SHORT same pair simultaneously
16. **Max leverage**: 10x (adjustable per pair, default 5x)

### Order Flow
17. **PREFER bracket LIMIT orders** — controls entry price
18. **ALWAYS validate R/R before placing** — `validate-rr` command
19. **After fill, verify protection** — `check-protection`
20. **24/7 market** — no OPG orders needed, trade anytime

### Data Integrity
21. **ALWAYS run `reconcile` before trading** — prevents phantom blocking
22. **Binance API is source of truth** — progress.md is a log, not a database

## Pre-Trading Checklist (MANDATORY)

```bash
# Phase 0: Pre-flight
source .venv/bin/activate

# 1. Reconcile state
python trading/executor.py reconcile

# 2. Account state
python trading/executor.py account

# 3. Existing positions
python trading/executor.py positions

# 4. Protection check — naked positions need SL/TP FIRST
python trading/executor.py check-protection

# 5. Time stops — positions held > 10 days must be reviewed
python trading/executor.py time-stops

# 6. Open orders
python trading/executor.py orders
```

**If `check-protection` returns unprotected: PLACE PROTECTION IMMEDIATELY.**

### Order Placement Flow
```bash
# PREFERRED: bracket with --limit validates R/R before placement
python trading/executor.py bracket BTC/USDT:USDT 0.01 95000 88000 --limit 90000 --side buy --leverage 5 --min-rr 1.5

# SHORT example:
python trading/executor.py bracket BTC/USDT:USDT 0.01 85000 92000 --limit 90000 --side sell --leverage 5

# High fear (F&G < 25) — relaxed R/R:
python trading/executor.py bracket ETH/USDT:USDT 0.5 3800 3400 --limit 3600 --side buy --min-rr 1.3

# R/R validation standalone:
python trading/executor.py validate-rr BTC/USDT:USDT 90000 95000 88000 --side buy --min-rr 1.5
```

## Strategy (by Fear & Greed Regime)

| F&G Range | Regime | Long Focus | Short Focus | Max Leverage | R/R Min |
|-----------|--------|------------|-------------|--------------|---------|
| > 75 | Extreme Greed | Reduce longs, tight TPs | Aggressive shorts on overextended | 5x | 1.5 |
| 50-75 | Greed | Momentum longs, breakouts | Short failed breakouts | 7x | 1.5 |
| 25-50 | Neutral | Trend following both ways | Mean reversion shorts | 5x | 1.5 |
| 10-25 | Fear | Quality pullback longs | Aggressive shorts on weak names | 5x | 1.3 |
| < 10 | Extreme Fear | High conviction longs only | Monitor only | 3x | 1.3 |

## Bidirectional Indicators (computed by technical-analyst)

| Indicator | Long Signal | Short Signal |
|-----------|------------|--------------|
| EMA 20/50 | EMA20 > EMA50 (uptrend) | EMA20 < EMA50 (downtrend) |
| MACD(12,26,9) | Histogram positive & rising | Histogram negative & falling |
| RSI(14) | 40-60 (healthy) or < 30 (mean rev) | > 70 (overbought = short) |
| Bollinger(20,2) | Price at lower band | Price above upper band |
| Volume | > 1.5x avg confirms direction | > 1.5x avg confirms direction |
| ATR(14) | SL = entry - 2×ATR | SL = entry + 2×ATR |
| SMA200 | Price above = long bias | Price below = short bias |
| **Funding Rate** | Negative (shorts paying) = long bias | Very positive (longs paying) = short bias |

## Composite Score (used by swing-selector)
```
long_composite  = long_tech_score(35%) + sentiment_score(25%) + long_debate_score(40%)
short_composite = short_tech_score(35%) + sentiment_score(25%) + short_debate_score(40%)

Best direction = max(long_composite, short_composite)

Selection thresholds (F&G-adaptive):
  F&G >= 25:  composite >= 55/100
  F&G < 25:   composite >= 50/100 (relaxed)
```

## Python Module `trading/` (CCXT SDK)

```
trading/
├── __init__.py
├── client.py          # CCXT Binance Futures wrapper (testnet/live)
├── categories.py      # Crypto category management (like GICS sectors)
├── indicators.py      # Dual scoring: long_score + short_score + funding rate
├── executor.py        # CLI with all commands
├── discord.py         # Rich Discord notifications
└── protector.py       # Post-trade protection enforcement
```

### CLI reference (`source .venv/bin/activate && python trading/executor.py <cmd>`)

| Command | Description |
|---------|-------------|
| `account` | Equity, balance, exposure |
| `positions` | All open positions with PnL |
| `orders` | All open orders |
| `quote BTC ETH` | Ticker + funding rate (multi-symbol) |
| `bars BTC --timeframe 4h --limit 200` | OHLCV candles |
| `asset BTC/USDT:USDT` | Market info (precision, limits, fees) |
| `funding BTC ETH SOL` | Current funding rates |
| `status` | Full dashboard |
| `analyze BTC ETH SOL --json` | Dual technical analysis (long_score + short_score) |
| `reconcile` | **Sync progress.md with live state (run FIRST)** |
| `check-protection` | **Verify all positions have SL/TP** |
| `validate-rr BTC 90000 95000 88000` | **R/R validation with live prices** |
| `bracket BTC 0.01 95000 88000 --limit 90000` | Long bracket order |
| `bracket BTC 0.01 85000 92000 --side sell` | Short bracket order |
| `close BTC/USDT:USDT` | Close position |
| `cancel ORDER_ID BTC/USDT:USDT` | Cancel order |
| `cancel-all BTC/USDT:USDT` | Cancel all orders for symbol |
| `set-leverage BTC/USDT:USDT 10` | Set leverage |
| `set-margin BTC/USDT:USDT isolated` | Set margin mode |
| `time-stops --max-days 10` | Check expired positions |
| `trail-stops --dry-run` | Trailing stop adjustments |
| `category BTC ETH SOL` | Category lookup |
| `closed-orders --days 30` | Historical orders |
| `trades BTC/USDT:USDT --days 30` | Trade history |

### Virtual environment
```bash
source .venv/bin/activate  # Python 3.12, ccxt + pandas + numpy
```

## Trading Pairs (aggressive list)

### Always active
- `BTC/USDT:USDT` — Store of Value, highest liquidity
- `ETH/USDT:USDT` — Smart Contract L1, second highest
- `SOL/USDT:USDT` — High volatility, good momentum

### Active
- `BNB/USDT:USDT`, `XRP/USDT:USDT`, `DOGE/USDT:USDT`
- `AVAX/USDT:USDT`, `LINK/USDT:USDT`, `SUI/USDT:USDT`
- `ARB/USDT:USDT`, `NEAR/USDT:USDT`, `OP/USDT:USDT`
- `FET/USDT:USDT`, `RENDER/USDT:USDT`, `INJ/USDT:USDT`

### Meme (high vol, momentum plays)
- `WIF/USDT:USDT`, `PEPE/USDT:USDT`, `BONK/USDT:USDT`

## Crypto Categories (replaces GICS sectors)

| Category | Examples | Max Positions |
|----------|----------|---------------|
| Store of Value | BTC | 2 |
| Smart Contract L1 | ETH, SOL, AVAX, SUI | 2 |
| Layer 2 | ARB, OP, MATIC | 2 |
| Exchange Token | BNB | 2 |
| DeFi | LINK, UNI, AAVE | 2 |
| Meme | DOGE, PEPE, WIF | 2 |
| AI | FET, RENDER, TAO | 2 |
| Payment | XRP, LTC | 2 |

## Short Selling — Order Logic (Binance Futures)

### Long flow (side=buy):
- Entry: BUY → TP: SELL at higher price → SL: SELL at lower price
- Protection: STOP_MARKET (sell) + TAKE_PROFIT_MARKET (sell), reduceOnly=true

### Short flow (side=sell):
- Entry: SELL → TP: BUY at lower price → SL: BUY at higher price
- **TP price < entry** (profit = sell high, buy back low)
- **SL price > entry** (stop = buy back at loss if price rises)
- Protection: STOP_MARKET (buy) + TAKE_PROFIT_MARKET (buy), reduceOnly=true

## Slash Commands

| Command | Description |
|---------|-------------|
| `/run-trading-cycle` | Full pipeline: fetch → analyze → risk → trade → notify |
| `/make-profitables-trades` | Aggressive 4-phase pipeline (9 agents) |
| `/market-scan` | Scan all pairs without trading |
| `/status` | Balance, positions, drawdown |
| `/check-positions` | Positions + PnL details |
| `/close-position` | Close a specific position |
| `/manual-trade` | Manual trade with risk checks |
| `/emergency-close` | KILL SWITCH — close all |
| `/reset` | Restart after kill switch |
| `/replay` | Debug last signal |
| `/configure` | Verify setup and connectivity |
| `/trade-history` | Historical trades from state.json |
| `/cron-install` | Install cron job (every 15 min for crypto) |

## Plugins (local, in `plugins/`)

### agents-crypto-trading
5 specialized crypto sub-agents (spawned via Agent tool with `subagent_type`):
- **crypto-analyst** — Market analysis, on-chain metrics, sentiment, trading signals
- **crypto-trader** — CCXT trading systems, strategy implementation, order execution
- **crypto-risk-manager** — VaR, position sizing, liquidation monitoring, Kelly Criterion
- **arbitrage-bot** — Cross-exchange arbitrage, DEX/CEX, flash loans
- **defi-strategist** — Yield farming, LP, vault strategies

### quant-trading-system
Production-grade quant trading stack:
- **risk-manager agent** — Multi-layer risk validation and position sizing (392 lines)
- **hooks** — pre_trade, post_trade, circuit_breaker, guard_approve, kill_switch
- **workflows** — 5-min trading loop, full quant pipeline
- **commands** — /ccxt-exchange (balance, ticker, markets), /metrics-write

## File Conventions
- Reports: `/reports/trading-session-YYYYMMDD-HHMMSS.md`
- Logs: `/logs/` (session metrics, hook logs)
- Portfolio state: `/progress.md` (LOG only, not source of truth)
- State: `/state.json` (initial_balance, killed, trades)
- All times in UTC

## Infrastructure

### Scaleway Serverless Jobs
- **Trading cycle**: Every 15 min, 24/7 (crypto never sleeps)
- **Protection check**: Every 5 min (lightweight)
- Cost target: < $10/trade

### Cron (local alternative)
```bash
# Every 15 minutes, 24/7
*/15 * * * * cd ~/MILKY-WAY/DEV/cc/TRAPPIST && ./run.sh >> logs/cron.log 2>&1
```
