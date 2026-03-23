"""CCXT Binance Futures client wrapper.

Provides a thin layer over ccxt.binance that:
- Loads credentials from env vars or keys.local.json
- Supports testnet/live mode switching
- Exposes bracket orders, position management, funding rates
- Returns plain dicts the agents and executor can consume
- Retries transient errors with exponential backoff
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(".env.local", override=True)


# ---------------------------------------------------------------------------
# Retry decorator for transient API failures
# ---------------------------------------------------------------------------

def _is_transient_error(exc: Exception) -> bool:
    """Return True if the error is likely transient and worth retrying."""
    if isinstance(exc, (ccxt.RateLimitExceeded, ccxt.RequestTimeout,
                        ccxt.NetworkError, ccxt.ExchangeNotAvailable)):
        return True
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
            raise last_exc  # type: ignore
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _get_credentials() -> tuple[str, str, bool]:
    """Return (api_key, secret, is_sandbox) from env vars or keys.local.json."""
    def _env(name: str) -> str:
        """Get env var, returning '' if it's a placeholder."""
        val = os.environ.get(name, "")
        if "REPLACE" in val or "your_" in val:
            return ""
        return val

    # Try env vars (support multiple naming conventions)
    key = _env("BINANCE_API_KEY") or _env("BINANCE_KEY_API") or ""
    secret = (_env("BINANCE_API_SECRET") or _env("BINANCE_SECRET")
              or _env("BINANCE_KEY_SECRET") or "")
    sandbox = os.environ.get("LIVE_MODE", "").lower() not in ("true", "1", "yes")

    # Fall back to keys.local.json
    if not key or not secret:
        keys_path = Path(__file__).resolve().parent.parent / "keys.local.json"
        if keys_path.exists():
            try:
                with open(keys_path) as f:
                    keys = json.load(f)
                binance_keys = keys.get("binance", {})
                key = key or binance_keys.get("apiKey", "")
                secret = secret or binance_keys.get("secret", "")
            except (json.JSONDecodeError, KeyError):
                pass

    return key, secret, sandbox


# ---------------------------------------------------------------------------
# Singleton exchange client
# ---------------------------------------------------------------------------

_exchange: Optional[ccxt.binanceusdm] = None


