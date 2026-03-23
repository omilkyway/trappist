"""Tests for trading/indicators.py — technical indicator calculations."""

import numpy as np
import pandas as pd
import pytest

from trading.indicators import (
    atr,
    bollinger_bands,
    compute_signals,
    ema,
    funding_rate_signal,
    macd,
    rsi,
    sma,
    volume_ratio,
)


def _make_ohlcv(n=100, start_price=100.0, trend=0.001, seed=42):
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    closes = [start_price]
    for _ in range(n - 1):
        change = trend + rng.normal(0, 0.02)
        closes.append(closes[-1] * (1 + change))
    closes = np.array(closes)
    highs = closes * (1 + rng.uniform(0.001, 0.02, n))
    lows = closes * (1 - rng.uniform(0.001, 0.02, n))
    opens = closes * (1 + rng.normal(0, 0.005, n))
    volumes = rng.uniform(1000, 5000, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


class TestSMA:
    def test_basic(self):
        s = pd.Series([1.0, 2, 3, 4, 5])
        result = sma(s, period=3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)


class TestEMA:
    def test_basic(self):
        s = pd.Series([1.0, 2, 3, 4, 5])
        result = ema(s, period=3)
        assert len(result) == 5
        assert result.iloc[-1] > result.iloc[0]

    def test_constant_series(self):
        s = pd.Series([10.0] * 20)
        result = ema(s, period=5)
        assert result.iloc[-1] == pytest.approx(10.0)


class TestRSI:
    def test_uptrend_high_rsi(self):
        s = pd.Series(np.linspace(100, 200, 50))
        result = rsi(s, period=14)
        assert result.iloc[-1] > 70

    def test_downtrend_low_rsi(self):
        s = pd.Series(np.linspace(200, 100, 50))
        result = rsi(s, period=14)
        assert result.iloc[-1] < 30

    def test_range_0_100(self):
        df = _make_ohlcv(100)
        result = rsi(df["close"])
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestMACD:
    def test_returns_three_columns(self):
        df = _make_ohlcv(100)
        result = macd(df["close"])
        assert "macd_line" in result.columns
        assert "signal_line" in result.columns
        assert "histogram" in result.columns

    def test_histogram_is_diff(self):
        df = _make_ohlcv(100)
        result = macd(df["close"])
        diff = result["macd_line"] - result["signal_line"]
        np.testing.assert_array_almost_equal(result["histogram"].values, diff.values)


class TestBollingerBands:
    def test_structure(self):
        df = _make_ohlcv(100)
        bb = bollinger_bands(df["close"])
        assert "upper" in bb.columns
        assert "lower" in bb.columns
        assert "pct_b" in bb.columns

    def test_upper_above_lower(self):
        df = _make_ohlcv(100)
        bb = bollinger_bands(df["close"]).dropna()
        assert (bb["upper"] >= bb["lower"]).all()


class TestATR:
    def test_positive(self):
        df = _make_ohlcv(100)
        result = atr(df["high"], df["low"], df["close"])
        valid = result.dropna()
        assert (valid > 0).all()


class TestVolumeRatio:
    def test_average_is_one(self):
        v = pd.Series([100.0] * 30)
        result = volume_ratio(v, period=20)
        assert result.iloc[-1] == pytest.approx(1.0)

    def test_spike_detected(self):
        v = pd.Series([100.0] * 25 + [300.0])
        result = volume_ratio(v, period=20)
        assert result.iloc[-1] > 2.0


class TestFundingRateSignal:
    def test_neutral(self):
        result = funding_rate_signal(0.0)
        assert result["long_score"] == 0
        assert result["short_score"] == 0

    def test_negative_long_bias(self):
        result = funding_rate_signal(-0.03)
        assert result["long_score"] > 0
        assert result["short_score"] == 0

    def test_extreme_negative_strong_long(self):
        result = funding_rate_signal(-0.08)
        assert result["long_score"] == 5

    def test_positive_short_bias(self):
        result = funding_rate_signal(0.06)
        assert result["short_score"] > 0
        assert result["long_score"] == 0

    def test_extreme_positive_strong_short(self):
        result = funding_rate_signal(0.12)
        assert result["short_score"] == 5


class TestComputeSignals:
    def test_returns_required_keys(self):
        df = _make_ohlcv(200)
        result = compute_signals(df)
        assert "price" in result
        assert "indicators" in result
        assert "signals" in result
        assert "long_score" in result["signals"]
        assert "short_score" in result["signals"]

    def test_scores_in_range(self):
        df = _make_ohlcv(200)
        result = compute_signals(df)
        assert 0 <= result["signals"]["long_score"] <= 100
        assert 0 <= result["signals"]["short_score"] <= 100

    def test_uptrend_favors_long(self):
        df = _make_ohlcv(200, trend=0.005)  # Strong uptrend
        result = compute_signals(df)
        assert result["signals"]["long_score"] > result["signals"]["short_score"]

    def test_downtrend_favors_short(self):
        df = _make_ohlcv(200, trend=-0.005)  # Strong downtrend
        result = compute_signals(df)
        assert result["signals"]["short_score"] > result["signals"]["long_score"]

    def test_insufficient_data_raises(self):
        df = _make_ohlcv(30)
        with pytest.raises(ValueError, match="at least 50 bars"):
            compute_signals(df)

    def test_with_funding_rate(self):
        df = _make_ohlcv(200)
        result = compute_signals(df, funding_rate=-0.08)
        assert result["signals"].get("funding_rate") is not None

    def test_suggested_sl_tp_present(self):
        df = _make_ohlcv(200)
        result = compute_signals(df)
        if result.get("suggested_sl_tp"):
            assert "long" in result["suggested_sl_tp"]
            assert "short" in result["suggested_sl_tp"]
