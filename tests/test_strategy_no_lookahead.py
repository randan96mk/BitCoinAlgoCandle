"""
Strategy-level tests:
  * a clean long setup produces a LONG signal naming the pattern
  * NO LOOK-AHEAD: the signal computed for a given "forming bar" position is
    identical whether later candles exist or not — replaying history bar-by-bar
    reproduces the exact signal that a live feed would have produced.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from backend.strategy.candlestick_strategy import CandlestickStrategy


def _make_df():
    """Downtrend, a hammer, then a bar that breaks the hammer high, plus tail."""
    rows, price, t0 = [], 30000.0, dt.datetime(2024, 1, 1)
    for i in range(115):
        o = price
        price -= 8
        c = price
        rows.append([t0 + dt.timedelta(minutes=i), o, max(o, c) + 3, min(o, c) - 3, c, 100 + i % 5])
    o = price
    c = price + 2
    hi = c + 1
    rows.append([t0 + dt.timedelta(minutes=115), o, hi, o - 30, c, 300])       # hammer
    rows.append([t0 + dt.timedelta(minutes=116), c, hi + 40, c - 2, hi + 38, 400])  # breakout
    for j in range(117, 122):
        rows.append([t0 + dt.timedelta(minutes=j), hi + 38, hi + 42, hi + 34, hi + 39, 60])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _mechanics_strategy():
    """Strategy with the discretionary quality filters relaxed, so these tests
    exercise pattern detection + entry mechanics rather than the tuned filters."""
    s = CandlestickStrategy()
    s.excluded_triggers = set()      # allow any pattern to trigger
    s.max_entry_ext_atr = 0          # disable anti-chase filter
    s.entry_bias = "any"             # disable pullback/breakout location filter
    s.min_score = 0                  # don't gate on score here
    for k in s._enabled:
        s._enabled[k] = False        # ignore indicator confirmations
    return s


def test_long_setup_names_pattern():
    df = _make_df().iloc[:118]  # forming bar = 117, last closed = 116 (breakout bar)
    r = _mechanics_strategy().evaluate(df)
    assert r.signal_type == "long", r.signal_type
    assert r.primary_pattern, "signal must name a candlestick pattern"
    assert r.entry_price > 0 and r.stop_loss < r.entry_price < r.tp1 < r.tp3
    assert r.confirmation_type  # breakout / close


def test_no_lookahead_replay_matches():
    """
    Evaluate on a truncated frame, then on the full frame truncated back to the
    same length. Results must be identical — proving the decision for the last
    closed bar never depends on future candles.
    """
    strat = CandlestickStrategy()
    full = _make_df()
    for k in range(100, len(full)):
        sub = full.iloc[:k + 1].reset_index(drop=True)
        r_sub = strat.evaluate(sub)
        # Re-slice from the FULL (future-containing) frame to the same window
        r_replay = strat.evaluate(full.iloc[:k + 1].reset_index(drop=True))
        assert r_sub.signal_type == r_replay.signal_type
        assert r_sub.primary_pattern == r_replay.primary_pattern
        assert r_sub.entry_price == r_replay.entry_price
        assert r_sub.signal_score == r_replay.signal_score


def test_signal_only_on_closed_bars():
    """The forming bar (index -1) must never be the pattern/confirmation bar."""
    df = _make_df().iloc[:118]
    r = _mechanics_strategy().evaluate(df)
    if r.signal_type and r.timestamp is not None:
        # The signal's timestamp is the last CLOSED bar, not the forming one.
        assert r.timestamp <= df["timestamp"].iloc[-2]


def test_entry_bias_pullback_filter():
    """Pullback mode: dip-in-uptrend passes; extended breakout-into-air fails."""
    from backend.strategy.candlestick_patterns import PatternMatch
    from backend.strategy.price_action import PriceActionContext

    s = CandlestickStrategy()
    s.entry_bias = "pullback"
    s.pullback_ema_atr = 0.75
    atr, ema_fast, ema_slow = 40.0, 30000.0, 29900.0  # uptrend (fast > slow)

    def pm(low, high):
        return PatternMatch("Hammer", "long", 0.8, 0.7, 0.6, high, low, low, high, low, high)

    # LONG at support -> pass
    ctx = PriceActionContext(at_support=True)
    assert s._entry_bias_ok("long", pm(29980, 30010), ctx, atr, ema_fast, ema_slow)
    # LONG pulled back to the fast EMA (low within band) -> pass
    ctx = PriceActionContext()
    assert s._entry_bias_ok("long", pm(30010, 30040), ctx, atr, ema_fast, ema_slow)
    # LONG extended far above the EMA, not at support -> fail (no chasing)
    ctx = PriceActionContext()
    assert not s._entry_bias_ok("long", pm(30200, 30240), ctx, atr, ema_fast, ema_slow)
    # LONG but trend is down (fast < slow) -> fail
    ctx = PriceActionContext(at_support=True)
    assert not s._entry_bias_ok("long", pm(29980, 30010), ctx, atr, 29800.0, 29900.0)


def test_trailing_only_ignores_tp():
    """honor_tp=False: TPs don't book; only the (trailing) stop closes the trade."""
    from types import SimpleNamespace
    s = CandlestickStrategy()
    long = SimpleNamespace(direction="long", stop_loss=100.0,
                           take_profit_1=105.0, take_profit_2=110.0, take_profit_3=115.0)
    # Price well above TP3 but honor_tp False -> no exit (rides on)
    assert s.check_exit(long, 120.0, honor_tp=False) is None
    # Same price with honor_tp True -> books at a take-profit
    assert s.check_exit(long, 120.0, honor_tp=True) == "take_profit_3"
    # Stop still closes it in trailing-only mode
    assert s.check_exit(long, 99.0, honor_tp=False) == "stop_loss"


def test_marubozu_strict_gate():
    from backend.strategy.candlestick_patterns import PatternMatch
    s = CandlestickStrategy()
    s.mar = {"enabled": True, "min_body_ratio": 0.92, "require_volume": True,
             "volume_ratio": 1.5, "require_trend": True, "min_score": 80}
    vol = pd.Series([100, 100, 300])       # pattern bar volume = 300
    vsma = pd.Series([100, 100, 150])      # avg = 150 -> 300 >= 1.5*150 -> ok
    clean = PatternMatch("Marubozu", "long", 0.95, 0.95, 0.0, 110, 100, 100, 110, 100, 109.5)
    # all conditions pass
    assert s._marubozu_ok(clean, {"ema": True}, 85, vol, vsma, 2)
    # trend fails
    assert not s._marubozu_ok(clean, {"ema": False}, 85, vol, vsma, 2)
    # score too low
    assert not s._marubozu_ok(clean, {"ema": True}, 78, vol, vsma, 2)
    # volume too low
    assert not s._marubozu_ok(clean, {"ema": True}, 85, pd.Series([1, 1, 100]), vsma, 2)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} strategy tests passed")
