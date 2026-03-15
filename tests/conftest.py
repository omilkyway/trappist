"""Shared fixtures for claude-trading tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def ohlcv_bullish() -> pd.DataFrame:
    """60-bar OHLCV DataFrame with a clear uptrend (good for long signals)."""
    np.random.seed(42)
    n = 60
    # Steady uptrend: price goes from 100 to ~130
    base = np.linspace(100, 130, n) + np.random.normal(0, 0.5, n)
    high = base + np.random.uniform(0.5, 2.0, n)
    low = base - np.random.uniform(0.5, 2.0, n)
    close = base + np.random.normal(0, 0.3, n)
    opn = close + np.random.normal(0, 0.3, n)
    volume = np.random.randint(1_000_000, 5_000_000, n).astype(float)

    return pd.DataFrame({
        "open": opn,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture()
def ohlcv_bearish() -> pd.DataFrame:
    """60-bar OHLCV DataFrame with a clear downtrend (good for short signals)."""
    np.random.seed(99)
    n = 60
    base = np.linspace(130, 95, n) + np.random.normal(0, 0.5, n)
    high = base + np.random.uniform(0.5, 2.0, n)
    low = base - np.random.uniform(0.5, 2.0, n)
    close = base + np.random.normal(0, 0.3, n)
    opn = close + np.random.normal(0, 0.3, n)
    volume = np.random.randint(1_000_000, 5_000_000, n).astype(float)

    return pd.DataFrame({
        "open": opn,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture()
def sample_positions() -> list[dict]:
    """Sample positions list mimicking Alpaca API output."""
    return [
        {
            "symbol": "AAPL",
            "qty": 10.0,
            "side": "PositionSide.LONG",
            "avg_entry_price": 175.0,
            "market_value": 1780.0,
            "unrealized_pl": 30.0,
            "unrealized_plpc": 0.017,
            "current_price": 178.0,
        },
        {
            "symbol": "XOM",
            "qty": 20.0,
            "side": "PositionSide.LONG",
            "avg_entry_price": 105.0,
            "market_value": 2140.0,
            "unrealized_pl": 40.0,
            "unrealized_plpc": 0.019,
            "current_price": 107.0,
        },
    ]


@pytest.fixture()
def sample_orders() -> list[dict]:
    """Sample open orders list mimicking Alpaca API output."""
    return [
        {
            "id": "order-1",
            "symbol": "AAPL",
            "side": "sell",
            "qty": "10",
            "type": "limit",
            "order_class": "OrderClass.OCO",
            "status": "accepted",
            "limit_price": "190.00",
            "stop_price": None,
            "legs": [
                {
                    "id": "leg-1",
                    "type": "stop",
                    "side": "sell",
                    "limit_price": None,
                    "stop_price": "165.00",
                    "status": "held",
                }
            ],
        },
    ]


@pytest.fixture()
def sample_protections() -> list[dict]:
    """Sample pending protections for protector tests."""
    return [
        {
            "symbol": "NVDA",
            "qty": 15,
            "direction": "LONG",
            "oco_side": "sell",
            "tp": 145.0,
            "sl": 125.0,
        },
        {
            "symbol": "AMD",
            "qty": 30,
            "direction": "SHORT",
            "oco_side": "buy",
            "tp": 80.0,
            "sl": 105.0,
        },
    ]
