# Alpaca-py SDK — Guide Exhaustif Getting Started

## 1. Vue d'ensemble

**alpaca-py** est le SDK Python officiel d'Alpaca (v0.43.2, Nov 2025). Il remplace l'ancien `alpaca-trade-api` (déprécié fin 2022). Licence Apache 2.0, Python 3.8+.

Il couvre **3 APIs** :
- **Trading API** — Ordres, positions, account, watchlists, corporate actions
- **Market Data API** — Données historiques + streaming temps réel (stocks, crypto, options, news)
- **Broker API** — Construction d'apps d'investissement (account opening, funding, KYC)

---

## 2. Installation

```bash
# Installation standard
pip install alpaca-py

# Mise à jour
pip install alpaca-py --upgrade

# Avec Poetry
poetry add alpaca-py
```

---

## 3. Obtenir ses clés API

1. Créer un compte gratuit : https://app.alpaca.markets/paper/dashboard/overview
2. Générer les API keys depuis le dashboard
3. Les clés **paper** (sandbox) sont séparées des clés **live**

> **Paper trading = 100K$ virtuels, données temps réel, zéro risque.**

---

## 4. Architecture des Clients

Le SDK suit un pattern **1 client = 1 domaine**. Tu instancies uniquement ce dont tu as besoin :

### Trading
```python
from alpaca.trading.client import TradingClient

# paper=True pour le sandbox
client = TradingClient("APCA-API-KEY-ID", "APCA-API-SECRET-KEY", paper=True)
```

### Market Data — Historique
```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.historical.news import NewsClient

# Crypto et News ne nécessitent PAS de clés
crypto_client = CryptoHistoricalDataClient()
news_client = NewsClient()

# Stocks et Options nécessitent des clés
stock_client = StockHistoricalDataClient("KEY", "SECRET")
option_client = OptionHistoricalDataClient("KEY", "SECRET")
```

### Market Data — Streaming temps réel (WebSocket)
```python
from alpaca.data.live import StockDataStream
from alpaca.data.live import CryptoDataStream
from alpaca.data.live import OptionDataStream
from alpaca.data.live.news import NewsDataStream
```

### Broker (B2B)
```python
from alpaca.broker.client import BrokerClient
broker_client = BrokerClient("BROKER-KEY", "BROKER-SECRET", sandbox=True)
```

---

## 5. Le pattern OOP : Request Models

Chaque opération utilise un objet **Request** typé + validation Pydantic à runtime.

### Correspondance méthode → request model

| Méthode | Request Model |
|---------|--------------|
| `client.submit_order()` | `MarketOrderRequest`, `LimitOrderRequest`, `StopOrderRequest`, `StopLimitOrderRequest`, `TrailingStopOrderRequest` |
| `client.get_orders()` | `GetOrdersRequest` |
| `client.get_all_positions()` | *(pas de request)* |
| `client.close_position()` | `ClosePositionRequest` |
| `stock_client.get_stock_bars()` | `StockBarsRequest` |
| `crypto_client.get_crypto_bars()` | `CryptoBarsRequest` |
| `option_client.get_option_bars()` | `OptionBarsRequest` |
| `news_client.get_news()` | `NewsRequest` |

---

## 6. Exemples Pratiques Complets

### 6.1 — Consulter son compte

```python
from alpaca.trading.client import TradingClient

client = TradingClient("KEY", "SECRET", paper=True)

account = client.get_account()
print(f"Equity: ${account.equity}")
print(f"Buying Power: ${account.buying_power}")
print(f"Cash: ${account.cash}")
print(f"Pattern Day Trader: {account.pattern_day_trader}")
print(f"Margin enabled: {account.account_number}")
```

### 6.2 — Passer un ordre Market (achat)

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

client = TradingClient("KEY", "SECRET", paper=True)

