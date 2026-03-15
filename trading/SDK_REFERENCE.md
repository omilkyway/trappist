# Trading Module — SDK Reference

> This file documents the `trading/` Python module and the underlying alpaca-py SDK (v0.43.2).
> Agents should read this file when they need to understand available functions, extend the module,
> or troubleshoot errors.

## Architecture

```
trading/
├── __init__.py        # Package marker
├── client.py          # Alpaca client wrapper — all SDK interactions
├── indicators.py      # Technical indicators (EMA, MACD, RSI, Bollinger, ATR)
├── executor.py        # CLI entrypoint — agents call this via Bash
└── SDK_REFERENCE.md   # This file
```

## CLI Quick Reference

All agents access Alpaca through the CLI. Always prefix with `source .venv/bin/activate &&`.

```bash
# Read-only data
python trading/executor.py account                          # Equity, buying power, cash
python trading/executor.py positions                        # Open positions with P&L
python trading/executor.py orders                           # Open orders with legs
python trading/executor.py clock                            # Market open/close status
python trading/executor.py status                           # All of the above combined
python trading/executor.py quote NVDA AAPL                  # Bid/ask/spread (multi-symbol)
python trading/executor.py bars NVDA --days 60              # Daily OHLCV bars
python trading/executor.py bars NVDA --timeframe 1Hour --days 10  # Hourly bars
python trading/executor.py bars NVDA --timeframe 5Min --days 2 --last 20  # Last 20 5-min bars
python trading/executor.py latest-trade NVDA                # Last trade price/size
python trading/executor.py latest-bar NVDA                  # Last OHLCV bar
python trading/executor.py asset NVDA                       # Tradability, exchange info
python trading/executor.py analyze NVDA AMD --days 60 --json  # Full technical analysis

# Order placement (trade-executor agent only)
python trading/executor.py bracket NVDA 28 185.00 166.50              # Market entry bracket
python trading/executor.py bracket NVDA 28 185.00 166.50 --limit 175  # Limit entry bracket
python trading/executor.py opg NVDA 28                                # Market-on-open
python trading/executor.py opg NVDA 28 --limit 175                    # Limit-on-open
python trading/executor.py oco NVDA 28 185.00 166.50                  # OCO protection (post-fill)

# Position management
python trading/executor.py close NVDA                 # Close entire position
python trading/executor.py close NVDA --pct 50        # Close 50% of position
python trading/executor.py cancel ORDER_UUID          # Cancel order by ID
```

---

## Python API — `trading.client`

All functions return plain `dict` or `list[dict]` (JSON-serializable). Import from `trading.client`.

### Account & Portfolio

| Function | Returns | Description |
|----------|---------|-------------|
| `get_account()` | `dict` | `{equity, buying_power, cash, last_equity, pattern_day_trader, account_number}` |
| `get_positions()` | `list[dict]` | Each: `{symbol, qty, side, avg_entry_price, market_value, unrealized_pl, unrealized_plpc, current_price}` |
| `get_open_orders()` | `list[dict]` | Each: `{id, symbol, side, qty, type, order_class, status, limit_price, stop_price, legs[]}` |
| `get_clock()` | `dict` | `{is_open, timestamp, next_open, next_close}` |

### Market Data

| Function | Args | Returns |
|----------|------|---------|
| `get_bars(symbol, timeframe="1Day", days=60)` | `str, str, int` | `pd.DataFrame` with columns: `open, high, low, close, volume, vwap` |
| `get_latest_quote(symbol)` | `str` | `{bid_price, ask_price, bid_size, ask_size, spread, spread_pct}` |
| `get_latest_trade(symbol)` | `str` | `{symbol, price, size, timestamp, exchange}` |
| `get_latest_bar(symbol)` | `str` | `{symbol, open, high, low, close, volume, timestamp, vwap}` |
| `get_asset_info(symbol)` | `str` | `{symbol, name, exchange, asset_class, tradable, shortable, fractionable, status}` |

### Order Placement

All order functions return `OrderResult` with `.to_dict()` → `{success, order_id, status, legs, error}`.

