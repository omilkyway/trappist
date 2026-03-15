"""Alpaca client wrapper with bracket order support.

Provides a thin layer over alpaca-py that:
- Loads credentials from env vars (.env supported)
- Exposes bracket orders, OPG+OCO flow, position management
- Returns typed results the agents and executor can consume
"""

from __future__ import annotations

import functools
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

from alpaca.common.exceptions import APIError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry decorator for transient API failures
# ---------------------------------------------------------------------------

def _is_transient_error(exc: Exception) -> bool:
    """Return True if the error is likely transient and worth retrying."""
    if isinstance(exc, APIError):
        # 429 = rate limit, 5xx = server errors
        return exc.status_code in (429, 500, 502, 503, 504)
    # Network errors, timeouts
    err_msg = str(exc).lower()
    return any(kw in err_msg for kw in ("timeout", "connection", "reset", "refused", "unavailable"))


def retry_api(max_attempts: int = 3, base_delay: float = 1.0):
    """Retry decorator with exponential backoff for transient API errors."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_attempts or not _is_transient_error(exc):
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "API call %s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            raise last_exc  # unreachable but satisfies type checker
        return wrapper
    return decorator


from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    OrderType,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopOrderRequest,
)

load_dotenv()  # loads .env
load_dotenv(".env.local", override=True)  # loads .env.local (overrides .env)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _get_credentials() -> tuple[str, str, bool]:
    """Return (api_key, secret_key, is_paper) from env vars."""
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER_TRADE", "True").lower() in ("true", "1", "yes")
    return key, secret, paper


# ---------------------------------------------------------------------------
# Singleton clients
# ---------------------------------------------------------------------------

_trading_client: Optional[TradingClient] = None
_data_client: Optional[StockHistoricalDataClient] = None


def get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        key, secret, paper = _get_credentials()
        _trading_client = TradingClient(key, secret, paper=paper)
    return _trading_client


def get_data_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        key, secret, _ = _get_credentials()
        _data_client = StockHistoricalDataClient(key, secret)
    return _data_client


# ---------------------------------------------------------------------------
# Account & positions
# ---------------------------------------------------------------------------

@retry_api()
def get_account() -> dict:
    """Return account info as a plain dict."""
    acct = get_trading_client().get_account()
    return {
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "cash": float(acct.cash),
        "last_equity": float(acct.last_equity),
        "pattern_day_trader": acct.pattern_day_trader,
        "account_number": acct.account_number,
    }


@retry_api()
def get_positions() -> list[dict]:
    """Return all open positions as list of dicts."""
    positions = get_trading_client().get_all_positions()
    return [
        {
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": str(p.side),
            "avg_entry_price": float(p.avg_entry_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
            "current_price": float(p.current_price),
        }
        for p in positions
    ]


@retry_api()
def get_open_orders() -> list[dict]:
    """Return all open orders as list of dicts."""
    orders = get_trading_client().get_orders(
        GetOrdersRequest(status=QueryOrderStatus.OPEN)
    )
    return [
        {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": str(o.side),
            "qty": str(o.qty),
            "type": str(o.type),
            "order_class": str(o.order_class),
            "status": str(o.status),
            "limit_price": str(o.limit_price) if o.limit_price else None,
            "stop_price": str(o.stop_price) if o.stop_price else None,
            "legs": [
                {
                    "id": str(leg.id),
                    "type": str(leg.type),
                    "side": str(leg.side),
                    "limit_price": str(leg.limit_price) if leg.limit_price else None,
                    "stop_price": str(leg.stop_price) if leg.stop_price else None,
                    "status": str(leg.status),
                }
                for leg in (o.legs or [])
            ],
        }
        for o in orders
    ]


@retry_api()
def get_clock() -> dict:
    """Return market clock info."""
    c = get_trading_client().get_clock()
    return {
        "is_open": c.is_open,
        "timestamp": str(c.timestamp),
        "next_open": str(c.next_open),
        "next_close": str(c.next_close),
    }


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@retry_api()
def get_bars(
    symbol: str,
    timeframe: str = "1Day",
    days: int = 60,
) -> "pd.DataFrame":
    """Fetch OHLCV bars and return a pandas DataFrame."""
    import pandas as pd

    tf_map = {
        "1Min": TimeFrame.Minute,
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame.Hour,
        "1Day": TimeFrame.Day,
        "1Week": TimeFrame.Week,
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)

    client = get_data_client()
    bars = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=datetime.now(timezone.utc) - timedelta(days=days),
        )
    )
    # bars.df returns a multi-index DataFrame (symbol, timestamp).
    # Select the single symbol and drop that index level.
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")
    return df


@retry_api()
def get_latest_quote(symbol: str) -> dict:
    """Get latest bid/ask quote."""
    client = get_data_client()
    quote = client.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=symbol)
    )
    q = quote[symbol]
    return {
        "bid_price": float(q.bid_price),
        "ask_price": float(q.ask_price),
        "bid_size": int(q.bid_size),
        "ask_size": int(q.ask_size),
        "spread": round(float(q.ask_price) - float(q.bid_price), 4),
        "spread_pct": round(
            (float(q.ask_price) - float(q.bid_price)) / float(q.ask_price) * 100, 4
        ) if float(q.ask_price) > 0 else 0,
    }


@retry_api()
def get_latest_trade(symbol: str) -> dict:
    """Get latest trade for a symbol."""
    client = get_data_client()
    trades = client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol)
    )
    t = trades[symbol]
    return {
        "symbol": symbol,
        "price": float(t.price),
        "size": int(t.size),
        "timestamp": str(t.timestamp),
        "exchange": str(t.exchange) if t.exchange else None,
    }


@retry_api()
def get_latest_bar(symbol: str) -> dict:
    """Get latest bar for a symbol."""
    client = get_data_client()
    bars = client.get_stock_latest_bar(
        StockLatestBarRequest(symbol_or_symbols=symbol)
    )
    b = bars[symbol]
    return {
        "symbol": symbol,
        "open": float(b.open),
        "high": float(b.high),
        "low": float(b.low),
        "close": float(b.close),
        "volume": int(b.volume),
        "timestamp": str(b.timestamp),
        "vwap": float(b.vwap) if b.vwap else None,
    }


@retry_api()
def get_asset_info(symbol: str) -> dict:
    """Get asset info (tradability, exchange, class)."""
    client = get_trading_client()
    asset = client.get_asset(symbol)
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "exchange": str(asset.exchange),
        "asset_class": str(asset.asset_class),
        "tradable": asset.tradable,
        "shortable": asset.shortable,
        "fractionable": asset.fractionable,
        "status": str(asset.status),
    }


# ---------------------------------------------------------------------------
# Order placement — BRACKET ORDERS
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    status: Optional[str] = None
    legs: Optional[list[dict]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "status": self.status,
            "legs": self.legs,
            "error": self.error,
        }


def _validate_order_params(
    qty: int,
    take_profit_price: float,
    stop_loss_price: float,
    side: str,
    limit_price: Optional[float] = None,
) -> Optional[str]:
    """Validate order parameters. Returns error message or None if valid."""
    if qty <= 0:
        return f"qty must be > 0, got {qty}"
    if take_profit_price <= 0:
        return f"take_profit_price must be > 0, got {take_profit_price}"
    if stop_loss_price <= 0:
        return f"stop_loss_price must be > 0, got {stop_loss_price}"
    if limit_price is not None and limit_price <= 0:
        return f"limit_price must be > 0, got {limit_price}"

    if side.lower() == "buy":
        # LONG: TP must be above SL
        if take_profit_price <= stop_loss_price:
            return (
                f"LONG order: take_profit ({take_profit_price}) must be > "
                f"stop_loss ({stop_loss_price})"
            )
    elif side.lower() == "sell":
        # SHORT: TP must be below SL (profit = buy back lower)
        if take_profit_price >= stop_loss_price:
            return (
                f"SHORT order: take_profit ({take_profit_price}) must be < "
                f"stop_loss ({stop_loss_price})"
            )
    else:
        return f"side must be 'buy' or 'sell', got '{side}'"

    return None


@retry_api()
def place_bracket_order(
    symbol: str,
    qty: int,
    take_profit_price: float,
    stop_loss_price: float,
    side: str = "buy",
    time_in_force: str = "gtc",
    limit_price: Optional[float] = None,
) -> OrderResult:
    """Place a bracket order (entry + take-profit + stop-loss in one request).

    Args:
        symbol: Ticker symbol (e.g., "NVDA")
        qty: Number of shares
        take_profit_price: Take-profit limit price
        stop_loss_price: Stop-loss trigger price
        side: "buy" or "sell" (default "buy")
        time_in_force: "day" or "gtc" (default "gtc" — preferred to avoid expiry)
        limit_price: If provided, entry is a LIMIT order; otherwise MARKET
    """
    err = _validate_order_params(qty, take_profit_price, stop_loss_price, side, limit_price)
    if err:
        return OrderResult(success=False, error=f"Validation failed: {err}")
    try:
        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if time_in_force.lower() == "gtc" else TimeInForce.DAY

        common = dict(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            order_class=OrderClass.BRACKET,
            take_profit={"limit_price": take_profit_price},
            stop_loss={"stop_price": stop_loss_price},
            client_order_id=f"ct_bracket_{symbol}_{int(time.time())}",
        )

        if limit_price is not None:
            order_data = LimitOrderRequest(
                type=OrderType.LIMIT,
                limit_price=limit_price,
                **common,
            )
        else:
            order_data = MarketOrderRequest(
                type=OrderType.MARKET,
                **common,
            )

        order = client.submit_order(order_data)

        legs = []
        for leg in (order.legs or []):
            legs.append({
                "id": str(leg.id),
                "type": str(leg.type),
                "side": str(leg.side),
                "limit_price": str(leg.limit_price) if leg.limit_price else None,
                "stop_price": str(leg.stop_price) if leg.stop_price else None,
                "status": str(leg.status),
            })

        return OrderResult(
            success=True,
            order_id=str(order.id),
            status=str(order.status),
            legs=legs,
        )
    except APIError as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=f"API Error {e.status_code}: {e}")
    except Exception as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=str(e))


@retry_api()
def place_opg_order(
    symbol: str,
    qty: int,
    side: str = "buy",
    limit_price: Optional[float] = None,
) -> OrderResult:
    """Place a Market-on-Open (MOO) or Limit-on-Open (LOO) order.

    Args:
        symbol: Ticker symbol
        qty: Number of shares
        side: "buy" (long entry) or "sell" (short entry)
        limit_price: If provided, entry is a LOO order; otherwise MOO
    """
    if qty <= 0:
        return OrderResult(success=False, error=f"Validation failed: qty must be > 0, got {qty}")
    if side.lower() not in ("buy", "sell"):
        return OrderResult(success=False, error=f"Validation failed: side must be 'buy' or 'sell', got '{side}'")
    if limit_price is not None and limit_price <= 0:
        return OrderResult(success=False, error=f"Validation failed: limit_price must be > 0, got {limit_price}")
    try:
        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        if limit_price is not None:
            order_data = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                type=OrderType.LIMIT,
                time_in_force=TimeInForce.OPG,
                limit_price=limit_price,
                client_order_id=f"ct_opg_{symbol}_{int(time.time())}",
            )
        else:
            order_data = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                type=OrderType.MARKET,
                time_in_force=TimeInForce.OPG,
                client_order_id=f"ct_opg_{symbol}_{int(time.time())}",
            )

        order = client.submit_order(order_data)
        return OrderResult(
            success=True,
            order_id=str(order.id),
            status=str(order.status),
        )
    except APIError as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=f"API Error {e.status_code}: {e}")
    except Exception as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=str(e))


@retry_api()
def place_oco_order(
    symbol: str,
    qty: int,
    take_profit_price: float,
    stop_loss_price: float,
    side: str = "sell",
) -> OrderResult:
    """Place an OCO (One-Cancels-Other) protection order after OPG fill.

    Args:
        symbol: Ticker symbol
        qty: Number of shares
        take_profit_price: Take-profit limit price
        stop_loss_price: Stop-loss trigger price
        side: "sell" to close a long, "buy" to cover a short
    """
    # OCO side is the EXIT side: sell closes long, buy covers short
    # For sell (closing long): TP > SL (sell high = profit, sell low = stop)
    # For buy (covering short): TP < SL (buy low = profit, buy high = stop)
    err = _validate_order_params(
        qty, take_profit_price, stop_loss_price,
        # Invert validation: OCO sell = closing a long (validate as buy entry)
        # OCO buy = covering a short (validate as sell entry)
        side="buy" if side.lower() == "sell" else "sell",
    )
    if err:
        return OrderResult(success=False, error=f"Validation failed: {err}")
    try:
        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            take_profit={"limit_price": take_profit_price},
            stop_loss={"stop_price": stop_loss_price},
            client_order_id=f"ct_oco_{symbol}_{int(time.time())}",
        )
        order = client.submit_order(order_data)

        legs = []
        for leg in (order.legs or []):
            legs.append({
                "id": str(leg.id),
                "type": str(leg.type),
                "side": str(leg.side),
                "limit_price": str(leg.limit_price) if leg.limit_price else None,
                "stop_price": str(leg.stop_price) if leg.stop_price else None,
                "status": str(leg.status),
            })

        return OrderResult(
            success=True,
            order_id=str(order.id),
            status=str(order.status),
            legs=legs,
        )
    except APIError as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=f"API Error {e.status_code}: {e}")
    except Exception as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

@retry_api()
def close_position(symbol: str, percentage: Optional[float] = None) -> OrderResult:
    """Close a position entirely or partially."""
    try:
        client = get_trading_client()
        opts = ClosePositionRequest(percentage=percentage) if percentage else None
        order = client.close_position(symbol, close_options=opts)
        return OrderResult(
            success=True,
            order_id=str(order.id),
            status=str(order.status),
        )
    except APIError as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=f"API Error {e.status_code}: {e}")
    except Exception as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return OrderResult(success=False, error=str(e))


@retry_api()
def cancel_order(order_id: str) -> bool:
    """Cancel an order by ID. Returns True if successful."""
    try:
        get_trading_client().cancel_order_by_id(order_id)
        return True
    except Exception as e:
        if _is_transient_error(e):
            raise  # let retry_api handle it
        return False


# ---------------------------------------------------------------------------
# Historical orders & activities (for auto-improve analysis)
# ---------------------------------------------------------------------------

@retry_api()
def get_closed_orders(days: int = 30, limit: int = 500) -> list[dict]:
    """Return closed/filled orders from the last N days."""
    orders = get_trading_client().get_orders(
        GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=datetime.now(timezone.utc) - timedelta(days=days),
            limit=limit,
        )
    )
    results = []
    for o in orders:
        entry = {
            "id": str(o.id),
            "client_order_id": str(o.client_order_id) if o.client_order_id else None,
            "symbol": o.symbol,
            "side": str(o.side),
            "qty": str(o.qty),
            "filled_qty": str(o.filled_qty) if o.filled_qty else "0",
            "type": str(o.type),
            "order_class": str(o.order_class),
            "status": str(o.status),
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "limit_price": float(o.limit_price) if o.limit_price else None,
            "stop_price": float(o.stop_price) if o.stop_price else None,
            "submitted_at": str(o.submitted_at) if o.submitted_at else None,
            "filled_at": str(o.filled_at) if o.filled_at else None,
            "expired_at": str(o.expired_at) if o.expired_at else None,
            "canceled_at": str(o.canceled_at) if o.canceled_at else None,
            "legs": [
                {
                    "id": str(leg.id),
                    "type": str(leg.type),
                    "side": str(leg.side),
                    "qty": str(leg.qty),
                    "filled_qty": str(leg.filled_qty) if leg.filled_qty else "0",
                    "filled_avg_price": float(leg.filled_avg_price) if leg.filled_avg_price else None,
                    "limit_price": float(leg.limit_price) if leg.limit_price else None,
                    "stop_price": float(leg.stop_price) if leg.stop_price else None,
                    "status": str(leg.status),
                }
                for leg in (o.legs or [])
            ],
        }
        results.append(entry)
    return results


@retry_api()
def get_portfolio_history(days: int = 30) -> dict:
    """Return portfolio equity history for performance analysis."""
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    client = get_trading_client()
    history = client.get_portfolio_history(
        GetPortfolioHistoryRequest(
            period=f"{days}D",
            timeframe="1D",
        )
    )
    return {
        "timestamps": [str(t) for t in (history.timestamp or [])],
        "equity": [float(e) for e in (history.equity or [])],
        "profit_loss": [float(p) for p in (history.profit_loss or [])],
        "profit_loss_pct": [float(p) for p in (history.profit_loss_pct or [])],
        "base_value": float(history.base_value) if history.base_value else None,
    }
