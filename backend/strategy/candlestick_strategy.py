"""
Candlestick strategy engine — the SINGLE decision engine used by both the live
trading loop and any offline replay. Candlestick pattern = primary trade reason;
indicators are optional confirmations/filters; price action adds context.

No look-ahead / no repaint:
  * The caller passes a DataFrame whose LAST row is the still-forming bar.
  * The last CLOSED bar is index `-2` (`t` below). Every computation uses data
    at indices <= t only.
  * A breakout confirmation fires exactly on the bar that first breaks the
    pattern's extreme, so replaying history reproduces live signals bar-for-bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from backend.config import Config
from backend.indicators.technical import rsi, sma, ema, atr, adx
from backend.strategy import price_action as pa
from backend.strategy.candlestick_patterns import (
    Candle, build_candles, detect_patterns, PatternMatch,
)
from backend.strategy.scorer import score_setup


@dataclass
class SignalResult:
    signal_type: Optional[str] = None  # "long" / "short" / None
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    signal_score: float = 0.0

    # Candlestick "why"
    primary_pattern: str = ""
    secondary_patterns: List[str] = field(default_factory=list)
    confirmation_type: str = "none"
    price_action_context: str = "none"
    indicator_confirmations: dict = field(default_factory=dict)
    rejection_strength: float = 0.0
    pattern_high: float = 0.0
    pattern_low: float = 0.0
    pattern_open: float = 0.0
    pattern_candle_high: float = 0.0
    pattern_candle_low: float = 0.0
    pattern_close: float = 0.0

    # Indicator context (for display / DB)
    rsi_value: float = 0.0
    adx_value: float = 0.0
    atr_value: float = 0.0
    ema_fast_value: float = 0.0
    ema_slow_value: float = 0.0
    trend_ma_value: float = 0.0
    regime: str = "neutral"
    score_breakdown: dict = field(default_factory=dict)
    timestamp: Optional[datetime] = None

    @property
    def direction(self) -> Optional[str]:
        return self.signal_type

    @property
    def setup_id(self) -> str:
        ts = self.timestamp.isoformat() if self.timestamp is not None else "?"
        return f"{self.primary_pattern}|{self.signal_type}|{ts}"


class CandlestickStrategy:
    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        self.cfg = cfg
        self.timeframe = cfg.get("strategy.timeframe", "1m")
        self.min_score = cfg.get("strategy.min_signal_score", 70)
        self.conf_mode = cfg.get("strategy.confirmation_mode", "breakout")
        self.conf_window = cfg.get("strategy.confirmation_window", 3)

        self.candle_cfg = cfg.get("strategy.candle", {})
        self.pa_cfg = cfg.get("strategy.price_action", {})
        self.conf = cfg.get("strategy.confirmations", {})
        self.weights = cfg.get("strategy.weights", {})

        self.atr_len = cfg.get("strategy.atr_length", 14)
        self.sl_buf_mult = cfg.get("strategy.sl_buffer_atr_mult", 0.25)
        self.tp1_r = cfg.get("strategy.tp1_r", 1.0)
        self.tp2_r = cfg.get("strategy.tp2_r", 2.0)
        self.tp3_r = cfg.get("strategy.tp3_r", 3.0)
        self.max_loss_points = cfg.get("risk.max_loss_points", 0)
        self.reversal_min = cfg.get("strategy.reversal_min_score", 65)

        self._enabled = {
            "ema": self.conf.get("enable_ema", True),
            "rsi": self.conf.get("enable_rsi", True),
            "adx": self.conf.get("enable_adx", False),
            "cardwell": self.conf.get("enable_cardwell", True),
            "volume": self.conf.get("enable_volume", True),
        }

    # ── public API ────────────────────────────────────────────────────────────
    def evaluate(self, df: pd.DataFrame, htf_df: Optional[pd.DataFrame] = None) -> SignalResult:
        """Evaluate the strategy; returns the signal for the confirming bar (or empty)."""
        min_bars = max(self.atr_len, 50, self.conf.get("ema_slow", 50)) + self.conf_window + 5
        if df is None or len(df) < min_bars:
            return SignalResult()

        candles = build_candles(df)
        t = len(candles) - 2  # last CLOSED bar
        if t < 5:
            return SignalResult()

        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"] if "volume" in df.columns else pd.Series(np.zeros(len(df)))

        atr_s = atr(high, low, close, self.atr_len)
        rsi_s = rsi(close, self.conf.get("rsi_length", 14))
        adx_s = adx(high, low, close, self.conf.get("adx_length", 14))
        ema_fast_s = ema(close, self.conf.get("ema_fast", 21))
        ema_slow_s = ema(close, self.conf.get("ema_slow", 50))
        sma_trend_s = sma(close, self.conf.get("cardwell_ma", 50))
        vol_sma_s = sma(vol, self.conf.get("volume_avg_period", 20))

        cur_atr = _safe(atr_s.iloc[t], 0.0)

        # HTF filter (hard filter when enabled)
        htf_bull_ok, htf_bear_ok = self._htf_ok(htf_df)

        # Scan the confirmation window for a pattern whose confirming bar is `t`.
        setup = self._find_confirmed_setup(candles, t, atr_s, htf_bull_ok, htf_bear_ok)
        base = self._context_result(candles, t, rsi_s, adx_s, atr_s,
                                    ema_fast_s, ema_slow_s, sma_trend_s, df)
        if setup is None:
            return base

        primary, secondaries, entry, confirmed, pat_idx = setup
        direction = primary.direction

        # SL from pattern structure; TP by R-multiples of risk
        buf = self.sl_buf_mult * cur_atr
        if direction == "long":
            sl = primary.pattern_low - buf
            risk = entry - sl
            if self.max_loss_points and risk > self.max_loss_points:
                sl = entry - self.max_loss_points
                risk = self.max_loss_points
            tp1, tp2, tp3 = (entry + self.tp1_r * risk, entry + self.tp2_r * risk,
                             entry + self.tp3_r * risk)
        else:
            sl = primary.pattern_high + buf
            risk = sl - entry
            if self.max_loss_points and risk > self.max_loss_points:
                sl = entry + self.max_loss_points
                risk = self.max_loss_points
            tp1, tp2, tp3 = (entry - self.tp1_r * risk, entry - self.tp2_r * risk,
                             entry - self.tp3_r * risk)

        if risk <= 0:
            return base

        # Confirmations (indicators)
        confirms = self._confirmations(direction, t, rsi_s, adx_s, ema_fast_s,
                                       ema_slow_s, sma_trend_s, vol, vol_sma_s, close)
        pa_ctx = pa.analyze(candles, t, self.pa_cfg, cur_atr,
                            primary.pattern_low, primary.pattern_high, direction)
        pa_score = pa_ctx.score_long if direction == "long" else pa_ctx.score_short

        score, breakdown = score_setup(
            pattern_strength=primary.strength,
            pattern_quality=primary.quality,
            price_action_score=pa_score,
            confirmations=confirms,
            enabled=self._enabled,
            breakout_ok=confirmed,
            rr_ratio=self.tp3_r,
            weights=self.weights,
        )

        # Fill result context (always) then gate on score
        base.signal_score = score
        base.score_breakdown = breakdown
        if score < self.min_score:
            return base  # context only, no trade

        base.signal_type = direction
        base.entry_price = round(float(entry), 2)
        base.stop_loss = round(float(sl), 2)
        base.tp1 = round(float(tp1), 2)
        base.tp2 = round(float(tp2), 2)
        base.tp3 = round(float(tp3), 2)
        base.primary_pattern = primary.name
        base.secondary_patterns = [m.name for m in secondaries]
        base.confirmation_type = ("Pattern High Breakout" if direction == "long"
                                  else "Pattern Low Breakdown") if confirmed else "Candle Close"
        base.price_action_context = pa_ctx.label
        base.indicator_confirmations = confirms
        base.rejection_strength = round(float(primary.rejection_strength), 3)
        base.pattern_high = round(float(primary.pattern_high), 2)
        base.pattern_low = round(float(primary.pattern_low), 2)
        base.pattern_open = round(float(primary.candle_open), 2)
        base.pattern_candle_high = round(float(primary.candle_high), 2)
        base.pattern_candle_low = round(float(primary.candle_low), 2)
        base.pattern_close = round(float(primary.candle_close), 2)
        return base

    def check_exit(self, signal, current_price: float) -> Optional[str]:
        """SL/TP exit check for an open position (reused from the reference design)."""
        if signal.direction == "long":
            if current_price <= signal.stop_loss:
                return "stop_loss"
            if signal.take_profit_3 and current_price >= signal.take_profit_3:
                return "take_profit_3"
            if signal.take_profit_2 and current_price >= signal.take_profit_2:
                return "take_profit_2"
            if signal.take_profit_1 and current_price >= signal.take_profit_1:
                return "take_profit_1"
        elif signal.direction == "short":
            if current_price >= signal.stop_loss:
                return "stop_loss"
            if signal.take_profit_3 and current_price <= signal.take_profit_3:
                return "take_profit_3"
            if signal.take_profit_2 and current_price <= signal.take_profit_2:
                return "take_profit_2"
            if signal.take_profit_1 and current_price <= signal.take_profit_1:
                return "take_profit_1"
        return None

    # ── internals ─────────────────────────────────────────────────────────────
    def _find_confirmed_setup(self, candles, t, atr_s, htf_bull_ok, htf_bear_ok):
        """
        Look for a directional pattern in [t-window, t] whose confirming bar is
        exactly `t`. Returns (primary, secondaries, entry, confirmed, pat_idx)
        or None. Only the most-recent qualifying pattern is used (one signal).
        """
        window = self.conf_window if self.conf_mode == "breakout" else 0
        for p in range(t, t - window - 1, -1):
            if p < 3:
                break
            sub = candles[:p + 1]
            atr_p = _safe(atr_s.iloc[p], 0.0)
            matches = detect_patterns(sub, self.candle_cfg, atr_p)
            directional = [m for m in matches if m.direction in ("long", "short")]
            if not directional:
                continue
            directional.sort(key=lambda m: m.strength * 0.6 + m.quality * 0.4, reverse=True)
            primary = directional[0]
            secondaries = [m for m in directional[1:] if m.direction == primary.direction]

            # HTF hard filter
            if primary.direction == "long" and not htf_bull_ok:
                continue
            if primary.direction == "short" and not htf_bear_ok:
                continue

            if self.conf_mode in ("close", "none"):
                if p != t:
                    continue
                entry = candles[t].close
                return primary, secondaries, entry, (self.conf_mode == "close"), p

            # breakout mode
            ext = primary.pattern_high if primary.direction == "long" else primary.pattern_low
            if p == t:
                continue  # a pattern needs a LATER bar to confirm its breakout
            if primary.direction == "long":
                broke_before = any(candles[b].high > ext for b in range(p + 1, t))
                broke_now = candles[t].high > ext
            else:
                broke_before = any(candles[b].low < ext for b in range(p + 1, t))
                broke_now = candles[t].low < ext
            if broke_now and not broke_before:
                return primary, secondaries, ext, True, p
            # if already broken earlier, this setup is stale — stop searching it
            if broke_before:
                continue
        return None

    def _confirmations(self, direction, t, rsi_s, adx_s, ema_fast_s, ema_slow_s,
                       sma_trend_s, vol, vol_sma_s, close):
        r = _safe(rsi_s.iloc[t], 50.0)
        a = _safe(adx_s.iloc[t], 0.0)
        ef = _safe(ema_fast_s.iloc[t], 0.0)
        es = _safe(ema_slow_s.iloc[t], 0.0)
        ma = _safe(sma_trend_s.iloc[t], 0.0)
        c = _safe(close.iloc[t], 0.0)
        v = _safe(vol.iloc[t], 0.0)
        vavg = _safe(vol_sma_s.iloc[t], 0.0)

        out = {}
        if direction == "long":
            out["ema"] = ef > es and c > es
            out["rsi"] = r >= self.conf.get("rsi_bull_min", 45)
            out["cardwell"] = (c > ma and self.conf.get("cardwell_bull_low", 40)
                               <= r <= self.conf.get("cardwell_bull_high", 80))
        else:
            out["ema"] = ef < es and c < es
            out["rsi"] = r <= self.conf.get("rsi_bear_max", 55)
            out["cardwell"] = (c < ma and self.conf.get("cardwell_bear_low", 20)
                               <= r <= self.conf.get("cardwell_bear_high", 60))
        out["adx"] = a >= self.conf.get("adx_min_strength", 20)
        out["volume"] = vavg > 0 and v >= self.conf.get("volume_confirm_ratio", 1.2) * vavg
        return out

    def _htf_ok(self, htf_df):
        if not self.conf.get("enable_htf", True) or htf_df is None:
            return True, True
        ma_len = self.conf.get("ema_slow", 50)
        if len(htf_df) < ma_len + 2:
            return True, True
        hc = htf_df["close"]
        hma = sma(hc, ma_len)
        i = len(htf_df) - 2
        c = _safe(hc.iloc[i], 0.0)
        m = _safe(hma.iloc[i], 0.0)
        return c > m, c < m

    def _context_result(self, candles, t, rsi_s, adx_s, atr_s,
                        ema_fast_s, ema_slow_s, sma_trend_s, df) -> SignalResult:
        c = _safe(df["close"].iloc[t], 0.0)
        ma = _safe(sma_trend_s.iloc[t], 0.0)
        r = _safe(rsi_s.iloc[t], 50.0)
        regime = "neutral"
        if c > ma and self.conf.get("cardwell_bull_low", 40) <= r <= self.conf.get("cardwell_bull_high", 80):
            regime = "bullish"
        elif c < ma and self.conf.get("cardwell_bear_low", 20) <= r <= self.conf.get("cardwell_bear_high", 60):
            regime = "bearish"
        return SignalResult(
            rsi_value=round(r, 2),
            adx_value=round(_safe(adx_s.iloc[t], 0.0), 2),
            atr_value=round(_safe(atr_s.iloc[t], 0.0), 2),
            ema_fast_value=round(_safe(ema_fast_s.iloc[t], 0.0), 2),
            ema_slow_value=round(_safe(ema_slow_s.iloc[t], 0.0), 2),
            trend_ma_value=round(ma, 2),
            regime=regime,
            timestamp=df["timestamp"].iloc[t] if "timestamp" in df.columns else None,
        )


def _safe(v, default=0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default
