"""
Candlestick pattern engine.

Computes full candle anatomy and detects 18 patterns on CLOSED candles only.
Every detector returns a `PatternMatch` carrying the metrics needed downstream
(scoring, stop placement, "why did this trade enter?"). No naive detection —
bodies, wicks, ratios and multi-candle relationships are all measured.

Thresholds come from the `strategy.candle` config block so the engine can be
tuned without code changes.

Convention: `candles[-1]` is the pattern candle (the most recent CLOSED bar).
Multi-candle patterns read backwards from there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ── Candle anatomy ────────────────────────────────────────────────────────────
@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bull(self) -> bool:
        return self.close > self.open

    @property
    def is_bear(self) -> bool:
        return self.close < self.open

    @property
    def direction(self) -> str:
        if self.close > self.open:
            return "bull"
        if self.close < self.open:
            return "bear"
        return "doji"

    @property
    def body_ratio(self) -> float:
        """Body as a fraction of total range (0..1)."""
        r = self.range
        return self.body / r if r > 0 else 0.0

    @property
    def upper_wick_ratio(self) -> float:
        r = self.range
        return self.upper_wick / r if r > 0 else 0.0

    @property
    def lower_wick_ratio(self) -> float:
        r = self.range
        return self.lower_wick / r if r > 0 else 0.0

    def wick_body(self, wick: float) -> float:
        """Ratio of a wick to the body (large ⇒ strong rejection)."""
        return wick / self.body if self.body > 0 else float("inf")

    @property
    def close_position(self) -> float:
        """Where the close sits in the range: 0 = at low, 1 = at high."""
        r = self.range
        return (self.close - self.low) / r if r > 0 else 0.5


def build_candles(df) -> List[Candle]:
    """Build Candle objects from an OHLCV DataFrame (chronological order)."""
    out: List[Candle] = []
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values if "volume" in df.columns else [0.0] * len(df)
    for i in range(len(df)):
        out.append(Candle(float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i])))
    return out


# ── Pattern result ────────────────────────────────────────────────────────────
@dataclass
class PatternMatch:
    name: str
    direction: str  # "long" / "short" / "neutral"
    strength: float  # 0..1 core structural strength
    quality: float  # 0..1 refinement (location, proportions)
    rejection_strength: float  # 0..1 wick-based rejection
    pattern_high: float  # high of the pattern (for stops / breakouts)
    pattern_low: float  # low of the pattern
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float
    n_candles: int = 1
    meta: dict = field(default_factory=dict)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _prior_trend(candles: List[Candle], end_idx: int, lookback: int = 5) -> str:
    """Direction of the run of candles BEFORE the pattern candle at end_idx."""
    start = max(0, end_idx - lookback)
    if end_idx - start < 2:
        return "flat"
    first = candles[start].close
    last = candles[end_idx - 1].close
    if last > first:
        return "up"
    if last < first:
        return "down"
    return "flat"


# ── Single-candle detectors ───────────────────────────────────────────────────
def _hammer(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0 or c.body <= 0:
        return None
    lw_body = c.wick_body(c.lower_wick)
    if (lw_body >= cfg["hammer_lower_wick_ratio"]
            and c.upper_wick_ratio <= cfg["hammer_upper_wick_max"]
            and c.body_ratio <= cfg["long_body_ratio"]):
        strength = _clip((lw_body - cfg["hammer_lower_wick_ratio"]) / 3 + 0.5)
        rej = _clip(c.lower_wick_ratio)
        return PatternMatch("Hammer", "long", strength, _clip(c.close_position),
                            rej, c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _inverted_hammer(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0 or c.body <= 0:
        return None
    uw_body = c.wick_body(c.upper_wick)
    if (uw_body >= cfg["hammer_lower_wick_ratio"]
            and c.lower_wick_ratio <= cfg["hammer_upper_wick_max"]
            and c.body_ratio <= cfg["long_body_ratio"]):
        strength = _clip((uw_body - cfg["hammer_lower_wick_ratio"]) / 3 + 0.45)
        rej = _clip(c.upper_wick_ratio)
        return PatternMatch("Inverted Hammer", "long", strength, _clip(1 - c.close_position),
                            rej, c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _shooting_star(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0 or c.body <= 0:
        return None
    uw_body = c.wick_body(c.upper_wick)
    if (uw_body >= cfg["hammer_lower_wick_ratio"]
            and c.lower_wick_ratio <= cfg["hammer_upper_wick_max"]
            and c.body_ratio <= cfg["long_body_ratio"]):
        strength = _clip((uw_body - cfg["hammer_lower_wick_ratio"]) / 3 + 0.5)
        rej = _clip(c.upper_wick_ratio)
        return PatternMatch("Shooting Star", "short", strength, _clip(1 - c.close_position),
                            rej, c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _bullish_pin(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if (c.wick_body(c.lower_wick) >= cfg["pin_wick_body_ratio"]
            and c.lower_wick_ratio >= cfg["rejection_wick_ratio"]
            and c.close_position >= 0.5):
        return PatternMatch("Bullish Pin Bar", "long",
                            _clip(c.lower_wick_ratio + 0.2), _clip(c.close_position),
                            _clip(c.lower_wick_ratio), c.high, c.low,
                            c.open, c.high, c.low, c.close)
    return None


def _bearish_pin(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if (c.wick_body(c.upper_wick) >= cfg["pin_wick_body_ratio"]
            and c.upper_wick_ratio >= cfg["rejection_wick_ratio"]
            and c.close_position <= 0.5):
        return PatternMatch("Bearish Pin Bar", "short",
                            _clip(c.upper_wick_ratio + 0.2), _clip(1 - c.close_position),
                            _clip(c.upper_wick_ratio), c.high, c.low,
                            c.open, c.high, c.low, c.close)
    return None


def _doji(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if c.body_ratio <= cfg["doji_body_ratio"]:
        return PatternMatch("Doji", "neutral", _clip(1 - c.body_ratio * 5), 0.5,
                            _clip(max(c.upper_wick_ratio, c.lower_wick_ratio)),
                            c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _marubozu(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if c.body_ratio >= cfg["marubozu_body_ratio"]:
        direction = "long" if c.is_bull else "short"
        return PatternMatch("Marubozu", direction, _clip(c.body_ratio), _clip(c.body_ratio),
                            0.0, c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _strong_bull_rejection(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if (c.is_bull and c.lower_wick_ratio >= cfg["rejection_wick_ratio"]
            and c.close_position >= 0.6):
        return PatternMatch("Strong Bullish Rejection", "long",
                            _clip(c.lower_wick_ratio + c.body_ratio * 0.5),
                            _clip(c.close_position), _clip(c.lower_wick_ratio),
                            c.high, c.low, c.open, c.high, c.low, c.close)
    return None


def _strong_bear_rejection(c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if c.range <= 0:
        return None
    if (c.is_bear and c.upper_wick_ratio >= cfg["rejection_wick_ratio"]
            and c.close_position <= 0.4):
        return PatternMatch("Strong Bearish Rejection", "short",
                            _clip(c.upper_wick_ratio + c.body_ratio * 0.5),
                            _clip(1 - c.close_position), _clip(c.upper_wick_ratio),
                            c.high, c.low, c.open, c.high, c.low, c.close)
    return None


# ── Two-candle detectors ──────────────────────────────────────────────────────
def _bullish_engulfing(prev: Candle, cur: Candle, cfg: dict) -> Optional[PatternMatch]:
    if prev.body <= 0 or cur.body <= 0:
        return None
    if (prev.is_bear and cur.is_bull
            and cur.close >= prev.open and cur.open <= prev.close
            and cur.body >= cfg["engulf_min_ratio"] * prev.body):
        strength = _clip(0.5 + (cur.body / prev.body - 1) * 0.25)
        return PatternMatch("Bullish Engulfing", "long", strength,
                            _clip(cur.body_ratio), _clip(cur.lower_wick_ratio),
                            max(prev.high, cur.high), min(prev.low, cur.low),
                            cur.open, cur.high, cur.low, cur.close, n_candles=2)
    return None


def _bearish_engulfing(prev: Candle, cur: Candle, cfg: dict) -> Optional[PatternMatch]:
    if prev.body <= 0 or cur.body <= 0:
        return None
    if (prev.is_bull and cur.is_bear
            and cur.open >= prev.close and cur.close <= prev.open
            and cur.body >= cfg["engulf_min_ratio"] * prev.body):
        strength = _clip(0.5 + (cur.body / prev.body - 1) * 0.25)
        return PatternMatch("Bearish Engulfing", "short", strength,
                            _clip(cur.body_ratio), _clip(cur.upper_wick_ratio),
                            max(prev.high, cur.high), min(prev.low, cur.low),
                            cur.open, cur.high, cur.low, cur.close, n_candles=2)
    return None


def _bullish_harami(prev: Candle, cur: Candle, cfg: dict) -> Optional[PatternMatch]:
    if prev.body <= 0 or cur.body <= 0:
        return None
    if (prev.is_bear and cur.is_bull
            and cur.open > prev.close and cur.close < prev.open
            and cur.body_ratio <= cfg["long_body_ratio"]
            and prev.body_ratio >= cfg["long_body_ratio"]):
        return PatternMatch("Bullish Harami", "long", 0.55, _clip(1 - cur.body / prev.body),
                            _clip(cur.lower_wick_ratio),
                            max(prev.high, cur.high), min(prev.low, cur.low),
                            cur.open, cur.high, cur.low, cur.close, n_candles=2)
    return None


def _bearish_harami(prev: Candle, cur: Candle, cfg: dict) -> Optional[PatternMatch]:
    if prev.body <= 0 or cur.body <= 0:
        return None
    if (prev.is_bull and cur.is_bear
            and cur.open < prev.close and cur.close > prev.open
            and cur.body_ratio <= cfg["long_body_ratio"]
            and prev.body_ratio >= cfg["long_body_ratio"]):
        return PatternMatch("Bearish Harami", "short", 0.55, _clip(1 - cur.body / prev.body),
                            _clip(cur.upper_wick_ratio),
                            max(prev.high, cur.high), min(prev.low, cur.low),
                            cur.open, cur.high, cur.low, cur.close, n_candles=2)
    return None


def _inside_bar(prev: Candle, cur: Candle, cfg: dict) -> Optional[PatternMatch]:
    if cur.high < prev.high and cur.low > prev.low:
        return PatternMatch("Inside Bar", "neutral", 0.4, _clip(1 - cur.range / prev.range),
                            0.0, prev.high, prev.low,
                            cur.open, cur.high, cur.low, cur.close, n_candles=2)
    return None


# ── Three-candle detectors ────────────────────────────────────────────────────
def _morning_star(a: Candle, b: Candle, c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if a.body <= 0 or c.body <= 0:
        return None
    mid_a = (a.open + a.close) / 2
    if (a.is_bear and a.body_ratio >= cfg["long_body_ratio"]
            and b.body_ratio <= cfg["small_body_ratio"]
            and c.is_bull and c.close > mid_a):
        return PatternMatch("Morning Star", "long", 0.7, _clip((c.close - mid_a) / a.body),
                            _clip(b.lower_wick_ratio),
                            max(a.high, b.high, c.high), min(a.low, b.low, c.low),
                            c.open, c.high, c.low, c.close, n_candles=3)
    return None


def _evening_star(a: Candle, b: Candle, c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if a.body <= 0 or c.body <= 0:
        return None
    mid_a = (a.open + a.close) / 2
    if (a.is_bull and a.body_ratio >= cfg["long_body_ratio"]
            and b.body_ratio <= cfg["small_body_ratio"]
            and c.is_bear and c.close < mid_a):
        return PatternMatch("Evening Star", "short", 0.7, _clip((mid_a - c.close) / a.body),
                            _clip(b.upper_wick_ratio),
                            max(a.high, b.high, c.high), min(a.low, b.low, c.low),
                            c.open, c.high, c.low, c.close, n_candles=3)
    return None


def _three_white_soldiers(a: Candle, b: Candle, c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if not (a.is_bull and b.is_bull and c.is_bull):
        return None
    if (b.close > a.close and c.close > b.close
            and b.open > a.open and c.open > b.open
            and a.body_ratio >= cfg["small_body_ratio"]
            and b.body_ratio >= cfg["small_body_ratio"]
            and c.body_ratio >= cfg["small_body_ratio"]):
        return PatternMatch("Three White Soldiers", "long", 0.75,
                            _clip((a.body_ratio + b.body_ratio + c.body_ratio) / 3),
                            0.0, max(a.high, b.high, c.high), min(a.low, b.low, c.low),
                            c.open, c.high, c.low, c.close, n_candles=3)
    return None


def _three_black_crows(a: Candle, b: Candle, c: Candle, cfg: dict) -> Optional[PatternMatch]:
    if not (a.is_bear and b.is_bear and c.is_bear):
        return None
    if (b.close < a.close and c.close < b.close
            and b.open < a.open and c.open < b.open
            and a.body_ratio >= cfg["small_body_ratio"]
            and b.body_ratio >= cfg["small_body_ratio"]
            and c.body_ratio >= cfg["small_body_ratio"]):
        return PatternMatch("Three Black Crows", "short", 0.75,
                            _clip((a.body_ratio + b.body_ratio + c.body_ratio) / 3),
                            0.0, max(a.high, b.high, c.high), min(a.low, b.low, c.low),
                            c.open, c.high, c.low, c.close, n_candles=3)
    return None


# ── Orchestration ─────────────────────────────────────────────────────────────
DEFAULT_CANDLE_CFG = {
    "doji_body_ratio": 0.1,
    "marubozu_body_ratio": 0.9,
    "long_body_ratio": 0.6,
    "small_body_ratio": 0.33,
    "pin_wick_body_ratio": 2.0,
    "hammer_lower_wick_ratio": 2.0,
    "hammer_upper_wick_max": 0.3,
    "rejection_wick_ratio": 0.5,
    "engulf_min_ratio": 1.0,
    "min_range_atr_ratio": 0.3,
}


def detect_patterns(candles: List[Candle], cfg: Optional[dict] = None,
                    atr: float = 0.0) -> List[PatternMatch]:
    """
    Detect every candlestick pattern ending on `candles[-1]` (the pattern candle,
    which MUST be a closed bar). Returns all matches; ranking/selection happens
    in the strategy layer.

    A `min_range_atr_ratio` filter drops micro-candles whose range is tiny
    relative to ATR (noise on 1m BTC futures).
    """
    ccfg = {**DEFAULT_CANDLE_CFG, **(cfg or {})}
    if len(candles) < 1:
        return []

    cur = candles[-1]
    # Noise filter: ignore patterns on candles far smaller than current volatility.
    if atr > 0 and cur.range < ccfg["min_range_atr_ratio"] * atr:
        return []

    matches: List[PatternMatch] = []
    idx = len(candles) - 1
    trend = _prior_trend(candles, idx, lookback=5)

    # Single-candle
    for fn in (_hammer, _inverted_hammer, _shooting_star, _bullish_pin, _bearish_pin,
               _doji, _marubozu, _strong_bull_rejection, _strong_bear_rejection):
        m = fn(cur, ccfg)
        if m:
            matches.append(m)

    # Two-candle
    if len(candles) >= 2:
        prev = candles[-2]
        for fn in (_bullish_engulfing, _bearish_engulfing, _bullish_harami,
                   _bearish_harami, _inside_bar):
            m = fn(prev, cur, ccfg)
            if m:
                matches.append(m)

    # Three-candle
    if len(candles) >= 3:
        a, b, c = candles[-3], candles[-2], candles[-1]
        for fn in (_morning_star, _evening_star, _three_white_soldiers, _three_black_crows):
            m = fn(a, b, c, ccfg)
            if m:
                matches.append(m)

    # Location bonus: reversal longs after a downtrend / shorts after an uptrend
    for m in matches:
        if m.direction == "long" and trend == "down":
            m.quality = _clip(m.quality + 0.15)
            m.meta["location"] = "after_downtrend"
        elif m.direction == "short" and trend == "up":
            m.quality = _clip(m.quality + 0.15)
            m.meta["location"] = "after_uptrend"

    return matches