| Function | Args | Description |
|----------|------|-------------|
| `place_bracket_order(symbol, qty, take_profit_price, stop_loss_price, side="buy", time_in_force="day", limit_price=None)` | Entry + TP + SL atomic | Market or limit entry based on `limit_price` |
| `place_opg_order(symbol, qty, limit_price=None)` | Market/limit-on-open | Use when market is closed; add OCO after fill |
| `place_oco_order(symbol, qty, take_profit_price, stop_loss_price)` | OCO sell protection | Place after OPG fill to protect position |
| `close_position(symbol, percentage=None)` | Close full or partial | `percentage=50` closes 50% |
| `cancel_order(order_id)` | Cancel by UUID | Returns `bool` |

### Clients (low-level — rarely needed directly)

| Function | Returns | Notes |
|----------|---------|-------|
| `get_trading_client()` | `TradingClient` | Singleton; for account, orders, positions |
| `get_data_client()` | `StockHistoricalDataClient` | Singleton; for bars, quotes, trades |

---

## Python API — `trading.indicators`

All functions accept `pd.Series` or `pd.DataFrame` (with appropriately named columns).

| Function | Args | Returns | Description |
|----------|------|---------|-------------|
| `sma(close, period=20)` | Series/DF, int | `pd.Series` | Simple Moving Average |
| `ema(close, period=20)` | Series/DF, int | `pd.Series` | Exponential Moving Average |
| `macd(close, fast=12, slow=26, signal=9)` | Series/DF | `pd.DataFrame` | Columns: `macd_line, signal_line, histogram` |
| `rsi(close, period=14)` | Series/DF, int | `pd.Series` | RSI with Wilder smoothing |
| `bollinger_bands(close, period=20, num_std=2.0)` | Series/DF | `pd.DataFrame` | Columns: `upper, middle, lower, pct_b, bandwidth` |
| `atr(high, low, close, period=14)` | 3× Series/DF | `pd.Series` | Average True Range (Wilder) |
| `volume_ratio(volume, period=20)` | Series/DF, int | `pd.Series` | Current vol / 20-day SMA vol |
| `compute_signals(df)` | DataFrame (OHLCV) | `dict` | Full analysis: `{price, indicators{}, signals{}}` |

### `compute_signals` output structure

```json
{
  "price": 185.50,
  "indicators": {
    "ema20": 183.22, "ema50": 180.15, "ema_trend": "bullish",
    "macd_line": 1.85, "macd_signal": 1.20, "macd_histogram": 0.65,
    "rsi14": 58.3,
    "bollinger_upper": 192.10, "bollinger_middle": 184.50, "bollinger_lower": 176.90,
    "bollinger_pct_b": 0.566,
    "atr14": 4.22,
    "volume_ratio": 1.35,
    "sma200": 170.80
  },
  "signals": {
    "raw_score": 6,
    "normalized_score": 69,
    "direction": "bullish",
    "strength": "moderate"
  }
}
```

### Signal scoring weights

| Signal | Bullish | Bearish |
|--------|---------|---------|
| EMA trend (EMA20 > EMA50) | +2 | -2 |
| EMA crossover (last 5 bars) | +3 | -3 |
| MACD histogram rising/falling | +2 | -2 |
| MACD line cross signal (last 3 bars) | +3 | -3 |
| RSI zone (40-60 healthy / >70 or <30) | +1 | -1 |
| Bollinger %B (0.2-0.8 OK / >1 or <0) | +1 | -2 |
| Volume ratio (>1.5x / <0.5x) | +2 | -1 |
| Price vs SMA200 (above/below) | +2 | -2 |

**Range**: raw -16 to +16, normalized to 0-100. Threshold for swing selection: >= 60.

---

## alpaca-py SDK Reference (v0.43.2)

The module uses **alpaca-py**, the official Python SDK. Below is a reference of the SDK classes and enums used, plus common patterns for extending.

### Installation

```bash
uv pip install alpaca-py  # Already in .venv
```

### Key imports