order = client.submit_order(
    MarketOrderRequest(
        symbol="AAPL",
        qty=5,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
)
print(f"Order ID: {order.id}, Status: {order.status}")
```

### 6.3 — Short Selling (vente à découvert)

```python
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# Short = SELL sans position existante (compte margin, equity >= $2,000)
short_order = client.submit_order(
    MarketOrderRequest(
        symbol="TSLA",
        qty=2,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
)
```

### 6.4 — Ordre Limit

```python
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

order = client.submit_order(
    LimitOrderRequest(
        symbol="NVDA",
        qty=10,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,  # Good Till Cancelled
        limit_price=120.00,
    )
)
```

### 6.5 — Ordre Stop Loss

```python
from alpaca.trading.requests import StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

order = client.submit_order(
    StopOrderRequest(
        symbol="AAPL",
        qty=5,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        stop_price=170.00,
    )
)
```

### 6.6 — Trailing Stop

```python
from alpaca.trading.requests import TrailingStopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

order = client.submit_order(
    TrailingStopOrderRequest(
        symbol="AAPL",
        qty=5,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        trail_percent=5.0,  # ou trail_price=10.0
    )
)
```

### 6.7 — Bracket Order (entry + take profit + stop loss)

```python
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

order = client.submit_order(
    MarketOrderRequest(
        symbol="SPY",
        qty=10,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
        order_class=OrderClass.BRACKET,
        take_profit={"limit_price": 460.0},
        stop_loss={"stop_price": 440.0},
    )
)
```

### 6.8 — Crypto (BTC, ETH)

```python
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

crypto_order = client.submit_order(
    MarketOrderRequest(
        symbol="BTC/USD",
        qty=0.001,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
    )
)
```

### 6.9 — Lister et annuler des ordres

```python
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

# Lister les ordres ouverts
open_orders = client.get_orders(
    GetOrdersRequest(status=QueryOrderStatus.OPEN)
)

# Annuler un ordre spécifique
client.cancel_order_by_id(order_id="uuid-here")

# Annuler TOUS les ordres ouverts
client.cancel_orders()
```

### 6.10 — Gérer les positions

```python
# Toutes les positions ouvertes
positions = client.get_all_positions()
for pos in positions:
    print(f"{pos.symbol}: {pos.qty} shares, P&L: ${pos.unrealized_pl}")

# Position spécifique
position = client.get_open_position("AAPL")

# Fermer une position entière
client.close_position("AAPL")

# Fermer partiellement (50%)
from alpaca.trading.requests import ClosePositionRequest
client.close_position("AAPL", close_options=ClosePositionRequest(percentage=50))

# Liquider toutes les positions
client.close_all_positions(cancel_orders=True)
```

---

## 7. Market Data — Historique

### 7.1 — Bars (OHLCV) pour Stocks

```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime

stock_client = StockHistoricalDataClient("KEY", "SECRET")

bars = stock_client.get_stock_bars(
    StockBarsRequest(
        symbol_or_symbols=["AAPL", "MSFT", "NVDA"],
        timeframe=TimeFrame.Day,
        start=datetime(2025, 1, 1),
        end=datetime(2025, 6, 1),
    )
)

# Conversion en DataFrame pandas (multi-index: symbol, timestamp)
df = bars.df
print(df.head())
```

### TimeFrame disponibles
- `TimeFrame.Minute` — 1 min
- `TimeFrame(5, TimeFrameUnit.Minute)` — 5 min
- `TimeFrame(15, TimeFrameUnit.Minute)` — 15 min
- `TimeFrame.Hour` — 1h
- `TimeFrame.Day` — journalier
- `TimeFrame.Week` — hebdo
- `TimeFrame.Month` — mensuel

### 7.2 — Bars Crypto (sans clés)

```python
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

crypto_client = CryptoHistoricalDataClient()  # pas de clés !

bars = crypto_client.get_crypto_bars(
    CryptoBarsRequest(
        symbol_or_symbols=["BTC/USD", "ETH/USD"],
        timeframe=TimeFrame.Day,
        start=datetime(2025, 1, 1),
    )
)
df = bars.df
```

### 7.3 — Quotes et Trades

```python
from alpaca.data.requests import StockLatestQuoteRequest

# Dernière quote (bid/ask)
quote = stock_client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols="AAPL"))

# Snapshots (quote + trade + bar + prev close en une requête)
from alpaca.data.requests import StockSnapshotRequest
snapshot = stock_client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols="AAPL"))
```

### 7.4 — News

```python
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

news_client = NewsClient()  # pas de clés
news = news_client.get_news(
    NewsRequest(
        symbols="TSLA",
        start=datetime(2025, 1, 1),
    )
)
df = news.df
```

### 7.5 — Options historiques

```python
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest

option_client = OptionHistoricalDataClient("KEY", "SECRET")
bars = option_client.get_option_bars(
    OptionBarsRequest(
        symbol_or_symbols=["AAPL250620C00200000"],
        timeframe=TimeFrame.Day,
    )
)
```

---

## 8. Streaming Temps Réel (WebSocket)

### 8.1 — Stream de trades et quotes stocks

```python
from alpaca.data.live import StockDataStream

stream = StockDataStream("KEY", "SECRET")

