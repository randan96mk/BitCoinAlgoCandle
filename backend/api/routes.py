"""
API routes — status, signals, analytics, chart data, config, and the
candlestick pattern-performance analytics that answer "which patterns actually
work on BTC futures?".
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import desc

from backend.config import Config
from backend.database.models import Signal, get_session, get_engine

router = APIRouter(prefix="/api")

PATTERN_EMOJI = "\U0001f56f"  # 🕯


def _get_db():
    return get_session(get_engine())


@router.get("/status")
async def get_status():
    from backend.main import engine_instance
    cfg_symbol = Config().get("exchange.symbol")
    exchange = Config().get("exchange.name")
    market_symbol = cfg_symbol.replace("/", "")
    if engine_instance and getattr(engine_instance, "feed", None):
        feed = engine_instance.feed
        if getattr(feed, "exchange_name", None):
            exchange = feed.exchange_name
        if getattr(feed, "_api_config", None):
            try:
                market_symbol = feed._api_config["symbol_fmt"](cfg_symbol)
            except Exception:
                pass
    return {
        "status": engine_instance.status if engine_instance else "stopped",
        "exchange": exchange,
        "symbol": cfg_symbol,
        "market_symbol": market_symbol,
        "timeframe": Config().get("strategy.timeframe"),
        "refresh_interval": Config().get("server.refresh_interval", 5),
        "auto_trade": Config().get("strategy.auto_trade", True),
        "current_price": engine_instance.current_price if engine_instance else 0,
        "last_signal": _signal_to_dict(engine_instance.last_signal) if engine_instance and engine_instance.last_signal else None,
    }


@router.get("/signals")
async def get_signals(
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    direction: Optional[str] = Query(None),
    pattern: Optional[str] = Query(None),
    is_closed: Optional[bool] = Query(None),
):
    session = _get_db()
    try:
        q = session.query(Signal).order_by(desc(Signal.timestamp))
        if direction:
            q = q.filter(Signal.direction == direction)
        if pattern:
            q = q.filter(Signal.primary_pattern == pattern)
        if is_closed is not None:
            q = q.filter(Signal.is_closed == is_closed)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {"total": total, "signals": [_signal_row(s) for s in rows]}
    finally:
        session.close()


@router.get("/analytics")
async def get_analytics():
    session = _get_db()
    try:
        closed = session.query(Signal).filter(Signal.is_closed == True).all()
        if not closed:
            return _empty_analytics()
        total = len(closed)
        winners = [s for s in closed if s.is_winner]
        profits = [s.pnl for s in closed if s.pnl and s.pnl > 0]
        losses = [s.pnl for s in closed if s.pnl and s.pnl < 0]
        total_profit = sum(profits) if profits else 0
        total_loss = abs(sum(losses)) if losses else 0
        return {
            "total_signals": total,
            "win_rate": len(winners) / total * 100 if total else 0,
            "loss_rate": (total - len(winners)) / total * 100 if total else 0,
            "total_profit": round(total_profit, 2),
            "total_loss": round(total_loss, 2),
            "net_profit": round(total_profit - total_loss, 2),
            "avg_profit": round(sum(profits) / len(profits), 2) if profits else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown": round(_calc_drawdown(closed), 2),
            "profit_factor": round(total_profit / total_loss, 2) if total_loss > 0 else 0,
            "avg_duration_min": round(sum(s.duration_minutes or 0 for s in closed) / total, 1),
            "long_trades": len([s for s in closed if s.direction == "long"]),
            "short_trades": len([s for s in closed if s.direction == "short"]),
            "long_win_rate": _win_rate([s for s in closed if s.direction == "long"]),
            "short_win_rate": _win_rate([s for s in closed if s.direction == "short"]),
            "equity_curve": _equity_curve(closed),
            "daily_pnl": _daily_pnl(closed),
        }
    finally:
        session.close()


@router.get("/pattern-analytics")
async def pattern_analytics():
    """Per-pattern and pattern+confirmation performance breakdown."""
    session = _get_db()
    try:
        closed = session.query(Signal).filter(Signal.is_closed == True).all()
        by_pattern = {}
        by_combo = {}
        for s in closed:
            name = s.primary_pattern or "Unknown"
            by_pattern.setdefault(name, []).append(s)
            confs = _parse_json(s.indicator_confirmations, {})
            active = "+".join(sorted(k for k, v in confs.items() if v)) or "none"
            by_combo.setdefault(f"{name} [{active}]", []).append(s)
        return {
            "patterns": [_pattern_stats(name, rows) for name, rows in
                         sorted(by_pattern.items(), key=lambda kv: -_net(kv[1]))],
            "combinations": [_pattern_stats(name, rows) for name, rows in
                             sorted(by_combo.items(), key=lambda kv: -_net(kv[1]))
                             if len(rows) >= 2],
        }
    finally:
        session.close()


@router.get("/chart/candles")
async def get_chart_candles(timeframe: Optional[str] = Query(None), limit: int = Query(200)):
    from backend.main import engine_instance
    empty = {"candles": [], "ema_fast": [], "ema_slow": [], "rsi": [], "volumes": [],
             "markers": [], "trade_levels": []}
    if not engine_instance or not engine_instance.feed.is_connected:
        return empty
    tf = timeframe or Config().get("strategy.timeframe", "1m")
    try:
        df = await engine_instance.feed.fetch_candles(tf, limit)
    except Exception:
        return empty

    from backend.indicators.technical import ema as calc_ema, rsi as calc_rsi
    close = df["close"]
    cfg = Config()
    ema_fast = calc_ema(close, cfg.get("strategy.confirmations.ema_fast", 21))
    ema_slow = calc_ema(close, cfg.get("strategy.confirmations.ema_slow", 50))
    rsi_vals = calc_rsi(close, cfg.get("strategy.confirmations.rsi_length", 14))

    candles, ef_data, es_data, rsi_data, vol_data = [], [], [], [], []
    for i, row in df.iterrows():
        t = int(row["timestamp"].timestamp())
        candles.append({"time": t, "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"]})
        vol_data.append({"time": t, "value": row["volume"],
                         "color": "rgba(38,166,154,0.4)" if row["close"] >= row["open"]
                         else "rgba(239,83,80,0.4)"})
        if not _isnan(ema_fast.iloc[i]):
            ef_data.append({"time": t, "value": round(ema_fast.iloc[i], 2)})
        if not _isnan(ema_slow.iloc[i]):
            es_data.append({"time": t, "value": round(ema_slow.iloc[i], 2)})
        if not _isnan(rsi_vals.iloc[i]):
            rsi_data.append({"time": t, "value": round(rsi_vals.iloc[i], 2)})

    candle_times = [c["time"] for c in candles]
    tf_seconds = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                  "30m": 1800, "1h": 3600}.get(tf, 60)

    def snap(epoch: int) -> Optional[int]:
        if not candle_times or epoch < candle_times[0] or epoch > candle_times[-1] + tf_seconds:
            return None
        return min(candle_times, key=lambda ct: abs(ct - epoch))

    session = _get_db()
    try:
        signals = session.query(Signal).order_by(desc(Signal.timestamp)).limit(50).all()
        markers = []
        for s in signals:
            if s.entry_time:
                t = snap(_epoch_utc(s.entry_time))
                if t is not None:
                    label = _marker_label(s)
                    if s.direction == "long":
                        markers.append({"time": t, "position": "belowBar", "color": "#2196f3",
                                        "shape": "arrowUp", "text": label})
                    else:
                        markers.append({"time": t, "position": "aboveBar", "color": "#ff9800",
                                        "shape": "arrowDown", "text": label})
            if s.exit_time and s.is_closed:
                t = snap(_epoch_utc(s.exit_time))
                if t is not None:
                    color = "#26a69a" if s.is_winner else "#ef5350"
                    markers.append({"time": t,
                                    "position": "belowBar" if s.direction == "short" else "aboveBar",
                                    "color": color, "shape": "circle",
                                    "text": f"EXIT {s.exit_price:.0f}"})
        trade_levels = []
        for s in session.query(Signal).filter(Signal.is_closed == False).all():
            trade_levels.append({
                "entry": s.entry_price, "sl": s.stop_loss,
                "tp1": s.take_profit_1, "tp2": s.take_profit_2, "tp3": s.take_profit_3,
                "direction": s.direction, "pattern": s.primary_pattern,
            })
    finally:
        session.close()

    return {
        "candles": candles, "ema_fast": ef_data, "ema_slow": es_data,
        "rsi": rsi_data, "volumes": vol_data,
        "markers": sorted(markers, key=lambda x: x["time"]),
        "trade_levels": trade_levels,
    }


@router.get("/config")
async def get_config():
    return Config().all()


@router.post("/config")
async def update_config(data: dict):
    from backend.main import engine_instance
    from backend.telegram.notifier import TelegramNotifier
    cfg = Config()
    needs_reload = False
    telegram_changed = False
    for key, val in data.items():
        old = cfg.get(key)
        cfg.set(key, val)
        if old != val and (key.startswith("exchange.") or key.startswith("strategy.")):
            needs_reload = True
        if old != val and key.startswith("telegram."):
            telegram_changed = True

    reloaded = False
    if needs_reload and engine_instance:
        try:
            reloaded = await engine_instance.reload()  # rebuilds notifier too
        except Exception as e:
            import logging
            logging.getLogger("app").error(f"Engine reload failed: {e}")
    # Telegram credentials change alone: rebuild just the notifier live so alerts
    # start firing without a restart (a full reload isn't needed).
    if telegram_changed and not reloaded and engine_instance:
        engine_instance.notifier = TelegramNotifier(cfg)

    return {"status": "ok", "reloaded": reloaded, "needs_reload": needs_reload}


@router.post("/config/reset")
async def reset_config():
    """Clear the user overlay so everything returns to default.json values."""
    from backend.main import engine_instance
    from backend.config import USER_CONFIG
    if USER_CONFIG.exists():
        USER_CONFIG.unlink()
    Config().reload()
    reloaded = False
    if engine_instance:
        try:
            reloaded = await engine_instance.reload()
        except Exception as e:
            import logging
            logging.getLogger("app").error(f"Reload after reset failed: {e}")
    return {"status": "ok", "reloaded": reloaded}


@router.post("/trade/close")
async def close_trade():
    """Manually close all open positions at market (override / stop automation)."""
    from backend.main import engine_instance
    if not engine_instance:
        return {"closed": 0, "error": "engine not running"}
    try:
        n = await engine_instance.close_open_positions("manual")
        return {"closed": n}
    except Exception as e:
        return {"closed": 0, "error": str(e)}


@router.post("/trade/auto")
async def set_auto_trade(data: dict):
    """Pause/resume automated entries (exits on open trades keep running)."""
    on = bool(data.get("enabled", True))
    Config().set("strategy.auto_trade", on)
    return {"auto_trade": on}


@router.post("/telegram/test")
async def telegram_test():
    """Send a test message with the currently-saved Telegram credentials and
    return a clear result so mis-set token / chat-id can be diagnosed."""
    from backend.telegram.notifier import TelegramNotifier
    return await TelegramNotifier(Config()).send_test()


# ── helpers ───────────────────────────────────────────────────────────────────
def _marker_label(s: Signal) -> str:
    dirlabel = "LONG" if s.direction == "long" else "SHORT"
    pat = s.primary_pattern or ""
    score = f" {s.signal_score:.0f}" if s.signal_score else ""
    return f"{dirlabel} {PATTERN_EMOJI}{pat}{score}".strip()


def _parse_json(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _net(rows) -> float:
    return sum(s.pnl or 0 for s in rows)


def _pattern_stats(name: str, rows: list) -> dict:
    n = len(rows)
    wins = [s for s in rows if s.is_winner]
    net = _net(rows)
    rs = [_r_multiple(s) for s in rows if _r_multiple(s) is not None]
    return {
        "pattern": name,
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0,
        "net_pnl": round(net, 2),
        "avg_pnl": round(net / n, 2) if n else 0,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else 0,
        "long": len([s for s in rows if s.direction == "long"]),
        "short": len([s for s in rows if s.direction == "short"]),
    }


def _r_multiple(s: Signal):
    """Realised R = pnl / initial risk (entry vs pattern-based stop)."""
    if not s.entry_price or s.take_profit_1 is None:
        return None
    tp1_r = Config().get("strategy.tp1_r", 1.0) or 1.0
    risk = abs(s.take_profit_1 - s.entry_price) / tp1_r
    if risk <= 0:
        return None
    return (s.pnl or 0) / risk


def _isnan(v) -> bool:
    try:
        import math
        return math.isnan(v)
    except (TypeError, ValueError):
        return True


def _epoch_utc(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _signal_to_dict(sig) -> dict:
    if not sig:
        return {}
    return {
        "type": sig.signal_type,
        "entry": sig.entry_price,
        "sl": sig.stop_loss,
        "tp1": sig.tp1,
        "tp2": sig.tp2,
        "tp3": sig.tp3,
        "score": sig.signal_score,
        "regime": sig.regime,
        "pattern": sig.primary_pattern,
        "secondary": sig.secondary_patterns,
        "confirmation": sig.confirmation_type,
        "price_action": sig.price_action_context,
        "confirms": sig.indicator_confirmations,
    }


def _signal_row(s: Signal) -> dict:
    return {
        "id": s.id,
        "timestamp": _iso_utc(s.timestamp),
        "entry_time": _iso_utc(s.entry_time),
        "exit_time": _iso_utc(s.exit_time),
        "direction": s.direction,
        "signal_type": s.signal_type,
        "entry_price": s.entry_price,
        "exit_price": s.exit_price,
        "stop_loss": s.stop_loss,
        "tp1": s.take_profit_1,
        "tp2": s.take_profit_2,
        "tp3": s.take_profit_3,
        "pnl": s.pnl,
        "pnl_pct": s.pnl_pct,
        "is_winner": s.is_winner,
        "exit_reason": s.exit_reason,
        "duration_min": s.duration_minutes,
        "regime": s.regime,
        "score": s.signal_score,
        "is_closed": s.is_closed,
        "primary_pattern": s.primary_pattern,
        "secondary_patterns": _parse_json(s.secondary_patterns, []),
        "confirmation_type": s.confirmation_type,
        "price_action_context": s.price_action_context,
        "indicator_confirmations": _parse_json(s.indicator_confirmations, {}),
        "pattern_high": s.pattern_high,
        "pattern_low": s.pattern_low,
    }


def _calc_drawdown(signals) -> float:
    equity = peak = max_dd = 0
    for s in sorted(signals, key=lambda x: x.exit_time or x.timestamp):
        equity += s.pnl or 0
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _win_rate(signals) -> float:
    if not signals:
        return 0
    return len([s for s in signals if s.is_winner]) / len(signals) * 100


def _equity_curve(signals) -> list:
    equity = 0
    curve = []
    for s in sorted(signals, key=lambda x: x.exit_time or x.timestamp):
        equity += s.pnl or 0
        curve.append({"time": (s.exit_time or s.timestamp).isoformat(), "equity": round(equity, 2)})
    return curve


def _daily_pnl(signals) -> list:
    days = {}
    for s in signals:
        key = (s.exit_time or s.timestamp).strftime("%Y-%m-%d")
        days[key] = days.get(key, 0) + (s.pnl or 0)
    return [{"day": k, "pnl": round(v, 2)} for k, v in sorted(days.items())[-30:]]


def _empty_analytics() -> dict:
    return {
        "total_signals": 0, "win_rate": 0, "loss_rate": 0, "total_profit": 0,
        "total_loss": 0, "net_profit": 0, "avg_profit": 0, "avg_loss": 0,
        "max_drawdown": 0, "profit_factor": 0, "avg_duration_min": 0,
        "long_trades": 0, "short_trades": 0, "long_win_rate": 0, "short_win_rate": 0,
        "equity_curve": [], "daily_pnl": [],
    }
