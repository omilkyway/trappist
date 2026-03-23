"""Technical indicators for crypto futures trading analysis.

Calculates EMA, MACD, RSI, Bollinger Bands, ATR, volume ratios,
and funding rate signals from OHLCV data.
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
# Funding rate signal (crypto-specific)
# ---------------------------------------------------------------------------

def funding_rate_signal(funding_rate: float) -> dict:
    """Score based on perpetual futures funding rate.

    Negative funding (shorts paying longs) → long bias (you get PAID to be long).
    Positive funding (longs paying shorts) → short bias (you get PAID to be short).

    Enhanced scoring: funding is free money — weight it heavily.
    Returns dict with long_score and short_score contributions.
    """
    long_adj = 0
    short_adj = 0

    if funding_rate < -0.05:
        # Extreme negative — very strong long signal + funding income
        long_adj += 5
    elif funding_rate < -0.03:
        # Strong negative — strong long signal
        long_adj += 4
    elif funding_rate < -0.01:
        # Moderate negative — long bias
        long_adj += 2

    if funding_rate > 0.08:
        # Extreme positive — very strong short signal + funding income
        short_adj += 5
    elif funding_rate > 0.05:
        # Strong positive — strong short signal
        short_adj += 4
    elif funding_rate > 0.02:
        # Moderate positive — short bias
        short_adj += 2

    return {
        "funding_rate": funding_rate,
        "long_score": long_adj,
        "short_score": short_adj,
        "funding_edge": "LONG_PAID" if funding_rate < -0.01 else ("SHORT_PAID" if funding_rate > 0.02 else "NEUTRAL"),
    }


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
# ADX (Average Directional Index) — trend strength + direction
# ---------------------------------------------------------------------------

def compute_adx(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    close: pd.Series | pd.DataFrame,
    period: int = 14,
) -> pd.DataFrame:
    """Average Directional Index with +DI and -DI.

    ADX measures trend STRENGTH (not direction): >25 = trending, <20 = ranging.
    +DI vs -DI gives trend DIRECTION.
    """
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")
    c = _ensure_series(close, "close")

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)

    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=h.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=h.index,
    )

    alpha = 1.0 / period
    tr_smooth = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_dm_s = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    minus_dm_s = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    tr_safe = tr_smooth.replace(0, np.nan)
    plus_di = (100 * plus_dm_s / tr_safe).fillna(0)
    minus_di = (100 * minus_dm_s / tr_safe).fillna(0)

    di_sum_safe = (plus_di + minus_di).replace(0, np.nan)
    dx = (100 * (plus_di - minus_di).abs() / di_sum_safe).fillna(0)
    adx_val = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di}, index=c.index)


# ---------------------------------------------------------------------------
# Chandelier Exit — ATR-based trailing stop (superior to fixed %)
# ---------------------------------------------------------------------------

def chandelier_exit(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    close: pd.Series | pd.DataFrame,
    lookback: int = 22,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Chandelier Exit — volatility-adaptive trailing stop.

    Long exit:  Highest High(lookback) - ATR(14) x multiplier
    Short exit: Lowest Low(lookback) + ATR(14) x multiplier

    For crypto use multiplier 3.0 (standard) to 5.0 (wide).
    """
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")

    atr_val = atr(high, low, close, period=14)
    highest = h.rolling(window=lookback, min_periods=lookback).max()
    lowest = l.rolling(window=lookback, min_periods=lookback).min()

    return pd.DataFrame({
        "long_exit": highest - multiplier * atr_val,
        "short_exit": lowest + multiplier * atr_val,
    }, index=h.index)


# ---------------------------------------------------------------------------
# Bollinger Squeeze — breakout imminent detector
# ---------------------------------------------------------------------------

