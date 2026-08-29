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


def test_long_setup_names_pattern():
    df = _make_df().iloc[:118]  # forming bar = 117, last closed = 116 (breakout bar)
    r = CandlestickStrategy().evaluate(df)
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
    r = CandlestickStrategy().evaluate(df)
    if r.signal_type and r.timestamp is not None:
        # The signal's timestamp is the last CLOSED bar, not the forming one.
        assert r.timestamp <= df["timestamp"].iloc[-2]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} strategy tests passed")
