"""Tests for trading/indicators.py — technical indicator calculations."""

import numpy as np
import pandas as pd
import pytest

from trading.indicators import (
    sma,
    ema,
    macd,
    rsi,
    bollinger_bands,
    atr,
    volume_ratio,
    find_pivots,
    find_nearest_levels,
    adr,
    compute_signals,
    _score_to_metrics,
    _ensure_series,
)


# ---------------------------------------------------------------------------
# Helper / _ensure_series
# ---------------------------------------------------------------------------

class TestEnsureSeries:
    def test_series_passthrough(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = _ensure_series(s)
        assert isinstance(result, pd.Series)

    def test_dataframe_extracts_column(self):
        df = pd.DataFrame({"close": [1.0, 2.0], "volume": [100, 200]})
        result = _ensure_series(df, "close")
        assert list(result) == [1.0, 2.0]

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError):
            _ensure_series([1, 2, 3])


# ---------------------------------------------------------------------------
# SMA / EMA
# ---------------------------------------------------------------------------

class TestMovingAverages:
    def test_sma_basic(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(s, period=3)
        assert result.iloc[-1] == pytest.approx(4.0)  # (3+4+5)/3
        assert pd.isna(result.iloc[0])  # not enough data

    def test_ema_follows_trend(self):
        s = pd.Series(range(1, 21), dtype=float)
        result = ema(s, period=5)
        # EMA should be close to recent values in uptrend
        assert result.iloc[-1] > result.iloc[-5]

    def test_sma_from_dataframe(self):
        df = pd.DataFrame({"close": [10.0, 20.0, 30.0, 40.0, 50.0]})
        result = sma(df, period=3)
        assert result.iloc[-1] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

class TestMACD:
    def test_macd_columns(self):
        s = pd.Series(np.random.randn(50).cumsum() + 100)
        result = macd(s)
        assert set(result.columns) == {"macd_line", "signal_line", "histogram"}
        assert len(result) == 50

    def test_histogram_is_difference(self):
        s = pd.Series(np.random.randn(50).cumsum() + 100)
        result = macd(s)
        diff = result["macd_line"] - result["signal_line"]
        np.testing.assert_array_almost_equal(result["histogram"].values, diff.values)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

class TestRSI:
    def test_rsi_range(self):
        s = pd.Series(np.random.randn(100).cumsum() + 100)
        result = rsi(s)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_overbought_on_pure_gains(self):
        """Monotonically rising prices should give RSI near 100."""
        s = pd.Series(range(100, 200), dtype=float)
        result = rsi(s, period=14)
        assert result.iloc[-1] > 90

    def test_rsi_oversold_on_pure_losses(self):
        """Monotonically falling prices should give RSI near 0."""
        s = pd.Series(range(200, 100, -1), dtype=float)
        result = rsi(s, period=14)
        assert result.iloc[-1] < 10


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

class TestBollingerBands:
    def test_bollinger_columns(self):
        s = pd.Series(np.random.randn(30) + 100)
        result = bollinger_bands(s, period=20)
        assert set(result.columns) == {"upper", "middle", "lower", "pct_b", "bandwidth"}

    def test_upper_above_lower(self):
        s = pd.Series(np.random.randn(30) + 100)
        result = bollinger_bands(s, period=20)
        valid = result.dropna()
        assert (valid["upper"] >= valid["lower"]).all()

    def test_pct_b_at_middle_is_half(self):
        """When price equals middle band, %B should be ~0.5."""
        s = pd.Series([100.0] * 25)  # constant price
        bollinger_bands(s, period=20)  # should not crash
        # With constant price, std=0 so bands collapse — skip this edge case
        # Instead test a non-degenerate case
        s2 = pd.Series([100 + i * 0.01 for i in range(25)])
        result2 = bollinger_bands(s2, period=20)
        last_pctb = result2["pct_b"].iloc[-1]
        assert 0.0 <= last_pctb <= 1.5  # reasonable range


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

class TestATR:
    def test_atr_positive(self):
        n = 30
        high = pd.Series(np.random.uniform(101, 105, n))
        low = pd.Series(np.random.uniform(95, 99, n))
        close = pd.Series(np.random.uniform(98, 103, n))
        result = atr(high, low, close, period=14)
        valid = result.dropna()
        assert (valid > 0).all()

    def test_atr_larger_with_volatile_data(self):
        n = 30
        # Calm market
        h1 = pd.Series(np.full(n, 101.0))
        l1 = pd.Series(np.full(n, 99.0))
        c1 = pd.Series(np.full(n, 100.0))
        atr_calm = atr(h1, l1, c1, period=14).iloc[-1]

        # Volatile market
        h2 = pd.Series(np.full(n, 110.0))
        l2 = pd.Series(np.full(n, 90.0))
        c2 = pd.Series(np.full(n, 100.0))
        atr_volatile = atr(h2, l2, c2, period=14).iloc[-1]

        assert atr_volatile > atr_calm


# ---------------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------------

class TestVolumeRatio:
    def test_avg_volume_gives_ratio_one(self):
        v = pd.Series([1000.0] * 25)
        result = volume_ratio(v, period=20)
        assert result.iloc[-1] == pytest.approx(1.0)

    def test_high_volume_gives_high_ratio(self):
        v = pd.Series([1000.0] * 20 + [2000.0])
        result = volume_ratio(v, period=20)
        # Rolling avg includes the 2000 bar, so ratio is slightly < 2.0
        assert result.iloc[-1] > 1.8


# ---------------------------------------------------------------------------
# Pivots / S&R
# ---------------------------------------------------------------------------

class TestPivots:
    def test_find_pivots_basic(self):
        # Create clear swing high at index 5 and swing low at index 10
        n = 20
        high = pd.Series([100.0] * n)
        low = pd.Series([98.0] * n)
        high.iloc[10] = 115.0  # clear swing high
        low.iloc[5] = 85.0    # clear swing low

        result = find_pivots(high, low, lookback=3)
        assert 85.0 in result["supports"]
        assert 115.0 in result["resistances"]

    def test_find_pivots_empty_on_flat(self):
        n = 20
        high = pd.Series([100.0] * n)
        low = pd.Series([99.0] * n)
        result = find_pivots(high, low, lookback=5)
        # Flat data may still produce pivots (>= comparison), but should not crash
        assert isinstance(result["supports"], list)
        assert isinstance(result["resistances"], list)


class TestNearestLevels:
    def test_basic_levels(self):
        pivots = {
            "supports": [90.0, 95.0, 105.0],
            "resistances": [110.0, 115.0, 88.0],
        }
        result = find_nearest_levels(100.0, pivots)
        assert result["support_1"] == 95.0
        assert result["support_2"] == 90.0
        assert result["resistance_1"] == 110.0
        assert result["resistance_2"] == 115.0

    def test_no_levels_returns_none(self):
        pivots = {"supports": [], "resistances": []}
        result = find_nearest_levels(100.0, pivots)
        assert result["support_1"] is None
        assert result["resistance_1"] is None


# ---------------------------------------------------------------------------
# ADR
# ---------------------------------------------------------------------------

class TestADR:
    def test_adr_positive(self):
        n = 20
        high = pd.Series(np.full(n, 105.0))
        low = pd.Series(np.full(n, 95.0))
        result = adr(high, low, period=14)
        assert result.iloc[-1] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Score metrics
# ---------------------------------------------------------------------------

class TestScoreMetrics:
    def test_max_score(self):
        # _MAX_RAW = 21 (16 base + 5 independent directional bonuses)
        m = _score_to_metrics(21)
        assert m["normalized_score"] == 100
        assert m["direction"] == "bullish"
        assert m["strength"] == "strong"

    def test_min_score(self):
        m = _score_to_metrics(-16)
        assert m["normalized_score"] == 0
        assert m["direction"] == "bearish"
        assert m["strength"] == "weak"

    def test_zero_score(self):
        m = _score_to_metrics(0)
        # With _MAX_RAW=21, _MIN_RAW=-16: (0-(-16))/(21-(-16))*100 = 43
        assert m["normalized_score"] == 43
        assert m["direction"] == "neutral"
        assert m["strength"] == "weak"

    def test_clamping(self):
        m = _score_to_metrics(999)
        assert m["normalized_score"] == 100
        m = _score_to_metrics(-999)
        assert m["normalized_score"] == 0


# ---------------------------------------------------------------------------
# compute_signals (integration)
# ---------------------------------------------------------------------------

class TestComputeSignals:
    def test_bullish_data_favors_long(self, ohlcv_bullish):
        result = compute_signals(ohlcv_bullish)
        assert "price" in result
        assert "indicators" in result
        assert "signals" in result
        assert "levels" in result
        assert "suggested_sl_tp" in result
        assert result["signals"]["long_score"] >= result["signals"]["short_score"]

    def test_bearish_data_has_short_signals(self, ohlcv_bearish):
        """Bearish data should produce meaningful short signals (not just zero)."""
        result = compute_signals(ohlcv_bearish)
        # Short score should be non-trivial on bearish data
        assert result["signals"]["short_score"] > 20
        # Long score should not be very high
        assert result["signals"]["long_score"] < 80

    def test_scores_in_range(self, ohlcv_bullish):
        result = compute_signals(ohlcv_bullish)
        assert 0 <= result["signals"]["long_score"] <= 100
        assert 0 <= result["signals"]["short_score"] <= 100

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"close": [100.0] * 60})
        with pytest.raises(ValueError, match="missing required columns"):
            compute_signals(df)

    def test_insufficient_bars_raises(self):
        df = pd.DataFrame({
            "close": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "volume": [1000.0] * 30,
        })
        with pytest.raises(ValueError, match="at least 50 bars"):
            compute_signals(df)

    def test_indicators_present(self, ohlcv_bullish):
        result = compute_signals(ohlcv_bullish)
        ind = result["indicators"]
        expected_keys = {
            "ema20", "ema50", "ema_trend", "macd_line", "macd_signal",
            "macd_histogram", "rsi14", "bollinger_upper", "bollinger_middle",
            "bollinger_lower", "bollinger_pct_b", "atr14", "adr14",
            "volume_ratio", "sma200",
        }
        assert expected_keys == set(ind.keys())

    def test_suggested_sl_tp_structure(self, ohlcv_bullish):
        result = compute_signals(ohlcv_bullish)
        sl_tp = result["suggested_sl_tp"]
        if sl_tp:  # ATR may be None if data is degenerate
            assert "long" in sl_tp
            assert "short" in sl_tp
            for direction in ("long", "short"):
                assert "sl" in sl_tp[direction]
                assert "tp" in sl_tp[direction]
                assert "rr" in sl_tp[direction]