def get_exchange() -> ccxt.binanceusdm:
    """Return a configured ccxt.binanceusdm instance (USDT-M Futures only).

    Uses binanceusdm (not binance) to avoid loading spot/margin markets
    which require authentication. binanceusdm only loads futures markets.

    Binance deprecated the old futures testnet. Use Demo Trading keys
    (generated from Binance app) or real keys with LIVE_MODE=true.
    """
    global _exchange
    if _exchange is None:
        key, secret, sandbox = _get_credentials()

        config = {
            "apiKey": key if key and "REPLACE" not in key else "",
            "secret": secret if secret and "REPLACE" not in secret else "",
            "options": {
                "adjustForTimeDifference": True,
                "fetchCurrencies": False,     # avoid sapi auth calls
                "fetchMarginMarkets": False,  # avoid sapi margin calls
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
            "enableRateLimit": True,
        }

        _exchange = ccxt.binanceusdm(config)

        # If sandbox/testnet mode, override URLs manually
        # (CCXT v4+ blocks set_sandbox_mode for binanceusdm)
        if sandbox:
            testnet = "https://testnet.binancefuture.com"
            for url_key in list(_exchange.urls.get("api", {}).keys()):
                url = _exchange.urls["api"][url_key]
                if isinstance(url, str):
                    if "fapi.binance.com" in url:
                        _exchange.urls["api"][url_key] = url.replace(
                            "https://fapi.binance.com", testnet
                        )
                    elif "api.binance.com" in url:
                        _exchange.urls["api"][url_key] = url.replace(
                            "https://api.binance.com", testnet
                        )

        _exchange.load_markets()
    return _exchange


def reset_exchange():
    """Reset the singleton (useful for testing or credential rotation)."""
    global _exchange
    _exchange = None


def is_sandbox() -> bool:
    """Return True if running in testnet mode."""
    _, _, sandbox = _get_credentials()
    return sandbox


# ---------------------------------------------------------------------------
# Account & positions
# ---------------------------------------------------------------------------

@retry_api()
def get_balance() -> dict:
    """Return account balance info as a plain dict."""
    exchange = get_exchange()
    balance = exchange.fetch_balance()
    usdt = balance.get("USDT", {})
    total_usdt = float(usdt.get("total", 0) or 0)
    free_usdt = float(usdt.get("free", 0) or 0)
    used_usdt = float(usdt.get("used", 0) or 0)

    return {
        "total": total_usdt,
        "free": free_usdt,
        "used": used_usdt,
        "currency": "USDT",
        "sandbox": is_sandbox(),
    }


@retry_api()
def get_account() -> dict:
    """Return account summary including balance, margin, and positions value."""
    exchange = get_exchange()
    balance = exchange.fetch_balance()
    usdt = balance.get("USDT", {})

    # Fetch positions to calculate unrealized PnL and exposure
    positions = get_positions()
    total_unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
    total_exposure = sum(abs(p.get("notional", 0)) for p in positions)

    total_usdt = float(usdt.get("total", 0) or 0)
    free_usdt = float(usdt.get("free", 0) or 0)

    return {
        "equity": total_usdt,
        "free": free_usdt,
        "used": float(usdt.get("used", 0) or 0),
        "unrealized_pnl": round(total_unrealized_pnl, 4),
        "total_exposure": round(total_exposure, 2),
        "exposure_pct": round(total_exposure / total_usdt * 100, 2) if total_usdt > 0 else 0,
        "positions_count": len(positions),
        "currency": "USDT",
        "sandbox": is_sandbox(),
    }


@retry_api()
def get_positions() -> list[dict]:
    """Return all open positions as list of dicts."""
    exchange = get_exchange()
    positions = exchange.fetch_positions()
    result = []
    for p in positions:
        contracts = float(p.get("contracts", 0) or 0)
        if contracts == 0:
            continue
        entry_price = float(p.get("entryPrice", 0) or 0)
        mark_price = float(p.get("markPrice", 0) or 0)
        notional = float(p.get("notional", 0) or 0)
        unrealized_pnl = float(p.get("unrealizedPnl", 0) or 0)
        leverage = float(p.get("leverage", 1) or 1)
        liq_price = float(p.get("liquidationPrice", 0) or 0)
        margin = float(p.get("initialMargin", 0) or p.get("collateral", 0) or 0)

        side = p.get("side", "long")
        symbol = p.get("symbol", "")

        # Calculate PnL percentage
        pnl_pct = 0.0
        if entry_price > 0 and contracts > 0:
            if side == "long":
                pnl_pct = (mark_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - mark_price) / entry_price * 100

        result.append({
            "symbol": symbol,
            "side": side,
            "contracts": contracts,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "notional": abs(notional),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "leverage": leverage,
            "liquidation_price": liq_price,
            "margin": round(margin, 4),
            "margin_mode": p.get("marginMode", "cross"),
            "timestamp": p.get("timestamp"),
        })
    return result


@retry_api()
def get_open_orders(symbol: str | None = None) -> list[dict]:
    """Return all open orders as list of dicts."""
    exchange = get_exchange()
    orders = exchange.fetch_open_orders(symbol)
    return [
        {
            "id": str(o["id"]),
            "symbol": o["symbol"],
            "side": o["side"],
            "type": o["type"],
            "amount": float(o.get("amount", 0) or 0),
            "price": float(o.get("price", 0) or 0),
            "stop_price": float(o.get("stopPrice", 0) or 0),
            "status": o["status"],
            "reduce_only": o.get("reduceOnly", False),
            "timestamp": o.get("timestamp"),
            "datetime": o.get("datetime"),
        }
        for o in orders
    ]


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@retry_api()
def get_ticker(symbol: str) -> dict:
    """Get current ticker (price, bid, ask, volume, change)."""
    exchange = get_exchange()
    t = exchange.fetch_ticker(symbol)
    return {
        "symbol": t["symbol"],
        "last": float(t.get("last", 0) or 0),
        "bid": float(t.get("bid", 0) or 0),
        "ask": float(t.get("ask", 0) or 0),
        "spread": round(float(t.get("ask", 0) or 0) - float(t.get("bid", 0) or 0), 6),
        "high_24h": float(t.get("high", 0) or 0),
        "low_24h": float(t.get("low", 0) or 0),
        "volume_24h": float(t.get("quoteVolume", 0) or 0),
        "change_pct": float(t.get("percentage", 0) or 0),
        "timestamp": t.get("timestamp"),
    }


@retry_api()
def get_bars(
    symbol: str,
    timeframe: str = "4h",
    limit: int = 500,
    since: int | None = None,
):
    """Fetch OHLCV bars and return a pandas DataFrame.

    Args:
        symbol: Trading pair (e.g., 'BTC/USDT:USDT')
        timeframe: Candle timeframe (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w)
        limit: Number of candles (max 1500 on Binance Futures)
        since: Start timestamp in ms (optional)
    """
    import pandas as pd

    exchange = get_exchange()
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    if not ohlcv:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


@retry_api()
def get_funding_rate(symbol: str) -> dict:
    """Get current funding rate for a symbol."""
    exchange = get_exchange()
    try:
        fr = exchange.fetch_funding_rate(symbol)
        return {
            "symbol": fr.get("symbol", symbol),
            "funding_rate": float(fr.get("fundingRate", 0) or 0),
            "funding_rate_pct": round(float(fr.get("fundingRate", 0) or 0) * 100, 4),
            "next_funding_time": fr.get("fundingDatetime"),
            "mark_price": float(fr.get("markPrice", 0) or 0),
            "index_price": float(fr.get("indexPrice", 0) or 0),
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "funding_rate": 0,
            "funding_rate_pct": 0,
            "error": str(e),
        }


@retry_api()
def get_funding_history(symbol: str, limit: int = 100) -> list[dict]:
    """Get historical funding rates."""
    exchange = get_exchange()
    try:
        history = exchange.fetch_funding_rate_history(symbol, limit=limit)
        return [
            {
                "timestamp": h.get("timestamp"),
                "datetime": h.get("datetime"),
                "funding_rate": float(h.get("fundingRate", 0) or 0),
            }
            for h in history
        ]
    except Exception:
        return []


@retry_api()
def get_market_info(symbol: str) -> dict:
    """Get market info for a symbol (precision, limits, max leverage)."""
    exchange = get_exchange()
    market = exchange.market(symbol)
    return {
        "symbol": market["symbol"],
        "base": market["base"],
        "quote": market["quote"],
        "active": market["active"],
        "type": market.get("type", "swap"),
        "linear": market.get("linear", True),
        "contract_size": float(market.get("contractSize", 1) or 1),
        "precision": {
            "amount": market["precision"].get("amount"),
            "price": market["precision"].get("price"),
        },
        "limits": {
            "amount_min": float(market["limits"]["amount"]["min"] or 0),
            "amount_max": float(market["limits"]["amount"].get("max") or 0),
            "price_min": float(market["limits"]["price"]["min"] or 0),
            "cost_min": float(market["limits"]["cost"]["min"] or 0) if market["limits"].get("cost") else 0,
        },
        "maker_fee": float(market.get("maker", 0) or 0),
        "taker_fee": float(market.get("taker", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Leverage & margin
# ---------------------------------------------------------------------------

@retry_api()
def set_leverage(symbol: str, leverage: int) -> dict:
    """Set leverage for a symbol."""
    exchange = get_exchange()
    try:
        result = exchange.set_leverage(leverage, symbol)
        return {"success": True, "symbol": symbol, "leverage": leverage, "result": str(result)}
    except ccxt.ExchangeError as e:
        # "No need to change leverage" is OK
        if "no need" in str(e).lower() or "not modified" in str(e).lower():
            return {"success": True, "symbol": symbol, "leverage": leverage, "note": "already set"}
        return {"success": False, "error": str(e)}


@retry_api()
def set_margin_mode(symbol: str, mode: str = "isolated") -> dict:
    """Set margin mode (isolated or cross) for a symbol."""
    exchange = get_exchange()
    try:
        result = exchange.set_margin_mode(mode, symbol)
        return {"success": True, "symbol": symbol, "mode": mode, "result": str(result)}
    except ccxt.ExchangeError as e:
        # "No need to change margin type" is OK
        if "no need" in str(e).lower() or "not modified" in str(e).lower():
            return {"success": True, "symbol": symbol, "mode": mode, "note": "already set"}
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

class OrderResult:
    """Encapsulates the result of an order operation."""

    def __init__(self, success: bool, order_id: str | None = None,
                 status: str | None = None, details: dict | None = None,
                 error: str | None = None):
        self.success = success
        self.order_id = order_id
        self.status = status
        self.details = details or {}
        self.error = error

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "status": self.status,
            "details": self.details,
            "error": self.error,
        }


def _validate_bracket_params(
    qty: float, tp: float, sl: float, side: str, entry: float | None = None,
) -> str | None:
    """Validate bracket order parameters. Returns error message or None."""
    if qty <= 0:
        return f"qty must be > 0, got {qty}"
    if tp <= 0:
        return f"take_profit must be > 0, got {tp}"
    if sl <= 0:
        return f"stop_loss must be > 0, got {sl}"

    if side.lower() == "buy":
        # LONG: TP above entry/current, SL below
        if tp <= sl:
            return f"LONG: take_profit ({tp}) must be > stop_loss ({sl})"
        if entry and tp <= entry:
            return f"LONG: take_profit ({tp}) must be > entry ({entry})"
        if entry and sl >= entry:
            return f"LONG: stop_loss ({sl}) must be < entry ({entry})"
    elif side.lower() == "sell":
        # SHORT: TP below entry/current, SL above
        if tp >= sl:
            return f"SHORT: take_profit ({tp}) must be < stop_loss ({sl})"
        if entry and tp >= entry:
            return f"SHORT: take_profit ({tp}) must be < entry ({entry})"
        if entry and sl <= entry:
            return f"SHORT: stop_loss ({sl}) must be > entry ({entry})"
    else:
        return f"side must be 'buy' or 'sell', got '{side}'"

    return None


@retry_api()
def place_bracket_order(
    symbol: str,
    qty: float,
    tp: float,
    sl: float,
    side: str = "buy",
    entry_price: float | None = None,
    leverage: int = 5,
) -> OrderResult:
    """Place a bracket order: entry + stop-loss + take-profit.

    If entry_price is given, uses a LIMIT entry. Otherwise MARKET.
    SL and TP are placed as separate reduce-only orders after entry.

    Args:
        symbol: Trading pair (e.g., 'BTC/USDT:USDT')
        qty: Position size in base currency (e.g., 0.01 BTC)
        tp: Take-profit trigger price
        sl: Stop-loss trigger price
        side: 'buy' for LONG, 'sell' for SHORT
        entry_price: Limit entry price (None = market order)
        leverage: Leverage multiplier (default 5)
    """
    err = _validate_bracket_params(qty, tp, sl, side, entry_price)
    if err:
        return OrderResult(success=False, error=f"Validation failed: {err}")

    try:
        exchange = get_exchange()

        # Set leverage and margin mode first
        set_leverage(symbol, leverage)
        set_margin_mode(symbol, "isolated")

        # Determine order type and close side for SL/TP
        order_type = "limit" if entry_price else "market"
        price = entry_price if entry_price else None
        close_side = "sell" if side.lower() == "buy" else "buy"

        # Step 1: Place entry order
        order = exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side.lower(),
            amount=qty,
            price=price,
        )

        entry_id = str(order["id"])
        entry_status = order.get("status", "unknown")
        fill_price = entry_price or order.get("average") or order.get("price")

        # Step 2: Place SL and TP as separate reduce-only orders
        # (Binance Futures doesn't reliably link SL/TP to market entry via params)
        sl_id = None
        tp_id = None
        protection_errors = []

        try:
            sl_order = exchange.create_order(
                symbol=symbol, type="STOP_MARKET", side=close_side, amount=qty,
                params={"stopPrice": sl, "reduceOnly": True},
            )
            sl_id = str(sl_order["id"])
        except Exception as e:
            protection_errors.append(f"SL failed: {e}")

        try:
            tp_order = exchange.create_order(
                symbol=symbol, type="TAKE_PROFIT_MARKET", side=close_side, amount=qty,
                params={"stopPrice": tp, "reduceOnly": True},
            )
            tp_id = str(tp_order["id"])
        except Exception as e:
            protection_errors.append(f"TP failed: {e}")

        return OrderResult(
            success=True,
            order_id=entry_id,
            status=entry_status,
            details={
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "qty": qty,
                "entry_price": fill_price,
                "tp": tp,
                "sl": sl,
                "leverage": leverage,
                "sl_order_id": sl_id,
                "tp_order_id": tp_id,
                "protection_errors": protection_errors or None,
                "timestamp": order.get("datetime"),
            },
        )
    except ccxt.InsufficientFunds as e:
        return OrderResult(success=False, error=f"Insufficient funds: {e}")
    except ccxt.InvalidOrder as e:
        return OrderResult(success=False, error=f"Invalid order: {e}")
    except ccxt.ExchangeError as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=f"Exchange error: {e}")
    except Exception as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=str(e))


