"""
SQLAlchemy models (SQLite) for the candlestick-pattern BTC futures platform.

The `Signal` table extends the reference schema with candlestick-specific
columns so every stored trade records exactly WHICH pattern caused it.
A tiny idempotent migration adds the new columns to pre-existing DBs.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Boolean, Text,
    create_engine, inspect, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DB_PATH = "database/trading_candle.db"


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    symbol = Column(String(20), default="BTCUSD")
    timeframe = Column(String(10))
    direction = Column(String(10))  # long / short
    signal_type = Column(String(20))  # entry / exit / stop_loss / take_profit / reversal

    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit_1 = Column(Float, nullable=True)
    take_profit_2 = Column(Float, nullable=True)
    take_profit_3 = Column(Float, nullable=True)

    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    is_winner = Column(Boolean, nullable=True)
    exit_reason = Column(String(50), nullable=True)
    entry_time = Column(DateTime, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)

    # ── Indicator context (optional confirmations) ──
    rsi_value = Column(Float, nullable=True)
    adx_value = Column(Float, nullable=True)
    atr_value = Column(Float, nullable=True)
    trend_ma = Column(Float, nullable=True)
    regime = Column(String(10), nullable=True)  # bullish / bearish / neutral
    signal_score = Column(Float, nullable=True)

    # ── Candlestick-specific columns (the "why") ──
    primary_pattern = Column(String(40), nullable=True)
    secondary_patterns = Column(Text, nullable=True)  # JSON list of names
    confirmation_type = Column(String(30), nullable=True)  # breakout / close / none
    price_action_context = Column(String(60), nullable=True)  # e.g. "support_rejection"
    indicator_confirmations = Column(Text, nullable=True)  # JSON dict {ema:true,...}
    rejection_strength = Column(Float, nullable=True)
    pattern_high = Column(Float, nullable=True)
    pattern_low = Column(Float, nullable=True)
    pattern_open = Column(Float, nullable=True)
    pattern_hi = Column(Float, nullable=True)   # pattern candle high (OHLC 'H')
    pattern_lo = Column(Float, nullable=True)   # pattern candle low  (OHLC 'L')
    pattern_close = Column(Float, nullable=True)

    notes = Column(Text, nullable=True)
    is_closed = Column(Boolean, default=False)
    telegram_sent = Column(Boolean, default=False)


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, index=True)
    symbol = Column(String(20))
    timeframe = Column(String(10))
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)


class AppLog(Base):
    __tablename__ = "app_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String(10))
    category = Column(String(30))
    message = Column(Text)


# Columns added after the first release; auto-added to existing SQLite files.
_MIGRATION_COLUMNS = {
    "primary_pattern": "VARCHAR(40)",
    "secondary_patterns": "TEXT",
    "confirmation_type": "VARCHAR(30)",
    "price_action_context": "VARCHAR(60)",
    "indicator_confirmations": "TEXT",
    "rejection_strength": "FLOAT",
    "pattern_high": "FLOAT",
    "pattern_low": "FLOAT",
    "pattern_open": "FLOAT",
    "pattern_hi": "FLOAT",
    "pattern_lo": "FLOAT",
    "pattern_close": "FLOAT",
}


def get_engine(db_path: str = DB_PATH):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def _migrate(engine):
    """Add any new Signal columns missing from an older DB (idempotent)."""
    insp = inspect(engine)
    if "signals" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("signals")}
    with engine.begin() as conn:
        for col, sqltype in _MIGRATION_COLUMNS.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE signals ADD COLUMN {col} {sqltype}"))


def init_db(db_path: str = DB_PATH):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return engine


def get_session(engine=None):
    if engine is None:
        engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()