def bollinger_squeeze(
    close: pd.Series | pd.DataFrame,
    period: int = 20,
    num_std: float = 2.0,
    lookback: int = 120,
) -> dict:
    """Detect Bollinger Band squeeze — bandwidth at local minimum.

    Squeeze = bandwidth in bottom 20th percentile over lookback.
    Breakout direction unknown — wait for candle close beyond band.
    """
    s = _ensure_series(close)
    bb = bollinger_bands(s, period, num_std)
    bw = bb["bandwidth"]

    last_idx = len(bw) - 1
    if last_idx < lookback or pd.isna(bw.iloc[last_idx]):
        return {"bandwidth": None, "bandwidth_percentile": None, "is_squeeze": False}

    recent = bw.iloc[max(0, last_idx - lookback):last_idx + 1].dropna()
    if len(recent) < 20:
        return {"bandwidth": None, "bandwidth_percentile": None, "is_squeeze": False}

    current = float(bw.iloc[last_idx])
    pctile = round(float((recent < current).sum()) / len(recent) * 100, 1)

    return {"bandwidth": round(current, 6), "bandwidth_percentile": pctile, "is_squeeze": pctile < 20}


# ---------------------------------------------------------------------------
# Volatility-adaptive leverage — keeps risk per trade constant
# ---------------------------------------------------------------------------

def suggest_leverage(
    atr_pct: float | None,
    sl_atr_mult: float = 2.0,
    safety_factor: float = 0.7,
    max_leverage: int = 20,
    min_leverage: int = 7,
) -> int:
    """Calculate max safe leverage from volatility.

    Constraint: liquidation must be FURTHER than SL.
    Formula: leverage = safety_factor / (atr_pct x sl_atr_mult)

    safety_factor 0.7 = 30% buffer between SL and liquidation (aggressive).

    ATR 1% → 20x (cap) | ATR 2% → 17x | ATR 3% → 11x | ATR 5% → 7x
    """
    if not atr_pct or atr_pct <= 0:
        return 10
    sl_pct = atr_pct * sl_atr_mult
    optimal = safety_factor / sl_pct
    return max(min_leverage, min(max_leverage, round(optimal)))


# ---------------------------------------------------------------------------
# Regime classifier — determines WHICH strategy to use
# ---------------------------------------------------------------------------

def classify_regime(adx_value: float | None) -> dict:
    """Classify market regime from ADX.

    ranging (ADX<20): mean reversion, Bollinger bounces
    transitioning (20-25): reduce size, wait for clarity
    trending (25-50): trend following, breakouts, momentum
    strong_trend (>50): trail existing positions, don't initiate new
    """
    if adx_value is None:
        return {"regime": "unknown", "adx": None, "strategy": "reduce_size"}
    adx_r = round(adx_value, 1)
    if adx_value >= 50:
        return {"regime": "strong_trend", "adx": adx_r, "strategy": "trail_only"}
    if adx_value >= 25:
        return {"regime": "trending", "adx": adx_r, "strategy": "trend_follow"}
    if adx_value >= 20:
        return {"regime": "transitioning", "adx": adx_r, "strategy": "reduce_size"}
    return {"regime": "ranging", "adx": adx_r, "strategy": "mean_revert"}


# ---------------------------------------------------------------------------
# Composite signal scoring (matches technical-analyst agent spec)
# ---------------------------------------------------------------------------

_SIGNAL_WEIGHTS = {
    "ema_trend":     (+2, -2),
    "ema_crossover": (+3, -3),
    "macd_hist":     (+2, -2),
    "macd_cross":    (+3, -3),
    "rsi_zone":      (+1, -1),
    "bollinger":     (+2, -3),   # boosted: Bollinger signals are high-quality
    "volume":        (+2, -1),
    "price_sma200":  (+2, -2),
}

_MAX_RAW = 36   # Realistic max (not all signals fire simultaneously): 17 base + 5 independent + 2 momentum + 5 funding + 3 ADX + 3 squeeze + 1 VWAP. Lowered from 43 to spread scores wider.
_MIN_RAW = -20   # Realistic min: -17 base - 1 ADX ranging - 1 VWAP - 1 misc. Raised from -24.


def _score_to_metrics(raw: int) -> dict:
    """Convert raw score to normalized score, direction and strength."""
    normalized = round((raw - _MIN_RAW) / (_MAX_RAW - _MIN_RAW) * 100)
    normalized = max(0, min(100, normalized))

    if normalized >= 65:
        strength = "strong"
    elif normalized >= 50:
        strength = "moderate"
    elif normalized >= 40:
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