@retry_api()
def place_market_order(
    symbol: str,
    qty: float,
    side: str = "buy",
    reduce_only: bool = False,
    leverage: int = 5,
) -> OrderResult:
    """Place a simple market order."""
    try:
        exchange = get_exchange()
        if not reduce_only:
            set_leverage(symbol, leverage)
            set_margin_mode(symbol, "isolated")

        params = {"reduceOnly": reduce_only}
        order = exchange.create_order(
            symbol=symbol,
            type="market",
            side=side.lower(),
            amount=qty,
            params=params,
        )
        return OrderResult(
            success=True,
            order_id=str(order["id"]),
            status=order.get("status", "unknown"),
            details={"symbol": symbol, "side": side, "qty": qty, "reduce_only": reduce_only},
        )
    except Exception as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=str(e))


@retry_api()
def place_stop_order(
    symbol: str,
    qty: float,
    trigger_price: float,
    side: str = "sell",
    reduce_only: bool = True,
) -> OrderResult:
    """Place a stop-market order (typically for stop-loss)."""
    try:
        exchange = get_exchange()
        order = exchange.create_order(
            symbol=symbol,
            type="STOP_MARKET",
            side=side.lower(),
            amount=qty,
            params={"stopPrice": trigger_price, "reduceOnly": reduce_only},
        )
        return OrderResult(
            success=True,
            order_id=str(order["id"]),
            status=order.get("status"),
            details={"type": "stop_market", "trigger": trigger_price},
        )
    except Exception as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=str(e))