async def on_trade(trade):
    print(f"Trade: {trade.symbol} @ {trade.price} x {trade.size}")

async def on_quote(quote):
    print(f"Quote: {quote.symbol} bid={quote.bid_price} ask={quote.ask_price}")

# Souscrire
stream.subscribe_trades(on_trade, "AAPL", "TSLA")
stream.subscribe_quotes(on_quote, "AAPL")

# Lancer le stream (bloquant)
stream.run()
```

### 8.2 — Stream crypto

```python
from alpaca.data.live import CryptoDataStream

crypto_stream = CryptoDataStream()  # pas de clés pour crypto

async def on_crypto_bar(bar):
    print(f"Crypto Bar: {bar.symbol} close={bar.close}")

crypto_stream.subscribe_bars(on_crypto_bar, "BTC/USD")
crypto_stream.run()
```

### 8.3 — Trade Updates (fills, cancels, etc.)

```python
from alpaca.trading.stream import TradingStream

trading_stream = TradingStream("KEY", "SECRET", paper=True)

async def on_trade_update(data):
    event = data.event
    order = data.order
    print(f"Event: {event}, Symbol: {order.symbol}, Status: {order.status}")

trading_stream.subscribe_trade_updates(on_trade_update)
trading_stream.run()
```

---

## 9. Market Clock & Calendar

```python
# Le marché est-il ouvert ?
clock = client.get_clock()
print(f"Open: {clock.is_open}, Next open: {clock.next_open}")

# Calendrier
from alpaca.trading.requests import GetCalendarRequest
calendar = client.get_calendar(
    GetCalendarRequest(start="2025-03-01", end="2025-03-31")
)
for day in calendar:
    print(f"{day.date}: {day.open} - {day.close}")
```

---

## 10. Watchlists

```python
from alpaca.trading.requests import CreateWatchlistRequest, UpdateWatchlistRequest

# Créer
watchlist = client.create_watchlist(
    CreateWatchlistRequest(name="Tech Long/Short", symbols=["AAPL", "TSLA", "NVDA"])
)

# Lister toutes les watchlists
watchlists = client.get_watchlists()

# Mettre à jour
client.update_watchlist_by_id(
    watchlist_id=watchlist.id,
    data=UpdateWatchlistRequest(name="Tech Basket v2", symbols=["AAPL", "MSFT", "GOOG"])
)
```

---

## 11. Corporate Actions

```python
from alpaca.trading.requests import GetCorporateAnnouncementsRequest
from alpaca.trading.enums import CorporateActionType

# Dividendes, splits, mergers
announcements = client.get_corporate_announcements(
    GetCorporateAnnouncementsRequest(
        ca_types=[CorporateActionType.DIVIDEND, CorporateActionType.SPLIT],
        since="2025-01-01",
        until="2025-06-01",
    )
)
```

---

## 12. Enums clés à connaître

### OrderSide
- `OrderSide.BUY`
- `OrderSide.SELL`

### TimeInForce
- `TimeInForce.DAY` — Expire fin de journée
- `TimeInForce.GTC` — Good Till Cancelled (max 90 jours)
- `TimeInForce.IOC` — Immediate Or Cancel
- `TimeInForce.FOK` — Fill Or Kill
- `TimeInForce.OPG` — Market on Open
- `TimeInForce.CLS` — Market on Close

### OrderClass (ordres conditionnels)
- `OrderClass.SIMPLE` — Défaut
- `OrderClass.BRACKET` — Entry + TP + SL
- `OrderClass.OCO` — One Cancels Other
- `OrderClass.OTO` — One Triggers Other

### OrderType
- `OrderType.MARKET`
- `OrderType.LIMIT`
- `OrderType.STOP`
- `OrderType.STOP_LIMIT`
- `OrderType.TRAILING_STOP`

---

## 13. Conversion en DataFrame

Tous les résultats Market Data supportent `.df` pour conversion pandas :

```python
bars = stock_client.get_stock_bars(request)

# Multi-index DataFrame (symbol, timestamp)
df = bars.df

# Pour un seul symbole, reset l'index
df_aapl = bars["AAPL"].df
```

---

## 14. Gestion d'erreurs

```python
from alpaca.common.exceptions import APIError

try:
    order = client.submit_order(order_data)
except APIError as e:
    print(f"API Error {e.status_code}: {e.message}")
    # 403 = insufficient buying power
    # 422 = invalid request params
    # 429 = rate limited