```python
# Trading client (account, orders, positions)
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,      # SIMPLE, BRACKET, OCO, OTO
    OrderSide,       # BUY, SELL
    OrderType,       # MARKET, LIMIT, STOP, STOP_LIMIT, TRAILING_STOP
    QueryOrderStatus,# OPEN, CLOSED, ALL
    TimeInForce,     # DAY, GTC, OPG, CLS, IOC, FOK
)
from alpaca.trading.requests import (
    ClosePositionRequest,    # percentage: Optional[float]
    GetOrdersRequest,        # status, limit, after, until, direction, symbols
    LimitOrderRequest,       # symbol, qty, side, type, time_in_force, limit_price, ...
    MarketOrderRequest,      # symbol, qty, side, type, time_in_force, ...
    StopOrderRequest,        # symbol, qty, side, type, time_in_force, stop_price
    TrailingStopOrderRequest,# symbol, qty, side, trail_percent or trail_price
)

# Data client (bars, quotes, trades)
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,         # symbol_or_symbols, timeframe, start, end, limit, feed
    StockLatestBarRequest,    # symbol_or_symbols, feed
    StockLatestQuoteRequest,  # symbol_or_symbols, feed
    StockLatestTradeRequest,  # symbol_or_symbols, feed
    StockSnapshotRequest,     # symbol_or_symbols, feed
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# Error handling
from alpaca.common.exceptions import APIError  # Has .status_code attribute
```

### Client initialization

```python
# Paper trading (our default)
trading_client = TradingClient(api_key, secret_key, paper=True)

# Data client (same keys, no paper flag needed)
data_client = StockHistoricalDataClient(api_key, secret_key)
```

### Common SDK patterns

#### Get account
```python
acct = trading_client.get_account()
# Properties: equity, buying_power, cash, last_equity, pattern_day_trader,
#             account_number, status, currency, portfolio_value
print(float(acct.equity))
```

#### Get all positions
```python
positions = trading_client.get_all_positions()
for p in positions:
    # Properties: symbol, qty, side, avg_entry_price, market_value,
    #             unrealized_pl, unrealized_plpc, current_price, lastday_price,
    #             change_today, asset_id, exchange
    print(p.symbol, float(p.unrealized_pl))
```

#### Get orders with filtering
```python
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

# All open orders
orders = trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))

# Closed orders for a specific symbol
orders = trading_client.get_orders(GetOrdersRequest(
    status=QueryOrderStatus.CLOSED,
    symbols=["NVDA"],
    limit=10,
))

# Order properties: id, symbol, side, qty, type, order_class, status,
#                   limit_price, stop_price, filled_avg_price, filled_qty,
#                   legs (list of child orders for bracket/OCO)
```

#### Market clock
```python
clock = trading_client.get_clock()
# Properties: is_open (bool), timestamp, next_open, next_close
```

#### Get asset info
```python
asset = trading_client.get_asset("NVDA")
# Properties: symbol, name, exchange, asset_class, tradable, shortable,
#             fractionable, status, marginable, maintenance_margin_requirement
```

#### Fetch OHLCV bars
```python
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from datetime import datetime, timedelta

bars = data_client.get_stock_bars(StockBarsRequest(
    symbol_or_symbols="NVDA",
    timeframe=TimeFrame.Day,  # or TimeFrame(5, TimeFrameUnit.Minute)
    start=datetime.now() - timedelta(days=60),
    # Optional: end, limit, feed ("iex" or "sip")
))
df = bars.df  # MultiIndex DataFrame (symbol, timestamp)
df = df.xs("NVDA", level="symbol")  # Single-symbol slice
# Columns: open, high, low, close, volume, trade_count, vwap
```

#### Latest quote
```python
from alpaca.data.requests import StockLatestQuoteRequest

quotes = data_client.get_stock_latest_quote(
    StockLatestQuoteRequest(symbol_or_symbols="NVDA")
)
q = quotes["NVDA"]
# Properties: bid_price, ask_price, bid_size, ask_size, timestamp
```

