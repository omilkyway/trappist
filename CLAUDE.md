# TRAPPIST — Crypto Futures Trading Bot

> **One mission: make money. Be aggressive. Learn from every trade.**

## How it works

Claude IS the trader. You read the data, you think, you trade.
Every loss is tuition. Every win is validation. Take meaningful positions.

- `/trade` — Full cycle: intel → scan → decide → execute → Discord notify
- `/status` — Quick dashboard (equity, win rate, positions, drawdown)
- `/kill` — Emergency close all

## Tools

### Python CLI (`source .venv/bin/activate`)
```bash
python trading/executor.py status                                    # Dashboard + P&L history
python trading/executor.py scan --pairs BTC,ETH,SOL                  # TA + funding + sizing hints
python trading/executor.py bracket BTC 0.01 80000 70000 --leverage 10  # LONG 10x
python trading/executor.py bracket ETH 0.5 3000 3800 --side sell       # SHORT
python trading/executor.py close BTC/USDT:USDT                        # Close + cancel
python trading/executor.py protect --trail                             # Fix protection + trail
```

### MCP Servers
- **binance-futures** — Prices, positions, orders, klines, leverage
- **fear-greed** — Fear & Greed Index (regime)
- **gloria-news** — AI crypto news (19 categories)
- **tradingview** — Indicators (RSI, MACD, EMA, Bollinger)
- **cryptopanic** — Real-time news feed
- **crypto-price** — Quick prices

### WebSearch
Always search before trading — breaking news overrides technicals.

## Hard Limits (code-enforced)

| Limit | Value | Cannot be overridden |
|-------|-------|---------------------|
| Max leverage | **20x** (use scan's `suggested_leverage`) | executor.py |
| Max positions | **8** concurrent | executor.py |
| Max exposure | **75%** gross | executor.py |
| Max per category | **3** | categories.py |
| Min R/R | **1.5** (code) / **2.0** (target) | executor.py |
| Risk per trade | **2%** equity (Half-Kelly) | executor.py scan |
| Cooldown | **60 min** per symbol | executor.py |
| Drawdown kill | **-20%** | risk_guardian.py |
| SL distance | **2x ATR** (volatility-adaptive) | indicators.py |
| TP distance | **4x ATR** (2.0 R/R minimum) | indicators.py |
| Emergency SL | leverage-aware (max 50% margin loss) | executor.py protect |
| Trail method | **Chandelier Exit** (3x ATR from high) | executor.py protect |
| Trail activation | at **+3%** PnL | executor.py protect |

## Sizing (from scan output)

```
risk_per_trade = equity × 5%
qty = risk / SL_distance
capped at: equity × 15% / price
```

The scan calculates `suggested_qty` — USE IT, don't invent sizes.
- **Tier A (high conviction, score > 65)**: full `suggested_qty`, 10x leverage
- **Tier B (moderate, score 55-65)**: 50% of `suggested_qty`, 5x leverage
- **Tier C (weak, score < 55)**: DO NOT TRADE

## Strategy Playbook (research-validated + data-driven)

**Core edge: Adaptive risk management**
- ATR-based everything: SL (2×ATR), TP (4×ATR), trail (Chandelier 3×ATR), leverage (volatility-inverse)
- Multi-timeframe: only trade when 1h + 4h align (+15-25% win rate vs single TF)
- Regime detection: ADX > 25 = trend-follow, ADX < 20 = skip or mean revert

**Position sizing: Half-Kelly (2% risk per trade)**
- Formula: qty = (equity × 2%) / SL_distance
- Capped at 12% notional per position
- Leverage = 2% / (ATR% × 2) — automatically adapts to volatility

**What works (from trade data + research):**
- Early momentum entries when ADX > 25 and multi-TF aligned = 65%+ win rate
- Chandelier Exit trailing captures 3-7% moves (ATR-adaptive)
- Bollinger squeeze → breakout = highest R/R setups (3:1+)
- AI category (FET, RENDER) and L1 (DOT, SOL) are the best performers

**What loses money:**
- Trading in ranging regime (ADX < 20) = noise, not signal
- Re-entering exhausted moves (cooldown now prevents this)
- Fixed leverage ignoring volatility (high vol + high lev = disaster)
- Low R/R trades (< 1.5) barely break even after fees at 40% win rate

**Timing:**
- Cycle runs every 15 min with 60 min cooldown per symbol
- Scan provides multi-TF + regime + squeeze + suggested leverage
- 0 trades per cycle is NORMAL in ranging markets — it's not a missed opportunity

## Universe & Discovery

**Dynamic**: The scan auto-discovers ALL Binance USDT-M Futures pairs every hour.
- Filters: 24h volume > $10M, spread < 0.15%, active status
- Typically 60-80 pairs, ranked by volume
- Core (BTC, ETH, SOL) always included
- Pre-filter keeps top 25 movers for deep TA

**Use news & web search** to find catalysts BEFORE scanning. If a token is trending
(CryptoPanic, Gloria, WebSearch), scan it explicitly: `--pairs TOKEN`

## Categories (max 3 per category)

Store of Value · Smart Contract L1 · Layer 2 · Exchange Token · DeFi · Meme ·
AI · Payment · Gaming · Infrastructure · Staking · Other (uncategorized tokens)

## Config

- Keys: `.env.local` (BINANCE_KEY_API, BINANCE_KEY_SECRET)
- State: `state.json` (trades, closed_trades, win_rate)
- Discord: `DISCORD_WEBHOOK_URL` in .env.local
- S3: `s3://trappist` (Scaleway, syncs state between runs)