@retry_api()
def place_tp_order(
    symbol: str,
    qty: float,
    trigger_price: float,
    side: str = "sell",
    reduce_only: bool = True,
) -> OrderResult:
    """Place a take-profit-market order."""
    try:
        exchange = get_exchange()
        order = exchange.create_order(
            symbol=symbol,
            type="TAKE_PROFIT_MARKET",
            side=side.lower(),
            amount=qty,
            params={"stopPrice": trigger_price, "reduceOnly": reduce_only},
        )
        return OrderResult(
            success=True,
            order_id=str(order["id"]),
            status=order.get("status"),
            details={"type": "take_profit_market", "trigger": trigger_price},
        )
    except Exception as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

@retry_api()
def close_position(symbol: str) -> OrderResult:
    """Close an open position entirely."""
    try:
        positions = get_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            return OrderResult(success=False, error=f"No open position on {symbol}")

        close_side = "sell" if pos["side"] == "long" else "buy"
        qty = pos["contracts"]

        return place_market_order(
            symbol=symbol,
            qty=qty,
            side=close_side,
            reduce_only=True,
        )
    except Exception as e:
        if _is_transient_error(e):
            raise
        return OrderResult(success=False, error=str(e))


@retry_api()
def cancel_order(order_id: str, symbol: str) -> bool:
    """Cancel an order by ID. Returns True if successful."""
    try:
        get_exchange().cancel_order(order_id, symbol)
        return True
    except Exception as e:
        if _is_transient_error(e):
            raise
        logger.warning("Failed to cancel order %s: %s", order_id, e)
        return False


