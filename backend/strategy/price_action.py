"""
Price-action context for candlestick setups.

Adds structure around a detected pattern — swing highs/lows, support/resistance
zones, breakouts and rejections — WITHOUT becoming the trade trigger. The
candlestick pattern remains primary; this only strengthens or weakens it.

All lookbacks are relative to the pattern bar index (a closed bar), so nothing
here uses future information.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from backend.strategy.candlestick_patterns import Candle


@dataclass
class PriceActionContext:
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    at_support: bool = False
    at_resistance: bool = False
    breakout_up: bool = False
    breakout_down: bool = False
    structure: str = "none"  # HH_HL / LH_LL / mixed / none
    label: str = "none"      # human context label used in the UI/DB
    score_long: float = 0.0  # 0..1 contribution for a long setup
    score_short: float = 0.0
    meta: dict = field(default_factory=dict)


def find_swing_highs(candles: List[Candle], idx: int, left: int, right: int) -> List[float]:
    """Fractal swing highs strictly before `idx` (needs `right` confirmed bars)."""
    highs = []
    for i in range(left, idx - right):
        pivot = candles[i].high
        if all(candles[i].high >= candles[j].high for j in range(i - left, i)) and \
           all(candles[i].high > candles[j].high for j in range(i + 1, i + right + 1)):
            highs.append(pivot)
    return highs


def find_swing_lows(candles: List[Candle], idx: int, left: int, right: int) -> List[float]:
    """Fractal swing lows strictly before `idx`."""
    lows = []
    for i in range(left, idx - right):
        pivot = candles[i].low
        if all(candles[i].low <= candles[j].low for j in range(i - left, i)) and \
           all(candles[i].low < candles[j].low for j in range(i + 1, i + right + 1)):
            lows.append(pivot)
    return lows


def _structure(swing_highs: List[float], swing_lows: List[float]) -> str:
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]
        if hh and hl:
            return "HH_HL"
        if lh and ll:
            return "LH_LL"
        return "mixed"
    return "none"


def analyze(candles: List[Candle], idx: int, cfg: dict, atr: float,
            pattern_low: float, pattern_high: float,
            direction: str) -> PriceActionContext:
    """
    Build price-action context for a pattern at `candles[idx]`.

    `cfg` is the `strategy.price_action` block:
      swing_lookback, sr_lookback, sr_zone_atr_mult, breakout_lookback
    """
    ctx = PriceActionContext()
    if idx < 3:
        return ctx

    swing = cfg.get("swing_lookback", 5)
    sr_lb = cfg.get("sr_lookback", 50)
    zone = cfg.get("sr_zone_atr_mult", 0.5) * (atr if atr > 0 else 0)
    brk_lb = cfg.get("breakout_lookback", 20)

    start = max(0, idx - sr_lb)
    window = candles[start:idx]  # excludes the pattern bar itself
    if not window:
        return ctx

    highs = find_swing_highs(candles, idx, swing, swing) or [c.high for c in window]
    lows = find_swing_lows(candles, idx, swing, swing) or [c.low for c in window]

    close = candles[idx].close
    res_above = [h for h in highs if h >= close]
    sup_below = [l for l in lows if l <= close]
    ctx.nearest_resistance = min(res_above) if res_above else max(highs)
    ctx.nearest_support = max(sup_below) if sup_below else min(lows)

    if zone > 0:
        if ctx.nearest_support is not None and abs(pattern_low - ctx.nearest_support) <= zone:
            ctx.at_support = True
        if ctx.nearest_resistance is not None and abs(pattern_high - ctx.nearest_resistance) <= zone:
            ctx.at_resistance = True

    # Breakout over the recent range (pattern bar breaks prior extreme)
    brk_start = max(0, idx - brk_lb)
    prior = candles[brk_start:idx]
    if prior:
        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)
        ctx.breakout_up = candles[idx].close > prior_high
        ctx.breakout_down = candles[idx].close < prior_low

    ctx.structure = _structure(highs, lows)

    # ── Contributions & label ──
    label = "none"
    if direction == "long":
        s = 0.0
        if ctx.at_support:
            s += 0.6
            label = "support_rejection"
        if ctx.breakout_up:
            s += 0.4
            label = "breakout" if label == "none" else "support_breakout"
        if ctx.structure == "HH_HL":
            s += 0.2
        ctx.score_long = min(1.0, s)
    elif direction == "short":
        s = 0.0
        if ctx.at_resistance:
            s += 0.6
            label = "resistance_rejection"
        if ctx.breakout_down:
            s += 0.4
            label = "breakdown" if label == "none" else "resistance_breakdown"
        if ctx.structure == "LH_LL":
            s += 0.2
        ctx.score_short = min(1.0, s)

    ctx.label = label
    return ctx