#### Latest trade
```python
from alpaca.data.requests import StockLatestTradeRequest

trades = data_client.get_stock_latest_trade(
    StockLatestTradeRequest(symbol_or_symbols="NVDA")
)
t = trades["NVDA"]
# Properties: price, size, timestamp, exchange, id
```

#### Snapshot (quote + trade + bar in one call)
```python
from alpaca.data.requests import StockSnapshotRequest

snapshots = data_client.get_stock_snapshot(
    StockSnapshotRequest(symbol_or_symbols=["NVDA", "AAPL"])
)
snap = snapshots["NVDA"]
# Properties: latest_trade, latest_quote, minute_bar, daily_bar, previous_daily_bar
print(snap.latest_trade.price)
print(snap.daily_bar.close)
```

#### Multi-symbol bars
```python
bars = data_client.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=["NVDA", "AAPL", "AMD"],
    timeframe=TimeFrame.Day,
    start=datetime.now() - timedelta(days=30),
))
df = bars.df
nvda_df = df.xs("NVDA", level="symbol")
aapl_df = df.xs("AAPL", level="symbol")
```

### Order types reference

#### Market order
```python
order = trading_client.submit_order(MarketOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.BUY,
    type=OrderType.MARKET, time_in_force=TimeInForce.DAY,
))
```

#### Limit order
```python
order = trading_client.submit_order(LimitOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.BUY,
    type=OrderType.LIMIT, time_in_force=TimeInForce.GTC,
    limit_price=175.00,
))
```

#### Bracket order (entry + TP + SL)
```python
order = trading_client.submit_order(MarketOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.BUY,
    type=OrderType.MARKET, time_in_force=TimeInForce.DAY,
    order_class=OrderClass.BRACKET,
    take_profit={"limit_price": 195.00},
    stop_loss={"stop_price": 165.00},
))
# Returns parent order with .legs containing TP and SL child orders
```

#### OCO order (TP + SL protection, no entry)
```python
order = trading_client.submit_order(LimitOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.SELL,
    type=OrderType.LIMIT, time_in_force=TimeInForce.GTC,
    order_class=OrderClass.OCO,
    take_profit={"limit_price": 195.00},
    stop_loss={"stop_price": 165.00},
))
```

#### OPG order (market-on-open)
```python
order = trading_client.submit_order(MarketOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.BUY,
    type=OrderType.MARKET, time_in_force=TimeInForce.OPG,
))
```

#### Trailing stop order
```python
order = trading_client.submit_order(TrailingStopOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.SELL,
    time_in_force=TimeInForce.GTC,
    trail_percent=5.0,  # OR trail_price=8.50
))
```

#### Stop limit order
```python
from alpaca.trading.requests import StopLimitOrderRequest
order = trading_client.submit_order(StopLimitOrderRequest(
    symbol="NVDA", qty=10, side=OrderSide.SELL,
    type=OrderType.STOP_LIMIT, time_in_force=TimeInForce.GTC,
    stop_price=170.00,   # Trigger price
    limit_price=169.50,  # Limit after trigger
))
```

### Position management

```python
# Close entire position
trading_client.close_position("NVDA")

# Close 50% of position
from alpaca.trading.requests import ClosePositionRequest
trading_client.close_position("NVDA", close_options=ClosePositionRequest(percentage=50))

# Close all positions
trading_client.close_all_positions(cancel_orders=True)
```

### Order management

```python
# Cancel specific order
trading_client.cancel_order_by_id(order_id)

# Cancel all open orders
trading_client.cancel_orders()

# Get specific order by ID
order = trading_client.get_order_by_id(order_id)

# Replace/modify order
from alpaca.trading.requests import ReplaceOrderRequest
trading_client.replace_order_by_id(order_id, ReplaceOrderRequest(
    qty=15,
    limit_price=180.00,
))
```

### TimeFrame options