@retry_api()
def cancel_all_orders(symbol: str) -> dict:
    """Cancel all open orders for a symbol."""
    try:
        result = get_exchange().cancel_all_orders(symbol)
        return {"success": True, "symbol": symbol, "cancelled": len(result) if isinstance(result, list) else 1}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Historical data (for auto-improve analysis)
# ---------------------------------------------------------------------------

@retry_api()
def get_closed_orders(symbol: str | None = None, days: int = 30, limit: int = 500) -> list[dict]:
    """Return closed/filled orders from the last N days."""
    exchange = get_exchange()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    all_orders = []
    if symbol:
        all_orders = exchange.fetch_closed_orders(symbol, since=since_ms, limit=limit)
    else:
        # Fetch for all active pairs
        for pair in get_active_pairs():
            try:
                orders = exchange.fetch_closed_orders(pair, since=since_ms, limit=limit)
                all_orders.extend(orders)
            except Exception:
                continue

    return [
        {
            "id": str(o["id"]),
            "symbol": o["symbol"],
            "side": o["side"],
            "type": o["type"],
            "amount": float(o.get("amount", 0) or 0),
            "filled": float(o.get("filled", 0) or 0),
            "price": float(o.get("price", 0) or 0),
            "average": float(o.get("average", 0) or 0),
            "cost": float(o.get("cost", 0) or 0),
            "status": o["status"],
            "reduce_only": o.get("reduceOnly", False),
            "timestamp": o.get("timestamp"),
            "datetime": o.get("datetime"),
        }
        for o in all_orders
    ]


