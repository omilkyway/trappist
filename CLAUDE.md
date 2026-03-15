# TRAPPIST — Crypto Futures Trading Bot

> **One mission: make money. Every cycle, every direction.**

## How it works

Claude IS the trader. No agents, no committee. You read the data, you think, you trade.

- `/trade` — Full cycle: intel → scan → decide → execute → Discord notify
- `/status` — Quick dashboard
- `/kill` — Emergency close all

## Tools

### Python CLI (`source .venv/bin/activate`)
```bash
python trading/executor.py status                              # Dashboard
python trading/executor.py scan --pairs BTC,ETH,SOL            # Technical + funding
python trading/executor.py bracket BTC 0.002 76000 71000       # LONG bracket
python trading/executor.py bracket ETH 0.05 3200 3600 --side sell  # SHORT
python trading/executor.py close BTC/USDT:USDT                 # Close + cancel
python trading/executor.py protect --trail                     # Fix protection + trail
```

### MCP Servers (interactive analysis)
- **binance-futures** — Prices, positions, orders, klines, leverage
- **fear-greed** — Crypto Fear & Greed Index (regime detection)
- **gloria-news** — AI-curated crypto news (19 categories)
- **tradingview** — Technical indicators (RSI, MACD, EMA, Bollinger)
- **cryptopanic** — Real-time crypto news feed
- **crypto-price** — Quick price checks

### WebSearch
Use for breaking news, whale movements, regulatory updates, protocol upgrades.
Always search before trading — the market knows more than the charts.

## Rules (non-negotiable)

1. ALWAYS SL + TP on every position
2. MAX 2% risk per trade, 5% notional per trade
3. MAX 10x leverage (default 5x)
4. MAX 2 positions per crypto category
5. MAX 5 positions total, 40% gross exposure
6. KILL if drawdown > 10% from initial balance
7. Check protection BEFORE opening new trades
8. In doubt → smaller size, NOT no trade

## Regime table (Fear & Greed)

| F&G | Regime | Style |
|-----|--------|-------|
| >75 | Extreme Greed | SHORT bias, tight TPs |
| 50-75 | Greed | LONG momentum |
| 25-50 | Neutral | Both directions |
| 10-25 | Fear | Quality longs, aggressive shorts |
| <10 | Extreme Fear | High-conviction longs only |

## Categories (max 2 per category)

Store of Value (BTC) · Smart Contract L1 (ETH, SOL, AVAX, SUI) · Layer 2 (ARB, OP) ·
Exchange Token (BNB) · DeFi (LINK, UNI, AAVE) · Meme (DOGE, PEPE, WIF) ·
AI (FET, RENDER) · Payment (XRP, LTC)

## Short order logic

- LONG: entry BUY → SL SELL below → TP SELL above
- SHORT: entry SELL → SL BUY above → TP BUY below

## Config

- API keys: `.env.local` (BINANCE_KEY_API, BINANCE_KEY_SECRET)
- Testnet by default (no LIVE_MODE in .env)
- State: `state.json` (initial_balance, killed, trades)
- Discord: `DISCORD_WEBHOOK_URL` in .env.local

## Cloud (Scaleway)

- Trading cycle: every 15 min, 24/7
- Protection check: every 5 min
- S3 bucket: `s3://trappist`
