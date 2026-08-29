"""
Configurable confluence scorer for candlestick setups.

The candlestick pattern is always the dominant term. Indicator confirmations
(EMA / RSI / ADX / Cardwell-regime / volume) and breakout/price-action add
points only. Disabled confirmations are removed from the denominator so the
0–100 scale — and therefore `min_signal_score` — stays meaningful regardless of
which confirmations are switched on.
"""
from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_WEIGHTS = {
    "pattern_strength": 40,
    "pattern_quality": 10,
    "price_action": 10,
    "ema": 10,
    "rsi": 5,
    "adx": 5,
    "cardwell": 5,
    "volume": 5,
    "breakout": 10,
    "risk_reward": 5,
}

# Confirmations that can be toggled off; the others are always part of the score.
TOGGLEABLE = ("ema", "rsi", "adx", "cardwell", "volume")


def score_setup(
    pattern_strength: float,
    pattern_quality: float,
    price_action_score: float,
    confirmations: Dict[str, bool],
    enabled: Dict[str, bool],
    breakout_ok: bool,
    rr_ratio: float,
    weights: Dict[str, float] = None,
) -> Tuple[float, Dict[str, float]]:
    """
    Returns (score 0..100, breakdown of earned points per component).

    - pattern_strength / pattern_quality / price_action_score / rr_ratio: 0..1
    - confirmations: {name: aligned?} for ema/rsi/adx/cardwell/volume
    - enabled: {name: on?} — a disabled confirmation contributes no points AND
      is excluded from the max possible.
    - breakout_ok: whether the entry is confirmed by a pattern breakout.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    earned: Dict[str, float] = {}
    max_possible = 0.0

    # Always-on components
    earned["pattern_strength"] = w["pattern_strength"] * _clip(pattern_strength)
    earned["pattern_quality"] = w["pattern_quality"] * _clip(pattern_quality)
    earned["price_action"] = w["price_action"] * _clip(price_action_score)
    earned["breakout"] = w["breakout"] if breakout_ok else 0.0
    earned["risk_reward"] = w["risk_reward"] * _clip(min(1.0, rr_ratio / 3.0))
    for k in ("pattern_strength", "pattern_quality", "price_action", "breakout", "risk_reward"):
        max_possible += w[k]

    # Toggleable confirmations
    for name in TOGGLEABLE:
        if enabled.get(name, False):
            max_possible += w[name]
            earned[name] = w[name] if confirmations.get(name, False) else 0.0
        else:
            earned[name] = 0.0

    total = sum(earned.values())
    score = (total / max_possible * 100.0) if max_possible > 0 else 0.0
    return round(score, 1), earned


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))