@retry_api()
def get_trades(symbol: str, days: int = 30, limit: int = 500) -> list[dict]:
    """Return trade history (fills) for a symbol."""
    exchange = get_exchange()
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    trades = exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
    return [
        {
            "id": str(t["id"]),
            "order_id": str(t.get("order", "")),
            "symbol": t["symbol"],
            "side": t["side"],
            "amount": float(t["amount"]),
            "price": float(t["price"]),
            "cost": float(t["cost"]),
            "fee": float(t["fee"]["cost"]) if t.get("fee") else 0,
            "timestamp": t.get("timestamp"),
            "datetime": t.get("datetime"),
        }
        for t in trades
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Core pairs always included regardless of filters
CORE_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
]

# Cache for discovered pairs (refreshed every hour)
_discovered_pairs_cache: dict = {"pairs": [], "timestamp": 0}


def discover_futures_pairs(
    min_volume_24h: float = 10_000_000,
    min_leverage: int = 5,
    max_pairs: int = 80,
) -> list[str]:
    """Dynamically discover ALL tradable Binance USDT-M Futures pairs.

    Filters by:
    - Active trading status
    - Minimum 24h volume (default $10M)
    - Minimum leverage available (default 5x)
    Returns pairs sorted by 24h volume descending, capped at max_pairs.
    """
    global _discovered_pairs_cache
    now = time.time()

    # Use cache if fresh (< 1 hour old)
    if _discovered_pairs_cache["pairs"] and (now - _discovered_pairs_cache["timestamp"]) < 3600:
        return _discovered_pairs_cache["pairs"]

    exchange = get_exchange()

    # Get all USDT-M perpetual markets
    all_symbols = []
    for sym, market in exchange.markets.items():
        if (
            market.get("active")
            and market.get("settle") == "USDT"
            and market.get("type") in ("swap", "future")
            and market.get("linear")
        ):
            all_symbols.append(sym)

    logger.info("Discovered %d active USDT-M futures pairs", len(all_symbols))

    # Fetch 24h tickers for volume ranking
    tickers = {}
    try:
        # Try batch fetch first (most efficient)
        tickers = exchange.fetch_tickers(all_symbols)
    except Exception:
        # Fallback: fetch all tickers without symbol filter
        try:
            tickers = exchange.fetch_tickers()
        except Exception:
            logger.warning("Ticker fetch failed entirely, returning all active symbols")
            # No volume data — return all symbols up to max_pairs (sorted alphabetically)
            pairs = all_symbols[:max_pairs]
            for core in CORE_PAIRS:
                if core not in pairs:
                    pairs.append(core)
            _discovered_pairs_cache = {"pairs": pairs, "timestamp": now}
            return pairs

    # Filter and rank by volume
    ranked = []
    for sym in all_symbols:
        ticker = tickers.get(sym, {})
        vol = float(ticker.get("quoteVolume", 0) or 0)
        if vol >= min_volume_24h:
            ranked.append((sym, vol))
        elif sym in CORE_PAIRS:
            ranked.append((sym, vol))  # Always include core

    # Sort by volume descending
    ranked.sort(key=lambda x: x[1], reverse=True)

    # Cap and ensure core pairs are included
    pairs = [sym for sym, _ in ranked[:max_pairs]]
    for core in CORE_PAIRS:
        if core not in pairs:
            pairs.append(core)

    logger.info("Selected %d pairs (min vol $%.0fM, from %d candidates)",
                len(pairs), min_volume_24h / 1e6, len(ranked))

    _discovered_pairs_cache = {"pairs": pairs, "timestamp": now}
    return pairs


