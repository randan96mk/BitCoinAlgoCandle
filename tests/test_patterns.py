"""Unit tests for candlestick pattern detection (known OHLC -> expected match)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.strategy.candlestick_patterns import Candle, detect_patterns


def _names(candles):
    return {m.name for m in detect_patterns(candles)}


def _downtrend(n=6, start=110.0, step=2.0):
    out, p = [], start
    for _ in range(n):
        o = p
        p -= step
        out.append(Candle(o, o + 0.3, p - 0.3, p))
    return out


def _uptrend(n=6, start=90.0, step=2.0):
    out, p = [], start
    for _ in range(n):
        o = p
        p += step
        out.append(Candle(o, p + 0.3, o - 0.3, p))
    return out


def test_hammer():
    ctx = _downtrend() + [Candle(100, 101, 94, 100.5)]  # long lower wick, small body top
    assert "Hammer" in _names(ctx)


def test_shooting_star():
    ctx = _uptrend() + [Candle(100, 106, 99.7, 100.3)]  # long upper wick, small body bottom
    names = _names(ctx)
    assert "Shooting Star" in names


def test_bullish_engulfing():
    ctx = _downtrend() + [Candle(100, 100.5, 98, 98.5), Candle(98, 101.5, 97.8, 101)]
    assert "Bullish Engulfing" in _names(ctx)


def test_bearish_engulfing():
    ctx = _uptrend() + [Candle(100, 102, 99.5, 101.5), Candle(101.5, 102, 98.5, 99)]
    assert "Bearish Engulfing" in _names(ctx)


def test_doji():
    ctx = _uptrend() + [Candle(100, 102, 98, 100.05)]  # near-zero body
    assert "Doji" in _names(ctx)


def test_marubozu():
    ctx = _uptrend() + [Candle(100, 110.1, 99.95, 110)]  # body spans almost whole range
    assert "Marubozu" in _names(ctx)


def test_three_white_soldiers():
    ctx = _downtrend() + [
        Candle(100, 103.2, 99.8, 103),
        Candle(103, 106.2, 102.8, 106),
        Candle(106, 109.2, 105.8, 109),
    ]
    assert "Three White Soldiers" in _names(ctx)


def test_three_black_crows():
    ctx = _uptrend() + [
        Candle(109, 109.2, 105.8, 106),
        Candle(106, 106.2, 102.8, 103),
        Candle(103, 103.2, 99.8, 100),
    ]
    assert "Three Black Crows" in _names(ctx)


def test_inside_bar():
    ctx = _uptrend() + [Candle(100, 108, 92, 101), Candle(100, 104, 96, 99)]
    assert "Inside Bar" in _names(ctx)


def test_directions_are_consistent():
    for c in detect_patterns(_downtrend() + [Candle(100, 101, 94, 100.5)]):
        assert c.direction in ("long", "short", "neutral")
        assert c.pattern_low <= c.pattern_high


def test_noise_filter_drops_tiny_candles():
    # A tiny-range candle relative to ATR yields no patterns.
    ctx = _uptrend() + [Candle(100.0, 100.02, 99.98, 100.0)]
    assert detect_patterns(ctx, atr=50.0) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} pattern tests passed")