| Value | Usage |
|-------|-------|
| `TimeFrame.Minute` | 1-minute bars |
| `TimeFrame(5, TimeFrameUnit.Minute)` | 5-minute bars |
| `TimeFrame(15, TimeFrameUnit.Minute)` | 15-minute bars |
| `TimeFrame.Hour` | 1-hour bars |
| `TimeFrame.Day` | Daily bars |
| `TimeFrame.Week` | Weekly bars |
| `TimeFrame.Month` | Monthly bars |

### TimeInForce options

| Value | Description |
|-------|-------------|
| `TimeInForce.DAY` | Good for day only |
| `TimeInForce.GTC` | Good til cancelled (max 90 days) |
| `TimeInForce.OPG` | Market/limit-on-open |
| `TimeInForce.CLS` | Market/limit-on-close |
| `TimeInForce.IOC` | Immediate or cancel |
| `TimeInForce.FOK` | Fill or kill |

### OrderClass options

| Value | Description |
|-------|-------------|
| `OrderClass.SIMPLE` | Regular order (default) |
| `OrderClass.BRACKET` | Entry + take-profit + stop-loss |
| `OrderClass.OCO` | One-cancels-other (TP + SL, no entry) |
| `OrderClass.OTO` | One-triggers-other |

### Error handling

```python
from alpaca.common.exceptions import APIError

try:
    order = trading_client.submit_order(...)
except APIError as e:
    print(e.status_code)  # HTTP status (403, 422, etc.)
    print(str(e))         # Error message from Alpaca
```

Common error codes:
- **403**: Forbidden (insufficient buying power, pattern day trader)
- **404**: Not found (invalid symbol or order ID)
- **422**: Unprocessable (invalid parameters, market closed for this order type)

### Environment variables

```bash
APCA_API_KEY_ID=...        # Alpaca API key (primary)
APCA_API_SECRET_KEY=...    # Alpaca secret key (primary)
ALPACA_API_KEY=...         # Alternative key name
ALPACA_SECRET_KEY=...      # Alternative secret name
ALPACA_PAPER_TRADE=True    # Paper mode (default True)
```

Credentials are loaded from `.env` file via `python-dotenv`.

---

## Extending the module

### Adding a new data function to `client.py`

1. Import the relevant request class from `alpaca.data.requests` or `alpaca.trading.requests`
2. Create a function that returns a plain `dict` (JSON-serializable)
3. Use `get_data_client()` or `get_trading_client()` singleton

```python
# Example: add stock snapshot support
from alpaca.data.requests import StockSnapshotRequest

def get_snapshot(symbol: str) -> dict:
    client = get_data_client()
    snaps = client.get_stock_snapshot(
        StockSnapshotRequest(symbol_or_symbols=symbol)
    )
    s = snaps[symbol]
    return {
        "symbol": symbol,
        "latest_trade": float(s.latest_trade.price),
        "latest_quote_bid": float(s.latest_quote.bid_price),
        "latest_quote_ask": float(s.latest_quote.ask_price),
        "daily_close": float(s.daily_bar.close),
        "daily_volume": int(s.daily_bar.volume),
        "prev_close": float(s.previous_daily_bar.close),
    }
```

### Adding a new CLI command to `executor.py`

1. Create `cmd_xxx(args)` function
2. Register it in `main()` with `sub.add_parser()`
3. Import any new functions from `trading.client`

```python
# In executor.py
def cmd_snapshot(args):
    from trading.client import get_snapshot
    print(json.dumps(get_snapshot(args.symbol), indent=2))
    return 0

# In main():
p = sub.add_parser("snapshot", help="Get stock snapshot")
p.add_argument("symbol", help="Ticker symbol")
p.set_defaults(func=cmd_snapshot)
```

### Adding a new indicator to `indicators.py`

1. Accept `pd.Series | pd.DataFrame` as input
2. Use `_ensure_series()` helper for type safety
3. Return `pd.Series` (for single indicator) or `pd.DataFrame` (for multi-output)

```python
# Example: add VWAP indicator
def vwap(high, low, close, volume) -> pd.Series:
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")
    c = _ensure_series(close, "close")
    v = _ensure_series(volume, "volume")
    typical_price = (h + l + c) / 3
    return (typical_price * v).cumsum() / v.cumsum()
```
