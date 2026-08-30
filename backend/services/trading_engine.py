"""
Trading engine — runs the candlestick strategy loop continuously on closed bars.

Reuses the reference engine's structure (async tick loop aligned to bar close,
SQLite persistence, reversal exits, Telegram alerts) and adds:
  * setup-ID + cooldown duplicate-signal prevention
  * pattern-aware signal recording (which pattern caused the trade)
  * break-even, trailing-stop, max-duration and opposite-pattern exits
"""
import asyncio
import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import Optional

from backend.config import Config
from backend.database.models import Signal, get_session, init_db
from backend.exchange.data_feed import DataFeed
from backend.indicators.technical import atr as calc_atr
from backend.strategy.candlestick_strategy import CandlestickStrategy, SignalResult
from backend.telegram.notifier import TelegramNotifier

logger = logging.getLogger("engine")

TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400,
}

# Only these carry enough authority to flip an open position (reversal exit).
# A plain opposite Marubozu/Doji must NOT whipsaw us out of a good trade.
REVERSAL_PATTERNS = {
    "Bullish Engulfing", "Bearish Engulfing", "Morning Star", "Evening Star",
    "Hammer", "Shooting Star", "Bullish Pin Bar", "Bearish Pin Bar",
    "Three White Soldiers", "Three Black Crows",
    "Strong Bullish Rejection", "Strong Bearish Rejection",
}


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt) -> datetime:
    if dt is None:
        return utcnow_naive()
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class TradingEngine:
    def __init__(self):
        self.config = Config()
        self.strategy = CandlestickStrategy(self.config)
        self.feed = DataFeed(self.config)
        self.notifier = TelegramNotifier(self.config)
        self.engine = init_db()
        self._running = False
        self._last_setup_id: Optional[str] = None
        self._last_signal_bar: Optional[datetime] = None
        self._last_signal: Optional[SignalResult] = None
        self._current_price: float = 0.0
        self._current_atr: float = 0.0
        self._status = "stopped"
        # Serialises exit checks so the signal loop and the fast exit-monitor
        # loop can never both close the same position.
        self._exit_lock = asyncio.Lock()
        self._monitor_task: Optional[asyncio.Task] = None
        # Last stop level we alerted per open trade, to throttle trailing-stop
        # move notifications (id -> stop_loss).
        self._alerted_stop: dict = {}

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_signal(self) -> Optional[SignalResult]:
        return self._last_signal

    @property
    def current_price(self) -> float:
        return self._current_price

    async def start(self):
        self._running = True
        self._status = "connecting"
        if not await self.feed.connect():
            self._status = "disconnected"
            logger.error("Could not connect to any exchange")
            return
        self._status = "running"
        logger.info(f"Trading engine started on {self.feed.exchange_name}")

        # Fast exit monitor runs independently of the bar-close signal loop so
        # stop-loss / take-profit are checked in near-real-time, not once a bar.
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._exit_monitor_loop())

        while self._running:
            try:
                await self._tick()
                if self._status != "running":
                    self._status = "running"
                    logger.info("Engine recovered — status running")
            except Exception as e:
                logger.exception(f"Engine tick error: {type(e).__name__}: {e}")
                self._status = "error"
                await asyncio.sleep(5)
                if not self.feed.is_connected:
                    self._status = "reconnecting"
                    try:
                        await self.feed.connect()
                    except Exception as ce:
                        logger.error(f"Reconnect failed: {ce}")
                continue

            tf = self.config.get("strategy.timeframe", "1m")
            interval = TIMEFRAME_SECONDS.get(tf, 60)
            now = _time.time()
            await asyncio.sleep(interval - (now % interval) + 3)

    async def _exit_monitor_loop(self):
        """Poll the live price frequently and check SL/TP on open positions.

        This closes the gap where a stop could sit un-triggered for up to a full
        bar: the signal loop only checks exits once per candle close, whereas
        real stops must react to price the moment it is touched.
        """
        while self._running:
            poll = max(1, int(self.config.get("strategy.exit_poll_seconds", 5)))
            await asyncio.sleep(poll)
            if not self._running or not self.feed.is_connected:
                continue
            try:
                price = await self.feed.get_current_price()
                if price and price > 0:
                    self._current_price = float(price)
                    await self._check_exits()
            except Exception as e:
                logger.debug(f"Exit monitor skipped a poll: {type(e).__name__}: {e}")

    async def reload(self):
        logger.info("Reloading engine configuration...")
        self.strategy = CandlestickStrategy(self.config)
        old_feed = self.feed
        self.feed = DataFeed(self.config)
        self.notifier = TelegramNotifier(self.config)
        self._status = "reconnecting"
        try:
            connected = await self.feed.connect()
        except Exception as e:
            logger.error(f"Reload connect failed: {e}")
            connected = False
        try:
            await old_feed.close()
        except Exception:
            pass
        self._status = "running" if connected else "disconnected"
        logger.info(f"Engine reloaded — connected={connected}")
        return connected

    async def _tick(self):
        df = await self.feed.fetch_candles()
        if df is None or len(df) == 0:
            logger.warning("Empty candle response; skipping tick")
            return

        htf_df = None
        if self.config.get("strategy.confirmations.enable_htf", True):
            try:
                htf_df = await self.feed.fetch_htf_candles()
            except Exception:
                pass

        self._current_price = float(df["close"].iloc[-1])
        try:
            self._current_atr = float(
                calc_atr(df["high"], df["low"], df["close"],
                         self.config.get("strategy.atr_length", 14)).iloc[-2]
            )
        except Exception:
            self._current_atr = 0.0

        result = self.strategy.evaluate(df, htf_df)

        # Manage open positions first (exits can free the field for reversals)
        await self._check_exits()

        if result.signal_type:
            # Automation paused: keep managing exits on any open trade but do not
            # open new positions (manual override / "stop automation").
            if not self.config.get("strategy.auto_trade", True):
                return
            if self._is_duplicate(result):
                return
            if self.config.get("strategy.exit_on_reversal", True) and self._is_reversal(result):
                await self._close_opposite_positions(result.signal_type)
            self._last_setup_id = result.setup_id
            self._last_signal_bar = to_naive_utc(result.timestamp)
            self._last_signal = result
            await self._record_signal(result)
            await self._send_alert(result)
            logger.info(f"Signal: {result.signal_type} {result.primary_pattern} "
                        f"@ {result.entry_price} score={result.signal_score}")

    def _is_reversal(self, result: SignalResult) -> bool:
        """A fresh signal may flip an open position only if it is a real reversal
        pattern with enough conviction — prevents opposite-Marubozu whipsaws."""
        min_score = self.config.get("strategy.reversal_min_score", 65)
        return (result.primary_pattern in REVERSAL_PATTERNS
                and result.signal_score >= min_score)

    def _is_duplicate(self, result: SignalResult) -> bool:
        # Same setup already alerted
        if self._last_setup_id == result.setup_id:
            return True
        # Cooldown: suppress new signals within N bars of the last one
        cooldown = self.config.get("strategy.cooldown_bars", 0)
        if cooldown and self._last_signal_bar and result.timestamp is not None:
            tf = self.config.get("strategy.timeframe", "1m")
            secs = TIMEFRAME_SECONDS.get(tf, 60) * cooldown
            elapsed = (to_naive_utc(result.timestamp) - self._last_signal_bar).total_seconds()
            if 0 <= elapsed < secs:
                return True
        return False

    async def _record_signal(self, result: SignalResult):
        session = get_session(self.engine)
        try:
            bar_time = to_naive_utc(result.timestamp)
            signal = Signal(
                timestamp=bar_time,
                symbol=self.config.get("exchange.symbol", "BTC/USDT"),
                timeframe=self.config.get("strategy.timeframe", "1m"),
                direction=result.signal_type,
                signal_type="entry",
                entry_price=result.entry_price,
                executed_entry_price=result.executed_entry_price,
                entry_slippage=result.entry_slippage,
                stop_loss=result.stop_loss,
                take_profit_1=result.tp1,
                take_profit_2=result.tp2,
                take_profit_3=result.tp3,
                rsi_value=result.rsi_value,
                adx_value=result.adx_value,
                atr_value=result.atr_value,
                trend_ma=result.trend_ma_value,
                regime=result.regime,
                signal_score=result.signal_score,
                primary_pattern=result.primary_pattern,
                secondary_patterns=json.dumps(result.secondary_patterns),
                confirmation_type=result.confirmation_type,
                price_action_context=result.price_action_context,
                indicator_confirmations=json.dumps(result.indicator_confirmations),
                rejection_strength=result.rejection_strength,
                pattern_high=result.pattern_high,
                pattern_low=result.pattern_low,
                pattern_open=result.pattern_open,
                pattern_hi=result.pattern_candle_high,
                pattern_lo=result.pattern_candle_low,
                pattern_close=result.pattern_close,
                entry_time=bar_time,
                is_closed=False,
            )
            session.add(signal)
            session.commit()
        finally:
            session.close()

    async def _close_position(self, session, sig: Signal, exit_reason: str):
        self._alerted_stop.pop(sig.id, None)
        now = utcnow_naive()
        sig.exit_price = self._current_price
        sig.exit_time = now
        sig.exit_reason = exit_reason
        sig.is_closed = True
        # PnL is measured from the ACTUAL executed entry price (what the market
        # was really at when the trade fired), not the theoretical trigger level.
        base_entry = sig.executed_entry_price or sig.entry_price
        if sig.direction == "long":
            sig.pnl = self._current_price - base_entry
        else:
            sig.pnl = base_entry - self._current_price
        sig.pnl_pct = (sig.pnl / base_entry) * 100 if base_entry else 0
        sig.is_winner = sig.pnl > 0
        if sig.entry_time:
            sig.duration_minutes = int((now - sig.entry_time).total_seconds() / 60)
        session.commit()

        msg = self.notifier.format_exit_signal(
            sig.direction, sig.symbol, base_entry,
            self._current_price, sig.pnl, sig.pnl_pct, exit_reason,
        )
        await self.notifier.send_message(msg)
        logger.info(f"Exit: {sig.direction} {exit_reason} PnL={sig.pnl:.2f}")

    async def _check_exits(self):
        # Only one exit pass at a time — the signal loop and the fast monitor
        # both call this; the lock prevents double-closing the same position.
        async with self._exit_lock:
            price = self._current_price
            use_be = self.config.get("strategy.use_breakeven", True)
            be_r = self.config.get("strategy.breakeven_trigger_r", 1.0)
            use_trail = self.config.get("strategy.use_trailing", False)
            trail_mult = self.config.get("strategy.trailing_atr_mult", 1.5)
            max_min = self.config.get("strategy.max_trade_minutes", 0)

            session = get_session(self.engine)
            try:
                for sig in session.query(Signal).filter(Signal.is_closed == False).all():
                    # Break-even & trailing stop adjustments (persisted)
                    change = self._manage_stop(session, sig, price, use_be, be_r,
                                               use_trail, trail_mult)
                    if change:
                        await self._maybe_alert_stop_move(sig, change, price)

                    # Trailing-stop-only mode: TP1/2/3 are display-only and do
                    # not book profit — the position rides until the (trailing)
                    # stop is hit. Other exits (reversal, max-duration) still apply.
                    reason = self.strategy.check_exit(sig, price, honor_tp=not use_trail)
                    if not reason and max_min and sig.entry_time:
                        age = (utcnow_naive() - sig.entry_time).total_seconds() / 60
                        if age >= max_min:
                            reason = "time_exit"
                    if reason:
                        await self._close_position(session, sig, reason)
            finally:
                session.close()

    def _manage_stop(self, session, sig, price, use_be, be_r, use_trail, trail_mult):
        """Move stop to break-even after `be_r` R, then optionally trail by ATR.

        Returns {'reason', 'old', 'new'} describing the move (or None if the stop
        did not change) so the caller can alert on it.
        """
        if not sig.entry_price or not sig.take_profit_1:
            return None
        # Initial risk recovered from TP1 geometry (tp1 = entry ± tp1_r*risk)
        tp1_r = self.config.get("strategy.tp1_r", 1.0) or 1.0
        risk = abs(sig.take_profit_1 - sig.entry_price) / tp1_r
        if risk <= 0:
            return None
        old_sl = sig.stop_loss
        reason = None
        if sig.direction == "long":
            move = price - sig.entry_price
            if use_be and move >= be_r * risk and sig.stop_loss < sig.entry_price:
                sig.stop_loss = sig.entry_price
                reason = "break_even"
            if use_trail and self._current_atr > 0:
                trail = round(price - trail_mult * self._current_atr, 2)
                if trail > sig.stop_loss:
                    sig.stop_loss = trail
                    reason = "trailing"
        else:
            move = sig.entry_price - price
            if use_be and move >= be_r * risk and sig.stop_loss > sig.entry_price:
                sig.stop_loss = sig.entry_price
                reason = "break_even"
            if use_trail and self._current_atr > 0:
                trail = round(price + trail_mult * self._current_atr, 2)
                if trail < sig.stop_loss:
                    sig.stop_loss = trail
                    reason = "trailing"
        if reason:
            session.commit()
            return {"reason": reason, "old": old_sl, "new": sig.stop_loss}
        return None

    async def _maybe_alert_stop_move(self, sig, change, price):
        """Alert (Telegram) when the stop moves — throttled so a trailing stop
        doesn't spam on every tick."""
        if not self.config.get("strategy.alert_on_stop_move", True):
            return
        new_sl = change["new"]
        # Break-even is a one-time meaningful event → always alert it.
        # Trailing → alert only once the stop has advanced at least
        # trail_alert_min_atr × ATR beyond the last level we alerted.
        last = self._alerted_stop.get(sig.id)
        if change["reason"] == "trailing" and last is not None:
            min_move = self.config.get("strategy.trail_alert_min_atr", 0.5) * (self._current_atr or 0)
            if abs(new_sl - last) < max(min_move, 1e-9):
                return
        self._alerted_stop[sig.id] = new_sl
        msg = self.notifier.format_stop_update(
            sig.direction, sig.symbol, sig.primary_pattern,
            change["reason"], change["old"], new_sl, price, sig.entry_price,
        )
        await self.notifier.send_message(msg)

    async def close_open_positions(self, reason: str = "manual") -> int:
        """Manually close every open position at the current market price.
        Refreshes price first so the fill isn't stale. Returns count closed."""
        try:
            p = await self.feed.get_current_price()
            if p and p > 0:
                self._current_price = float(p)
        except Exception:
            pass
        n = 0
        async with self._exit_lock:
            session = get_session(self.engine)
            try:
                for sig in session.query(Signal).filter(Signal.is_closed == False).all():
                    await self._close_position(session, sig, reason)
                    n += 1
            finally:
                session.close()
        logger.info(f"Manual close: {n} position(s) closed ({reason})")
        return n

    async def _close_opposite_positions(self, new_direction: str):
        session = get_session(self.engine)
        try:
            for sig in session.query(Signal).filter(
                Signal.is_closed == False,
                Signal.direction != new_direction,
            ).all():
                await self._close_position(session, sig, "reversal")
        finally:
            session.close()

    async def _send_alert(self, result: SignalResult):
        symbol = self.config.get("exchange.symbol", "BTC/USDT")
        tf = self.config.get("strategy.timeframe", "1m")
        msg = self.notifier.format_pattern_entry_signal(
            result.signal_type, symbol, tf,
            result.primary_pattern, result.secondary_patterns,
            result.confirmation_type, result.signal_score,
            result.entry_price, result.stop_loss,
            result.tp1, result.tp2, result.tp3,
            result.indicator_confirmations,
            result.executed_entry_price, result.entry_slippage,
        )
        await self.notifier.send_message(msg)

    async def stop(self):
        self._running = False
        self._status = "stopped"
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None
        await self.feed.close()
        logger.info("Trading engine stopped")