def get_active_pairs() -> list[str]:
    """Return list of active trading pairs — dynamically discovered from Binance."""
    try:
        return discover_futures_pairs()
    except Exception as e:
        logger.warning("Dynamic discovery failed (%s), using core pairs", e)
        return CORE_PAIRS


def format_symbol(base: str) -> str:
    """Format a base token into Binance Futures CCXT symbol.

    Examples: 'BTC' → 'BTC/USDT:USDT', 'ETH' → 'ETH/USDT:USDT'
    """
    base = base.upper().strip()
    if "/" in base:
        return base  # already formatted
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def amount_precision(symbol: str) -> int:
    """Get the amount precision (decimal places) for a symbol."""
    try:
        market = get_exchange().market(symbol)
        prec = market["precision"]["amount"]
        if prec is None:
            return 3
        # CCXT may return precision as decimal places (int) or tick size (float)
        if isinstance(prec, int):
            return prec
        # Tick size format (e.g., 0.001 → 3 decimal places)
        import math
        return max(0, -int(math.log10(float(prec))))
    except Exception:
        return 3  # safe default


def price_precision(symbol: str) -> int:
    """Get the price precision (decimal places) for a symbol."""
    try:
        market = get_exchange().market(symbol)
        prec = market["precision"]["price"]
        if prec is None:
            return 2
        if isinstance(prec, int):
            return prec
        import math
        return max(0, -int(math.log10(float(prec))))
    except Exception:
        return 2  # safe default


# ---------------------------------------------------------------------------
# Open Interest — contrarian signal for liquidation cascades
# ---------------------------------------------------------------------------

@retry_api()
def get_open_interest(symbol: str) -> dict:
    """Get current open interest for a symbol (total outstanding contracts)."""
    exchange = get_exchange()
    try:
        # CCXT v4+ method
        oi = exchange.fetch_open_interest(symbol)
        return {
            "symbol": symbol,
            "open_interest": float(oi.get("openInterestAmount", 0) or 0),
            "open_interest_value": float(oi.get("openInterestValue", 0) or 0),
            "timestamp": oi.get("timestamp"),
        }
    except (AttributeError, ccxt.NotSupported):
        # Fallback: try direct Binance API
        try:
            base = symbol.split("/")[0] + "USDT"
            import urllib.request
            req = urllib.request.Request(
                f"https://fapi.binance.com/fapi/v1/openInterest?symbol={base}"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            return {
                "symbol": symbol,
                "open_interest": float(data.get("openInterest", 0)),
                "open_interest_value": 0,
                "timestamp": data.get("time"),
            }
        except Exception:
            return {"symbol": symbol, "open_interest": 0, "open_interest_value": 0}
    except Exception:
        return {"symbol": symbol, "open_interest": 0, "open_interest_value": 0}


@retry_api()
def get_long_short_ratio(symbol: str) -> dict:
    """Get top trader long/short ratio (Binance-specific)."""
    try:
        base = symbol.split("/")[0] + "USDT"
        import urllib.request
        req = urllib.request.Request(
            f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={base}&period=1h&limit=1"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data:
            entry = data[0]
            return {
                "symbol": symbol,
                "long_account": float(entry.get("longAccount", 0.5)),
                "short_account": float(entry.get("shortAccount", 0.5)),
                "long_short_ratio": float(entry.get("longShortRatio", 1.0)),
                "timestamp": entry.get("timestamp"),
            }
    except Exception:
        pass
    return {"symbol": symbol, "long_account": 0.5, "short_account": 0.5, "long_short_ratio": 1.0}


@retry_api()
def get_recent_liquidations(symbol: str, limit: int = 20) -> list[dict]:
    """Get recent forced liquidation orders for a symbol (Binance-specific).

    Liquidation cascades are price magnets — heavy liquidations in one direction
    signal potential reversal or continuation depending on context.
    """
    try:
        base = symbol.split("/")[0] + "USDT"
        import urllib.request
        req = urllib.request.Request(
            f"https://fapi.binance.com/fapi/v1/allForceOrders?symbol={base}&limit={limit}"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return [
            {
                "symbol": symbol,
                "side": entry.get("side", "").lower(),
                "qty": float(entry.get("origQty", 0)),
                "price": float(entry.get("price", 0)),
                "time": entry.get("time"),
            }
            for entry in data
        ]
    except Exception:
        return []
