"""Technical indicators for swing trading analysis.

Calculates EMA, MACD, RSI, Bollinger Bands, ATR, and volume ratios
from OHLCV data returned by the Alpaca SDK.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_series(data, col: str = "close") -> pd.Series:
    """Accept a pd.Series or a DataFrame with the named column."""
    if isinstance(data, pd.Series):
        return data.astype(float)
    if isinstance(data, pd.DataFrame):
        return data[col].astype(float)
    raise TypeError(f"Expected pd.Series or pd.DataFrame, got {type(data)}")


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------

def sma(close: pd.Series | pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple Moving Average."""
    s = _ensure_series(close)
    return s.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series | pd.DataFrame, period: int = 20) -> pd.Series:
    """Exponential Moving Average (standard span method)."""
    s = _ensure_series(close)
    return s.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    close: pd.Series | pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line and histogram.

    Returns DataFrame with columns: macd_line, signal_line, histogram.
    """
    s = _ensure_series(close)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    }, index=s.index)


# ---------------------------------------------------------------------------
# RSI (Wilder smoothing)
# ---------------------------------------------------------------------------

def rsi(close: pd.Series | pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index with Wilder smoothing."""
    s = _ensure_series(close)
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    # Guard against division by zero (all gains, no losses)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # When avg_loss == 0, RSI = 100 (max overbought)
    result = result.fillna(100.0)
    return result


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    close: pd.Series | pd.DataFrame,
    period: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands.

    Returns DataFrame with columns: upper, middle, lower, pct_b, bandwidth.
    """
    s = _ensure_series(close)
    middle = s.rolling(window=period, min_periods=period).mean()
    std = s.rolling(window=period, min_periods=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    pct_b = (s - lower) / (upper - lower)
    bandwidth = (upper - lower) / middle
    return pd.DataFrame({
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "pct_b": pct_b,
        "bandwidth": bandwidth,
    }, index=s.index)


# ---------------------------------------------------------------------------
# ATR (Wilder smoothing)
# ---------------------------------------------------------------------------

def atr(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    close: pd.Series | pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    """Average True Range with Wilder smoothing."""
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")
    c = _ensure_series(close, "close")
    prev_close = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_close).abs(),
        (l - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------------

def volume_ratio(volume: pd.Series | pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume divided by SMA of volume over *period* days."""
    v = _ensure_series(volume, "volume")
    avg = v.rolling(window=period, min_periods=period).mean()
    return v / avg


# ---------------------------------------------------------------------------
# Support / Resistance detection via pivot points
# ---------------------------------------------------------------------------

def find_pivots(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    lookback: int = 5,
) -> dict:
    """Detect swing highs and swing lows for support/resistance levels.

    A swing high at index i: high[i] >= max(high[i-lookback:i]) AND
                              high[i] >= max(high[i+1:i+lookback+1])
    Returns dict with 'supports' and 'resistances' as lists of (index, price).
    """
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")

    resistances = []
    supports = []

    for i in range(lookback, len(h) - lookback):
        # Swing high
        window_before = h.iloc[i - lookback:i]
        window_after = h.iloc[i + 1:i + lookback + 1]
        if len(window_before) > 0 and len(window_after) > 0:
            if h.iloc[i] >= window_before.max() and h.iloc[i] >= window_after.max():
                resistances.append(float(h.iloc[i]))

        # Swing low
        wb = l.iloc[i - lookback:i]
        wa = l.iloc[i + 1:i + lookback + 1]
        if len(wb) > 0 and len(wa) > 0:
            if l.iloc[i] <= wb.min() and l.iloc[i] <= wa.min():
                supports.append(float(l.iloc[i]))

    return {"supports": supports, "resistances": resistances}


def find_nearest_levels(price: float, pivots: dict) -> dict:
    """Find the nearest support below and resistance above current price."""
    supports_below = sorted([s for s in pivots["supports"] if s < price], reverse=True)
    resistances_above = sorted([r for r in pivots["resistances"] if r > price])
    supports_above = sorted([s for s in pivots["supports"] if s > price])
    resistances_below = sorted([r for r in pivots["resistances"] if r < price], reverse=True)

    return {
        "support_1": supports_below[0] if supports_below else None,
        "support_2": supports_below[1] if len(supports_below) > 1 else None,
        "resistance_1": resistances_above[0] if resistances_above else None,
        "resistance_2": resistances_above[1] if len(resistances_above) > 1 else None,
        # For shorts: nearest support below (TP target) and resistance above (SL zone)
        "nearest_support_above": supports_above[0] if supports_above else None,
        "nearest_resistance_below": resistances_below[0] if resistances_below else None,
    }


