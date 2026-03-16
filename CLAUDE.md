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
| Max leverage | **20x** (default 10x) | executor.py |
| Max positions | **8** concurrent | executor.py |
| Max exposure | **75%** gross | executor.py |
| Max per category | **3** | categories.py |
| Min R/R | **1.2** | executor.py |
| Drawdown kill | **-20%** | risk_guardian.py |
| Emergency SL | **-10%** from entry | executor.py protect |
| Emergency TP | **+15%** from entry | executor.py protect |
| Trail to breakeven | at **+5%** PnL | executor.py protect |
| Trail distance | **5%** from price | executor.py protect |

## Sizing (from scan output)

```
risk_per_trade = equity × 5%
qty = risk / SL_distance
capped at: equity × 15% / price
```

The scan calculates `suggested_qty` — USE IT, don't invent sizes.

## Categories (max 3 per category)

Store of Value (BTC) · Smart Contract L1 (ETH, SOL, AVAX, SUI) · Layer 2 (ARB, OP) ·
Exchange Token (BNB) · DeFi (LINK, UNI, AAVE) · Meme (DOGE, PEPE, WIF) ·
AI (FET, RENDER) · Payment (XRP, LTC)

## Config

- Keys: `.env.local` (BINANCE_KEY_API, BINANCE_KEY_SECRET)
- State: `state.json` (trades, closed_trades, win_rate)
- Discord: `DISCORD_WEBHOOK_URL` in .env.local
- S3: `s3://trappist` (Scaleway, syncs state between runs)
