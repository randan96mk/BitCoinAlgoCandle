"""
Technical indicators matching Pine Script's ta.* functions exactly.
All smoothing uses Wilder's RMA (seeded with SMA of first N values).
"""
import numpy as np
import pandas as pd


def _rma(series: pd.Series, length: int) -> pd.Series:
    """
    Wilder's RMA — exact match for Pine Script's ta.rma().
    Seed: SMA of first `length` values, then: alpha*val + (1-alpha)*prev
    """
    result = np.full(len(series), np.nan)
    vals = series.values
    alpha = 1.0 / length

    first_valid = 0
    while first_valid < len(vals) and np.isnan(vals[first_valid]):
        first_valid += 1

    if first_valid + length > len(vals):
        return pd.Series(result, index=series.index)

    seed_end = first_valid + length
    result[seed_end - 1] = np.nanmean(vals[first_valid:seed_end])

    for i in range(seed_end, len(vals)):
        result[i] = alpha * vals[i] + (1.0 - alpha) * result[i - 1]

    return pd.Series(result, index=series.index)


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """RSI matching Pine Script's ta.rsi() — uses RMA for smoothing."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = _rma(gain, length)
    avg_loss = _rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.inf)
    return 100.0 - (100.0 / (1.0 + rs))


def sma(series: pd.Series, length: int) -> pd.Series:
    """SMA matching Pine Script's ta.sma()."""
    return series.rolling(window=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """EMA matching Pine Script's ta.ema() (standard 2/(len+1) smoothing)."""
    return series.ewm(span=length, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """ATR matching Pine Script's ta.atr() — uses RMA on True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return _rma(tr, length)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """
    ADX matching Pine Script's ta.dmi(dilen, adxlen).
    Both dilen and adxlen use the same length (as in the Pine Script).
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = (high - prev_high).where((high - prev_high) > (prev_low - low), 0.0)
    plus_dm = plus_dm.where(plus_dm > 0, 0.0)

    minus_dm = (prev_low - low).where((prev_low - low) > (high - prev_high), 0.0)
    minus_dm = minus_dm.where(minus_dm > 0, 0.0)

    atr_val = atr(high, low, close, length)

    plus_di = 100.0 * (_rma(plus_dm, length) / atr_val)
    minus_di = 100.0 * (_rma(minus_dm, length) / atr_val)

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.inf)) * 100.0
    return _rma(dx, length)