```

---

## 15. Variables d'environnement (alternative aux clés en dur)

```bash
export APCA_API_KEY_ID="your-key"
export APCA_API_SECRET_KEY="your-secret"
export APCA_API_BASE_URL="https://paper-api.alpaca.markets"  # paper
```

```python
# Le client détecte automatiquement les env vars
client = TradingClient()  # pas besoin de passer les clés
```

---

## 16. Arbre complet de la doc SDK

### Doc SDK (Sphinx) — https://alpaca.markets/sdks/python/

- **Getting Started** → `/getting_started.html`
- **Market Data** → `/market_data.html`
- **Trading** → `/trading.html`
- **Broker** → `/broker.html`
- **API Reference**
  - Market Data Reference → Clients, Requests, Models, Enums, TimeFrame
    - Stock: Historical, Real-Time, Screener
    - Crypto: Historical, Real-Time
    - Option: Historical, Real-Time
    - Corporate Actions
  - Trading Reference → TradingClient, TradingStream, Account, Positions, Orders, Assets, Contracts, Watchlists, Calendar, Clock, Corporate Actions, Models, Requests, Enums
  - Broker Reference → BrokerClient, Accounts, Documents, Funding, Journals, Trading, Models, Requests, Enums

### Doc API REST (OpenAPI) — https://docs.alpaca.markets/reference

Endpoints exhaustifs : OAuth, Trading API (Account, Assets, Corporate Actions, Orders, Positions, Portfolio History, Watchlists, Account Config, Activities, Calendar, Clock, Crypto Funding), Market Data API (Stock, Option, Crypto, Fixed Income, Forex, Logos, Screener, News, Corporate Actions), Broker API (Accounts, Documents, Trading, Assets, Funding, Journals, Events, KYC, etc.)

### Doc narrative — https://docs.alpaca.markets/docs/trading-api

Guides conceptuels : Margin & Short Selling, Options Trading (L1→L3), Crypto, Fractional Trading, User Protection, Websocket Streaming, etc.

---

## 17. Repo GitHub & Exemples

**Repo principal** : https://github.com/alpacahq/alpaca-py (1.2K stars, 908 commits)

**Jupyter Notebooks d'exemples** dans `/examples/` :
- `examples/stocks/` — Stocks trading & data
- `examples/crypto/` — Crypto trading & data
- `examples/options/` — Options trading basic + multi-leg
- `examples/options/options-trading-basic.ipynb` — Notebook détaillé options

---

## 18. Libs complémentaires recommandées

| Catégorie | Lib | Usage |
|-----------|-----|-------|
| Backtesting | [Backtrader](https://github.com/backtrader/backtrader) | Backtest classique |
| Backtesting ML | [Vectorbt](https://github.com/polakowo/vectorbt) | Backtest vectorisé + ML |
| Algo Trading | [LiuAlgoTrader](https://github.com/amor71/LiuAlgoTrader/) | Framework multi-process |
| RL Trading | [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Reinforcement learning |
| Portfolio | [Pyfolio](https://github.com/quantopian/pyfolio) | Analytics & risk |
| Portfolio Optim | [FinQuant](https://github.com/fmilthaler/FinQuant) | Optimisation Markowitz |
| Banking | [Plaid](https://github.com/plaid/plaid-python) | Connexion bancaire |
| KYC | [Onfido](https://github.com/onfido/onfido-python) | Vérification identité |

---

## 19. MCP Server (bonus)

Pour piloter Alpaca en langage naturel depuis Claude, Cursor ou VS Code :

```bash
uvx alpaca-mcp-server init
```

Repo : https://github.com/alpacahq/alpaca-mcp-server

---

## 20. Récap des liens essentiels

| Ressource | URL |
|-----------|-----|
| SDK Doc (Sphinx) | https://alpaca.markets/sdks/python/getting_started.html |
| SDK Home | https://alpaca.markets/sdks/python/ |
| API Reference (REST) | https://docs.alpaca.markets/reference |
| Doc Narrative | https://docs.alpaca.markets/docs/trading-api |
| Margin & Short Selling | https://docs.alpaca.markets/docs/margin-and-short-selling |
| Options Trading | https://docs.alpaca.markets/docs/options-trading |
| GitHub alpaca-py | https://github.com/alpacahq/alpaca-py |
| PyPI | https://pypi.org/project/alpaca-py/ |
| Paper Trading Dashboard | https://app.alpaca.markets/paper/dashboard/overview |
| Community Forum | https://forum.alpaca.markets/ |
| MCP Server | https://github.com/alpacahq/alpaca-mcp-server |
| Changelog API | https://docs.alpaca.markets/changelog |
