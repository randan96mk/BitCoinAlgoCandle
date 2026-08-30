"""
Telegram alert sender.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from backend.config import Config

logger = logging.getLogger("telegram")

IST = timezone(timedelta(hours=5, minutes=30))


def _timestamp_line() -> str:
    """Current time as 'YYYY-MM-DD HH:MM UTC (hh:MM AM/PM IST)'."""
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    ist_12h = now_ist.strftime('%I:%M %p').lstrip('0')
    return f"{now_utc.strftime('%Y-%m-%d %H:%M')} UTC ({ist_12h} IST)"


class TelegramNotifier:
    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        self.bot_token = cfg.get("telegram.bot_token", "")
        self.chat_id = cfg.get("telegram.chat_id", "")
        self.enabled = cfg.get("telegram.enabled", False) and bool(self.bot_token) and bool(self.chat_id)

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled, skipping message")
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info("Telegram message sent")
                    return True
                logger.error(f"Telegram error: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def send_test(self) -> dict:
        """Send a test message using the saved credentials, ignoring the enabled
        flag, and return a clear {ok, detail} so credential errors are visible.
        """
        if not self.bot_token or not self.chat_id:
            return {"ok": False, "detail": "Bot token and chat ID are both required."}
        text = ("✅ <b>BTC Futures Candlestick</b>\n"
                "Telegram is connected — you will receive signal alerts here.")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return {"ok": True, "detail": "Test message sent — check Telegram."}
                # Surface Telegram's own description (bad token, chat not found, …)
                detail = resp.text
                try:
                    detail = resp.json().get("description", detail)
                except Exception:
                    pass
                return {"ok": False, "detail": f"Telegram {resp.status_code}: {detail}"}
        except Exception as e:
            return {"ok": False, "detail": f"Request failed: {type(e).__name__}: {e}"}

    def format_entry_signal(self, direction: str, symbol: str, timeframe: str,
                            entry: float, sl: float, tp1: float, tp2: float,
                            tp3: float, score: float, regime: str,
                            rsi: float, adx: float) -> str:
        emoji = "\U0001f680" if direction == "long" else "\U0001f534"
        dir_label = "BUY" if direction == "long" else "SELL"
        risk = abs(entry - sl)
        reward = abs(tp3 - entry)
        rr = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"

        return (
            f"{emoji} <b>{dir_label} SIGNAL</b>\n\n"
            f"<b>Pair:</b> {symbol}\n"
            f"<b>Timeframe:</b> {timeframe}\n"
            f"<b>Regime:</b> {regime.upper()}\n\n"
            f"<b>Entry:</b> {entry:.2f}\n"
            f"<b>Stop Loss:</b> {sl:.2f}\n"
            f"<b>TP1:</b> {tp1:.2f}\n"
            f"<b>TP2:</b> {tp2:.2f}\n"
            f"<b>TP3:</b> {tp3:.2f}\n\n"
            f"<b>Risk/Reward:</b> {rr}\n"
            f"<b>Signal Score:</b> {score:.0f}%\n"
            f"<b>RSI:</b> {rsi:.1f} | <b>ADX:</b> {adx:.1f}\n\n"
            f"<b>Time:</b> {_timestamp_line()}"
        )

    def format_pattern_entry_signal(self, direction: str, symbol: str, timeframe: str,
                                    primary_pattern: str, secondary_patterns: list,
                                    confirmation: str, score: float,
                                    entry: float, sl: float, tp1: float, tp2: float,
                                    tp3: float, confirms: dict,
                                    executed: float = 0.0, slippage: float = 0.0) -> str:
        """Candlestick-led entry alert — leads with the pattern (the trade reason)."""
        emoji = "\U0001f7e2" if direction == "long" else "\U0001f534"
        dir_label = "LONG" if direction == "long" else "SHORT"
        risk = abs(entry - sl)
        reward = abs(tp3 - entry)
        rr = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"
        also = ""
        if secondary_patterns:
            also = f"\n<b>Also detected:</b> {', '.join(secondary_patterns)}"
        active = [k.upper() for k, v in (confirms or {}).items() if v]
        conf_line = (" ".join(active) + " ✓") if active else "pattern only"

        return (
            f"{emoji} <b>{dir_label}</b> — BTC FUTURES SIGNAL\n\n"
            f"\U0001f56f <b>Pattern:</b> {primary_pattern}{also}\n"
            f"<b>Confirmation:</b> {confirmation}\n"
            f"<b>Score:</b> {score:.0f}/100\n"
            f"<b>Confluence:</b> {conf_line}\n\n"
            f"<b>Pair:</b> {symbol} | <b>TF:</b> {timeframe}\n"
            f"<b>Entry (trigger):</b> {entry:.2f}\n"
            + (f"<b>Executed:</b> {executed:.2f}  (slip {slippage:+.2f})\n" if executed else "")
            + f"<b>Stop Loss:</b> {sl:.2f}\n"
            f"<b>TP1:</b> {tp1:.2f}\n"
            f"<b>TP2:</b> {tp2:.2f}\n"
            f"<b>TP3:</b> {tp3:.2f}\n\n"
            f"<b>Risk/Reward:</b> {rr}\n"
            f"<b>Time:</b> {_timestamp_line()}"
        )

    def format_stop_update(self, direction: str, symbol: str, pattern: str,
                           reason: str, old_sl: float, new_sl: float,
                           price: float, entry: float) -> str:
        """Alert when the trailing / break-even stop moves."""
        label = "Break-even stop" if reason == "break_even" else "Trailing stop"
        emoji = "\U0001f512"  # 🔒
        locked = new_sl - entry if direction == "long" else entry - new_sl
        lock_line = (f"\n<b>Locked:</b> {locked:+.2f} (stop now "
                     f"{'above' if locked >= 0 else 'below'} entry)")
        return (
            f"{emoji} <b>{label} moved</b> — {symbol}\n\n"
            f"\U0001f56f <b>{pattern or 'Trade'}</b> ({direction.upper()})\n"
            f"<b>SL:</b> {old_sl:.2f} → <b>{new_sl:.2f}</b>{lock_line}\n"
            f"<b>Price:</b> {price:.2f}\n\n"
            f"<b>Time:</b> {_timestamp_line()}"
        )

    def format_exit_signal(self, direction: str, symbol: str,
                           entry: float, exit_price: float,
                           pnl: float, pnl_pct: float,
                           reason: str) -> str:
        emoji = "✅" if pnl >= 0 else "❌"
        reason_labels = {
            "stop_loss": "Stop Loss",
            "take_profit_1": "Take Profit 1",
            "take_profit_2": "Take Profit 2",
            "take_profit_3": "Take Profit 3",
            "reversal": "Trend Reversal",
        }
        reason_label = reason_labels.get(reason, reason.replace("_", " ").title())
        return (
            f"{emoji} <b>Trade Closed</b>\n\n"
            f"<b>Pair:</b> {symbol}\n"
            f"<b>Direction:</b> {direction.upper()}\n"
            f"<b>Entry:</b> {entry:.2f}\n"
            f"<b>Exit:</b> {exit_price:.2f}\n"
            f"<b>PnL:</b> {pnl:.2f} USDT\n"
            f"<b>Profit:</b> {pnl_pct:.2f}%\n"
            f"<b>Reason:</b> {reason_label}\n\n"
            f"<b>Time:</b> {_timestamp_line()}"
        )