# ---------------------------------------------------------------------------
# ADR (Average Daily Range) — realistic TP target calculation
# ---------------------------------------------------------------------------

def adr(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    """Average Daily Range — mean of (high - low) over period days."""
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")
    daily_range = h - l
    return daily_range.rolling(window=period, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Composite signal scoring (matches technical-analyst agent spec)
# ---------------------------------------------------------------------------

_SIGNAL_WEIGHTS = {
    "ema_trend":     (+2, -2),
    "ema_crossover": (+3, -3),
    "macd_hist":     (+2, -2),
    "macd_cross":    (+3, -3),
    "rsi_zone":      (+1, -1),
    "bollinger":     (+1, -2),
    "volume":        (+2, -1),
    "price_sma200":  (+2, -2),
}

_MAX_RAW = 21   # 16 base + 5 independent directional bonuses
_MIN_RAW = -16


def _score_to_metrics(raw: int) -> dict:
    """Convert raw score to normalized score, direction and strength."""
    normalized = round((raw - _MIN_RAW) / (_MAX_RAW - _MIN_RAW) * 100)
    normalized = max(0, min(100, normalized))

    if normalized >= 70:
        strength = "strong"
    elif normalized >= 55:
        strength = "moderate"
    elif normalized >= 45:
        strength = "neutral"
    else:
        strength = "weak"

    direction = "bullish" if raw > 0 else ("bearish" if raw < 0 else "neutral")

    return {
        "raw_score": raw,
        "normalized_score": normalized,
        "direction": direction,
        "strength": strength,
    }


def compute_signals(df: pd.DataFrame) -> dict:
    """Compute trading signals from a daily OHLCV DataFrame.

    Returns BOTH long and short scores (0-100).
    Long score = how good is this as a BUY setup.
    Short score = how good is this as a SHORT setup (inverted signals).

    Raises ValueError if data is insufficient or malformed.
    """
    required_cols = {"close", "high", "low", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")
    if len(df) < 50:
        raise ValueError(f"Need at least 50 bars for analysis, got {len(df)}")

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)

    # Check for excessive NaN in close prices
    nan_pct = close.isna().sum() / len(close)
    if nan_pct > 0.1:
        raise ValueError(f"Too many NaN values in close prices: {nan_pct:.0%}")

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    sma200 = sma(close, 200)
    macd_df = macd(close)
    rsi_val = rsi(close)
    bb = bollinger_bands(close)
    atr_val = atr(high, low, close)
    vol_r = volume_ratio(vol)

    last = len(close) - 1
    price = close.iloc[last]
    if pd.isna(price):
        raise ValueError("Last close price is NaN — cannot compute signals")

    # --- compute raw scores for both directions ---
    long_raw = 0
    short_raw = 0

    # Helper: safe NaN check for indicator values
    def _safe(val):
        return val if not pd.isna(val) else None

    # EMA trend
    ema20_last = _safe(ema20.iloc[last])
    ema50_last = _safe(ema50.iloc[last])
    ema_bullish = (ema20_last is not None and ema50_last is not None
                   and ema20_last > ema50_last)
    long_raw += _SIGNAL_WEIGHTS["ema_trend"][0 if ema_bullish else 1]
    short_raw += _SIGNAL_WEIGHTS["ema_trend"][1 if ema_bullish else 0]

    # EMA crossover (within last 5 bars)
    ml = macd_df["macd_line"]
    sl = macd_df["signal_line"]
    for i in range(max(last - 5, 1), last + 1):
        if ema20.iloc[i] > ema50.iloc[i] and ema20.iloc[i - 1] <= ema50.iloc[i - 1]:
            long_raw += _SIGNAL_WEIGHTS["ema_crossover"][0]
            short_raw += _SIGNAL_WEIGHTS["ema_crossover"][1]
            break
        if ema20.iloc[i] < ema50.iloc[i] and ema20.iloc[i - 1] >= ema50.iloc[i - 1]:
            long_raw += _SIGNAL_WEIGHTS["ema_crossover"][1]
            short_raw += _SIGNAL_WEIGHTS["ema_crossover"][0]
            break

    # MACD histogram
    hist = macd_df["histogram"].iloc[last]
    hist_prev = macd_df["histogram"].iloc[last - 1] if last > 0 else 0
    if hist > 0 and hist > hist_prev:
        long_raw += _SIGNAL_WEIGHTS["macd_hist"][0]
        short_raw += _SIGNAL_WEIGHTS["macd_hist"][1]
    elif hist < 0 and hist < hist_prev:
        long_raw += _SIGNAL_WEIGHTS["macd_hist"][1]
        short_raw += _SIGNAL_WEIGHTS["macd_hist"][0]

    # MACD crossover (last 3 bars)
    for i in range(max(last - 3, 1), last + 1):
        if ml.iloc[i] > sl.iloc[i] and ml.iloc[i - 1] <= sl.iloc[i - 1]:
            long_raw += _SIGNAL_WEIGHTS["macd_cross"][0]
            short_raw += _SIGNAL_WEIGHTS["macd_cross"][1]
            break
        if ml.iloc[i] < sl.iloc[i] and ml.iloc[i - 1] >= sl.iloc[i - 1]:
            long_raw += _SIGNAL_WEIGHTS["macd_cross"][1]
            short_raw += _SIGNAL_WEIGHTS["macd_cross"][0]
            break

    # RSI — directional scoring
    r = rsi_val.iloc[last]
    if pd.isna(r):
        r = 50.0  # neutral fallback if RSI unavailable
    if 40 <= r <= 60:
        long_raw += _SIGNAL_WEIGHTS["rsi_zone"][0]
    elif r > 70 or r < 30:
        long_raw += _SIGNAL_WEIGHTS["rsi_zone"][1]
    if r > 70:
        short_raw += _SIGNAL_WEIGHTS["rsi_zone"][0]   # overbought = short opportunity
    elif r < 40:
        short_raw += _SIGNAL_WEIGHTS["rsi_zone"][1]   # oversold = bad short

    # Bollinger %B
    pct_b = bb["pct_b"].iloc[last]
    if pd.isna(pct_b):
        pct_b = 0.5  # neutral fallback
    if 0.2 <= pct_b <= 0.8:
        long_raw += _SIGNAL_WEIGHTS["bollinger"][0]
    elif pct_b > 1.0 or pct_b < 0.0:
        long_raw += _SIGNAL_WEIGHTS["bollinger"][1]
    if pct_b > 1.0:
        short_raw += _SIGNAL_WEIGHTS["bollinger"][0]   # above upper = overbought = short
    elif pct_b < 0.0:
        short_raw += _SIGNAL_WEIGHTS["bollinger"][1]   # below lower = don't short

    # Volume (high volume confirms any direction)
    vr = vol_r.iloc[last]
    if pd.isna(vr):
        vr = 1.0  # neutral fallback
    if vr > 1.5:
        long_raw += _SIGNAL_WEIGHTS["volume"][0]
        short_raw += _SIGNAL_WEIGHTS["volume"][0]
    elif vr < 0.5:
        long_raw += _SIGNAL_WEIGHTS["volume"][1]
        short_raw += _SIGNAL_WEIGHTS["volume"][1]

    # Price vs SMA200
    sma200_val = sma200.iloc[last] if not np.isnan(sma200.iloc[last]) else None
    if sma200_val is not None:
        if price > sma200_val:
            long_raw += _SIGNAL_WEIGHTS["price_sma200"][0]
            short_raw += _SIGNAL_WEIGHTS["price_sma200"][1]
        else:
            long_raw += _SIGNAL_WEIGHTS["price_sma200"][1]
            short_raw += _SIGNAL_WEIGHTS["price_sma200"][0]

    # --- INDEPENDENT DIRECTIONAL SIGNALS (not mirrored) ---
    # These break the symmetry between long and short scores,
    # allowing each direction to be evaluated on its own merits.

    # 1. Directional volume confirmation (+2 each side independently)
    #    Price up + high volume = accumulation (long bonus)
    #    Price down + high volume = distribution (short bonus)
    if last > 0 and not pd.isna(close.iloc[last - 1]):
        price_change = close.iloc[last] - close.iloc[last - 1]
        if price_change > 0 and vr > 1.5:
            long_raw += 2   # accumulation confirmed
        elif price_change < 0 and vr > 1.5:
            short_raw += 2  # distribution confirmed

    # 2. RSI momentum shift (+2 each side independently)
    #    RSI crossing 50 from above = bearish momentum shift
    #    RSI crossing 50 from below = bullish momentum shift
    if last >= 5 and not pd.isna(rsi_val.iloc[last - 5]):
        rsi_5_ago = float(rsi_val.iloc[last - 5])
        if rsi_5_ago > 50 and r < 45:
            short_raw += 2  # momentum shifting bearish
        elif rsi_5_ago < 50 and r > 55:
            long_raw += 2   # momentum shifting bullish

    # 3. EMA gap acceleration (+1 each side independently)
    #    Widening bullish gap = trend accelerating up
    #    Widening bearish gap = trend accelerating down
    if ema20_last is not None and ema50_last is not None and last >= 5:
        ema20_5ago = _safe(ema20.iloc[last - 5])
        ema50_5ago = _safe(ema50.iloc[last - 5])
        if ema20_5ago is not None and ema50_5ago is not None:
            gap_now = ema20_last - ema50_last
            gap_5ago = ema20_5ago - ema50_5ago
            if gap_now < gap_5ago and gap_now < 0:
                short_raw += 1  # bearish gap widening
            elif gap_now > gap_5ago and gap_now > 0:
                long_raw += 1   # bullish gap widening

    long_m = _score_to_metrics(long_raw)
    short_m = _score_to_metrics(short_raw)

    def _r(val, decimals=2):
        """Round a value, returning None if NaN."""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return round(val, decimals)

    # --- Support/Resistance levels and ADR ---
    pivots = find_pivots(high, low, lookback=5)
    levels = find_nearest_levels(price, pivots)
    adr_val = adr(high, low)
    adr_last = _r(adr_val.iloc[last]) if not pd.isna(adr_val.iloc[last]) else None
    atr_last = _r(atr_val.iloc[last])

    # Compute suggested SL/TP for both directions
    sl_tp = {}
    if atr_last:
        # LONG levels
        long_sl = levels["support_1"] if levels["support_1"] else round(price - 2 * atr_last, 2)
        long_tp = levels["resistance_1"] if levels["resistance_1"] else round(price + 1.5 * (price - long_sl), 2)
        long_sl_pct = round((long_sl - price) / price * 100, 2)
        long_tp_pct = round((long_tp - price) / price * 100, 2)
        long_rr = round(abs(long_tp_pct / long_sl_pct), 2) if long_sl_pct != 0 else 0

        # SHORT levels
        short_sl = levels["resistance_1"] if levels["resistance_1"] else round(price + 2 * atr_last, 2)
        short_tp = levels["support_1"] if levels["support_1"] else round(price - 1.5 * (short_sl - price), 2)
        short_sl_pct = round((short_sl - price) / price * 100, 2)
        short_tp_pct = round((short_tp - price) / price * 100, 2)
        short_rr = round(abs(short_tp_pct / short_sl_pct), 2) if short_sl_pct != 0 else 0

        sl_tp = {
            "long": {"sl": long_sl, "tp": long_tp, "sl_pct": long_sl_pct, "tp_pct": long_tp_pct, "rr": long_rr},
            "short": {"sl": short_sl, "tp": short_tp, "sl_pct": short_sl_pct, "tp_pct": short_tp_pct, "rr": short_rr},
        }

    return {
        "price": round(price, 2),
        "indicators": {
            "ema20": _r(ema20.iloc[last]),
            "ema50": _r(ema50.iloc[last]),
            "ema_trend": "bullish" if ema_bullish else "bearish",
            "macd_line": _r(ml.iloc[last], 4),
            "macd_signal": _r(sl.iloc[last], 4),
            "macd_histogram": _r(hist, 4),
            "rsi14": _r(r, 1),
            "bollinger_upper": _r(bb["upper"].iloc[last]),
            "bollinger_middle": _r(bb["middle"].iloc[last]),
            "bollinger_lower": _r(bb["lower"].iloc[last]),
            "bollinger_pct_b": _r(pct_b, 3),
            "atr14": atr_last,
            "adr14": adr_last,
            "volume_ratio": _r(vr),
            "sma200": _r(sma200_val) if sma200_val is not None else None,
        },
        "levels": levels,
        "suggested_sl_tp": sl_tp,
        "signals": {
            "long_score": long_m["normalized_score"],
            "long_direction": long_m["direction"],
            "long_strength": long_m["strength"],
            "short_score": short_m["normalized_score"],
            "short_direction": short_m["direction"],
            "short_strength": short_m["strength"],
            "long_raw": long_raw,
            "short_raw": short_raw,
        },
    }