def compute_signals(df: pd.DataFrame, *, funding_rate: float | None = None) -> dict:
    """Compute trading signals from an OHLCV DataFrame.

    Returns BOTH long and short scores (0-100).
    Long score = how good is this as a BUY setup.
    Short score = how good is this as a SHORT setup (inverted signals).

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with at least 50 bars.
    funding_rate : float | None
        Perpetual futures funding rate (e.g. 0.01 for 0.01%).
        When provided, adjusts long/short scores based on funding bias.

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
    vwap_val = vwap(high, low, close, vol)

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

    # EMA crossover (within last 10 bars — wider window for more signals)
    ml = macd_df["macd_line"]
    sl = macd_df["signal_line"]
    for i in range(max(last - 10, 1), last + 1):
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

    # MACD crossover (last 6 bars — wider window for more signals)
    for i in range(max(last - 6, 1), last + 1):
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

    # --- FUNDING RATE SIGNAL (crypto-specific) ---
    funding_info = None
    if funding_rate is not None:
        funding_info = funding_rate_signal(funding_rate)
        long_raw += funding_info["long_score"]
        short_raw += funding_info["short_score"]

    # --- MOMENTUM SIGNAL: recent price action ---
    # Strong directional move in last 6 candles = trend confirmation
    if last >= 6 and not pd.isna(close.iloc[last - 6]):
        move_pct = (close.iloc[last] - close.iloc[last - 6]) / close.iloc[last - 6] * 100
        if move_pct > 2.0:
            long_raw += 2   # strong upward momentum
        elif move_pct < -2.0:
            short_raw += 2  # strong downward momentum

    # --- ADX REGIME SCORING ---
    adx_data = compute_adx(high, low, close)
    adx_last = _safe(adx_data["adx"].iloc[last])
    plus_di_last = _safe(adx_data["plus_di"].iloc[last])
    minus_di_last = _safe(adx_data["minus_di"].iloc[last])

    if adx_last is not None:
        if adx_last > 30 and plus_di_last is not None and minus_di_last is not None:
            # Trending — boost dominant direction (lowered from 40 to 30)
            if plus_di_last > minus_di_last:
                long_raw += 3
            else:
                short_raw += 3
        elif adx_last < 20:
            # Ranging — light dampen only (was -2, now -1)
            long_raw -= 1
            short_raw -= 1

    # --- VWAP: institutional mean-reversion anchor ---
    vwap_last = _safe(vwap_val.iloc[last]) if not pd.isna(vwap_val.iloc[last]) else None
    if vwap_last is not None and price > 0:
        vwap_dist_pct = (price - vwap_last) / vwap_last * 100
        if vwap_dist_pct < -1.0:
            long_raw += 1    # below VWAP = accumulation zone
        elif vwap_dist_pct > 1.0:
            short_raw += 1   # above VWAP = distribution zone

    regime = classify_regime(adx_last)
    squeeze = bollinger_squeeze(close)

    # --- SQUEEZE BONUS: imminent breakout boosts BOTH directions ---
    # Squeeze = low bandwidth = coiled spring. Breakout direction unknown
    # but the MOVE will be large → higher R/R for whoever catches it.
    if squeeze.get("is_squeeze"):
        long_raw += 3
        short_raw += 3

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
    adr_last = _r(adr_val.iloc[last], 6) if not pd.isna(adr_val.iloc[last]) else None
    atr_last = _r(atr_val.iloc[last], 6)

    # Compute suggested SL/TP — ATR-based (primary) with pivot adjustment
    # Research: 2x ATR SL, 3x ATR TP = 1.5 R/R minimum (tighter TP captures more wins)
    sl_tp = {}
    atr_pct = atr_last / price if price > 0 and atr_last else None
    if atr_last and price > 0:
        sl_mult = 2.0   # 2x ATR stop-loss (consensus for crypto)
        tp_mult = 3.0   # 3x ATR take-profit (1.5 R/R minimum) — reduced from 4x: most wins close below 4x ATR

        # Dynamic precision based on price magnitude (fixes low-price tokens)
        import math
        _prec = max(2, -int(math.floor(math.log10(max(price, 1e-10)))) + 2)
        def _rp(v):
            return round(v, _prec)

        # LONG — ATR-based with pivot structure adjustment
        long_sl = _rp(price - sl_mult * atr_last)
        long_tp = _rp(price + tp_mult * atr_last)
        # Tighten SL to support if nearby (within 1 ATR)
        if levels["support_1"] and abs(levels["support_1"] - long_sl) < atr_last:
            long_sl = _rp(min(long_sl, levels["support_1"]))
        # Extend TP to resistance if further (let winners run)
        if levels["resistance_1"] and levels["resistance_1"] > long_tp:
            long_tp = _rp(levels["resistance_1"])

        long_sl_pct = round((long_sl - price) / price * 100, 2)
        long_tp_pct = round((long_tp - price) / price * 100, 2)
        long_rr = round(abs(long_tp_pct / long_sl_pct), 2) if long_sl_pct != 0 else 0

        # SHORT — ATR-based with pivot structure adjustment
        short_sl = _rp(price + sl_mult * atr_last)
        short_tp = _rp(price - tp_mult * atr_last)
        if levels["resistance_1"] and abs(levels["resistance_1"] - short_sl) < atr_last:
            short_sl = _rp(max(short_sl, levels["resistance_1"]))
        if levels["support_1"] and levels["support_1"] < short_tp:
            short_tp = _rp(levels["support_1"])

        short_sl_pct = round((short_sl - price) / price * 100, 2)
        short_tp_pct = round((short_tp - price) / price * 100, 2)
        short_rr = round(abs(short_tp_pct / short_sl_pct), 2) if short_sl_pct != 0 else 0

        sl_tp = {
            "long": {"sl": long_sl, "tp": long_tp, "sl_pct": long_sl_pct, "tp_pct": long_tp_pct, "rr": long_rr},
            "short": {"sl": short_sl, "tp": short_tp, "sl_pct": short_sl_pct, "tp_pct": short_tp_pct, "rr": short_rr},
        }

    # Chandelier Exit levels for trailing
    ce = chandelier_exit(high, low, close)
    ce_long = _r(ce["long_exit"].iloc[last])
    ce_short = _r(ce["short_exit"].iloc[last])

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
            "atr_pct": _r(atr_pct * 100, 4) if atr_pct else None,
            "adr14": adr_last,
            "volume_ratio": _r(vr),
            "sma200": _r(sma200_val) if sma200_val is not None else None,
            "vwap": _r(vwap_last) if vwap_last is not None else None,
            "adx": _r(adx_last, 1),
            "plus_di": _r(plus_di_last, 1),
            "minus_di": _r(minus_di_last, 1),
        },
        "levels": levels,
        "suggested_sl_tp": sl_tp,
        "regime": regime,
        "squeeze": squeeze,
        "chandelier_exit": {"long_trail": ce_long, "short_trail": ce_short},
        "suggested_leverage": suggest_leverage(atr_pct),
        "signals": {
            "long_score": long_m["normalized_score"],
            "long_direction": long_m["direction"],
            "long_strength": long_m["strength"],
            "short_score": short_m["normalized_score"],
            "short_direction": short_m["direction"],
            "short_strength": short_m["strength"],
            "long_raw": long_raw,
            "short_raw": short_raw,
            "funding_rate": funding_info,
        },
    }


# ---------------------------------------------------------------------------
# Multi-timeframe analysis (crypto-specific)
# ---------------------------------------------------------------------------

# Default weights: higher timeframes dominate trend, lower timeframes
# refine entry timing.  Keys are timeframe labels (e.g. "5m", "1h", "4h",
# "1d").  Unlisted timeframes receive weight 1.0.
_MTF_WEIGHTS: dict[str, float] = {
    "1m":  0.5,
    "5m":  0.8,
    "15m": 1.0,
    "30m": 1.2,
    "1h":  1.5,
    "2h":  1.8,
    "4h":  2.0,
    "1d":  2.5,
    "1w":  3.0,
}


def compute_multi_timeframe(
    timeframes: dict[str, pd.DataFrame],
    *,
    funding_rate: float | None = None,
) -> dict:
    """Combine signals from multiple timeframes into a single view.

    Parameters
    ----------
    timeframes : dict[str, pd.DataFrame]
        Mapping of timeframe label (e.g. "5m", "1h", "4h", "1d") to an
        OHLCV DataFrame suitable for ``compute_signals``.
    funding_rate : float | None
        Perpetual futures funding rate, forwarded to each
        ``compute_signals`` call.

    Returns
    -------
    dict with keys:
        - ``combined_long_score``  (0-100 weighted average)
        - ``combined_short_score`` (0-100 weighted average)
        - ``dominant_direction``   ("long" | "short" | "neutral")
        - ``timeframe_details``    per-timeframe signal results
        - ``weights_used``         effective weights applied
    """
    if not timeframes:
        raise ValueError("timeframes dict must contain at least one entry")

    per_tf: dict[str, dict] = {}
    weighted_long = 0.0
    weighted_short = 0.0
    total_weight = 0.0

    for tf_label, df in timeframes.items():
        signals = compute_signals(df, funding_rate=funding_rate)
        weight = _MTF_WEIGHTS.get(tf_label, 1.0)

        weighted_long += signals["signals"]["long_score"] * weight
        weighted_short += signals["signals"]["short_score"] * weight
        total_weight += weight

        per_tf[tf_label] = {
            "weight": weight,
            "long_score": signals["signals"]["long_score"],
            "short_score": signals["signals"]["short_score"],
            "long_strength": signals["signals"]["long_strength"],
            "short_strength": signals["signals"]["short_strength"],
            "price": signals["price"],
            "rsi14": signals["indicators"]["rsi14"],
            "ema_trend": signals["indicators"]["ema_trend"],
        }

    combined_long = round(weighted_long / total_weight, 1)
    combined_short = round(weighted_short / total_weight, 1)

    if combined_long - combined_short >= 5:
        dominant = "long"
    elif combined_short - combined_long >= 5:
        dominant = "short"
    else:
        dominant = "neutral"

    return {
        "combined_long_score": combined_long,
        "combined_short_score": combined_short,
        "dominant_direction": dominant,
        "timeframe_details": per_tf,
        "weights_used": {
            tf: _MTF_WEIGHTS.get(tf, 1.0) for tf in timeframes
        },
    }


# ---------------------------------------------------------------------------
# Kelly criterion — category-aware position sizing
# ---------------------------------------------------------------------------

# Historical win rates and payoff ratios per category (from evolve reports).
# Updated after each /evolve analysis. Half-Kelly for safety.
_CATEGORY_EDGE: dict[str, dict] = {
    "AI":               {"win_rate": 0.56, "payoff": 3.1, "risk_pct": 0.08},  # 83% of profits — overweight
    "Smart Contract L1": {"win_rate": 0.45, "payoff": 1.5, "risk_pct": 0.04},
    "Store of Value":   {"win_rate": 0.50, "payoff": 2.0, "risk_pct": 0.05},
    "Layer 2":          {"win_rate": 0.40, "payoff": 1.3, "risk_pct": 0.03},
    "DeFi":             {"win_rate": 0.42, "payoff": 1.4, "risk_pct": 0.03},
    "Meme":             {"win_rate": 0.38, "payoff": 2.5, "risk_pct": 0.03},  # high payoff but low WR
    "Exchange Token":   {"win_rate": 0.40, "payoff": 1.2, "risk_pct": 0.02},
    "Payment":          {"win_rate": 0.38, "payoff": 1.2, "risk_pct": 0.02},
    "Gaming":           {"win_rate": 0.35, "payoff": 1.5, "risk_pct": 0.02},
    "Infrastructure":   {"win_rate": 0.40, "payoff": 1.3, "risk_pct": 0.03},
    "Staking":          {"win_rate": 0.40, "payoff": 1.3, "risk_pct": 0.03},
    "Other":            {"win_rate": 0.40, "payoff": 1.3, "risk_pct": 0.03},
}


def kelly_risk_pct(category: str) -> float:
    """Return the Half-Kelly risk percentage for a given category.

    Uses historical win_rate and payoff ratio to compute optimal bet size.
    Formula: Kelly% = (win_rate * payoff - (1 - win_rate)) / payoff
    Half-Kelly = Kelly% / 2 (conservative, reduces variance).

    Returns risk as a fraction (e.g. 0.08 = 8% of equity).
    """
    edge = _CATEGORY_EDGE.get(category, _CATEGORY_EDGE["Other"])
    w = edge["win_rate"]
    b = edge["payoff"]

    kelly = (w * b - (1 - w)) / b if b > 0 else 0
    half_kelly = max(0.01, kelly / 2)  # floor at 1%

    # Cap at the pre-defined risk_pct (which is already Half-Kelly-calibrated)
    return min(half_kelly, edge["risk_pct"])


# ---------------------------------------------------------------------------
# Open Interest signal — contrarian indicator
# ---------------------------------------------------------------------------

def open_interest_signal(oi_change_pct: float, price_change_pct: float) -> dict:
    """Score based on open interest change vs price change.

    OI rising + price rising = new longs entering → trend continuation (long bias)
    OI rising + price falling = new shorts entering → trend continuation (short bias)
    OI falling + price rising = short squeeze → long bias (but exhaustion warning)
    OI falling + price falling = long liquidation → short bias (but exhaustion warning)

    Returns dict with long_score and short_score contributions.
    """
    long_adj = 0
    short_adj = 0

    if oi_change_pct > 5:  # Significant OI increase
        if price_change_pct > 1:
            long_adj += 2   # New money entering long — continuation
        elif price_change_pct < -1:
            short_adj += 2  # New money entering short — continuation
    elif oi_change_pct < -5:  # Significant OI decrease
        if price_change_pct > 2:
            long_adj += 1   # Short squeeze — bullish but caution
        elif price_change_pct < -2:
            short_adj += 1  # Long liquidation cascade — bearish but caution

    return {
        "oi_change_pct": oi_change_pct,
        "price_change_pct": price_change_pct,
        "long_score": long_adj,
        "short_score": short_adj,
    }


# ---------------------------------------------------------------------------
# VWAP — Volume Weighted Average Price (institutional anchor)
# ---------------------------------------------------------------------------

def vwap(
    high: pd.Series | pd.DataFrame,
    low: pd.Series | pd.DataFrame,
    close: pd.Series | pd.DataFrame,
    volume: pd.Series | pd.DataFrame,
    period: int = 20,
) -> pd.Series:
    """Rolling VWAP over *period* bars.

    In 24/7 crypto (no daily reset), use rolling window.
    Price below VWAP = accumulation zone (institutional buying).
    Price above VWAP = distribution zone (institutional selling).
    """
    h = _ensure_series(high, "high")
    l = _ensure_series(low, "low")
    c = _ensure_series(close, "close")
    v = _ensure_series(volume, "volume")

    typical = (h + l + c) / 3
    cum_tp_vol = (typical * v).rolling(window=period, min_periods=period).sum()
    cum_vol = v.rolling(window=period, min_periods=period).sum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Time-of-day adjustment — proven by trade data
# ---------------------------------------------------------------------------

def time_of_day_adjustment() -> dict:
    """Returns score adjustment and size multiplier based on UTC hour.

    From evolve report 2026-03-22:
    - 02:00-09:00 UTC: ALL profitable trades > $10 happened here
    - 12:00-18:00 UTC: 6-loss cluster

    Returns dict with score_adj (added to raw), size_mult, and window name.
    """
    from datetime import datetime as _dt, timezone as _tz
    hour = _dt.now(_tz.utc).hour

    if 2 <= hour <= 9:
        return {"score_adj": 3, "size_mult": 1.25, "window": "GOLDEN_HOURS",
                "note": "02-09 UTC: historically most profitable window"}
    elif 12 <= hour <= 18:
        return {"score_adj": -3, "size_mult": 0.75, "window": "CAUTION_HOURS",
                "note": "12-18 UTC: historically loss cluster"}
    return {"score_adj": 0, "size_mult": 1.0, "window": "STANDARD", "note": ""}


# ---------------------------------------------------------------------------
# Dynamic score threshold — adapts to recent performance
# ---------------------------------------------------------------------------

def dynamic_score_threshold(closed_trades: list, base_threshold: int = 55) -> dict:
    """Adjust score threshold based on recent win rate.

    Hot streak (WR > 55%) → lower threshold, more trades (momentum)
    Cold streak (WR < 35%) → slightly higher threshold (but CAPPED at 58)

    CRITICAL: max threshold = 58. Scores rarely exceed 63. A threshold of 65+
    creates a death spiral: no trades → no wins → threshold stays high.
    Data from 34 cycles showed threshold=65 blocked ALL trades for 4 hours.

    Returns dict with threshold and reasoning.
    """
    if len(closed_trades) < 10:
        return {"threshold": base_threshold, "high_threshold": 60,
                "reason": "insufficient data (<10 trades)", "win_rate": None}

    recent = closed_trades[-20:]
    wins = sum(1 for t in recent if t.get("pnl_pct", 0) > 0 or t.get("unrealized_pnl", 0) > 0)
    win_rate = round(wins / len(recent) * 100, 1)

    if win_rate > 55:
        threshold = max(48, base_threshold - 7)
        high_threshold = 58
        reason = f"HOT: {win_rate}% WR → aggressive (threshold {threshold})"
    elif win_rate < 35:
        threshold = min(58, base_threshold + 3)
        high_threshold = 62
        reason = f"COLD: {win_rate}% WR → slightly selective (threshold {threshold})"
    else:
        threshold = base_threshold
        high_threshold = 60
        reason = f"NORMAL: {win_rate}% WR → standard"

    return {"threshold": threshold, "high_threshold": high_threshold,
            "reason": reason, "win_rate": win_rate}


# ---------------------------------------------------------------------------
# Liquidation signal — liquidation cascades are price magnets
# ---------------------------------------------------------------------------

def liquidation_signal(recent_liquidations: list) -> dict:
    """Score based on recent forced liquidation data.

    Heavy long liquidations = panic selling → short-term bounce potential (long)
    Heavy short liquidations = short squeeze → more upside (long continuation)

    recent_liquidations: list of dicts with 'side' and 'qty' keys.
    """
    if not recent_liquidations:
        return {"long_score": 0, "short_score": 0, "long_liq_volume": 0, "short_liq_volume": 0}

    long_liq = sum(l.get("qty", 0) for l in recent_liquidations if l.get("side") == "sell")
    short_liq = sum(l.get("qty", 0) for l in recent_liquidations if l.get("side") == "buy")

    long_adj = 0
    short_adj = 0

    # Heavy long liquidations = capitulation → contrarian long signal
    if long_liq > short_liq * 3 and long_liq > 0:
        long_adj += 2
    # Heavy short liquidations = squeeze → continuation long signal
    elif short_liq > long_liq * 3 and short_liq > 0:
        long_adj += 1
    # Heavy short liq without continuation = reversal short signal
    if short_liq > long_liq * 5 and short_liq > 0:
        short_adj += 1

    return {
        "long_score": long_adj,
        "short_score": short_adj,
        "long_liq_volume": round(long_liq, 2),
        "short_liq_volume": round(short_liq, 2),
    }


# ---------------------------------------------------------------------------
# Suggested limit entry — better fills than market
# ---------------------------------------------------------------------------

def suggest_limit_entry(
    price: float,
    atr_last: float,
    vwap_last: float | None,
    support_1: float | None,
    resistance_1: float | None,
    direction: str,
) -> float | None:
    """Suggest a limit entry price for better fills.

    LONG: enter at best of (VWAP, support_1, price - 0.3*ATR)
    SHORT: enter at best of (VWAP, resistance_1, price + 0.3*ATR)
    Returns None if no good level found (use market).
    """
    if not atr_last or price <= 0:
        return None

    if direction == "LONG":
        candidates = [price - 0.3 * atr_last]  # slight pullback from current
        if vwap_last and vwap_last < price and vwap_last > price - atr_last:
            candidates.append(vwap_last)
        if support_1 and support_1 < price and support_1 > price - atr_last:
            candidates.append(support_1)
        # Best entry = highest of the candidates (closest to current price but still below)
        return round(max(candidates), 6) if candidates else None
    else:  # SHORT
        candidates = [price + 0.3 * atr_last]
        if vwap_last and vwap_last > price and vwap_last < price + atr_last:
            candidates.append(vwap_last)
        if resistance_1 and resistance_1 > price and resistance_1 < price + atr_last:
            candidates.append(resistance_1)
        return round(min(candidates), 6) if candidates else None
