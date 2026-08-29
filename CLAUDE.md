# CLAUDE.md — BTC Futures Candlestick Strategy

Reference for AI assistants and developers working on this repo. It documents
the architecture, the candlestick-first strategy, and the reuse relationship
with the parent project [BitCoinAlgo](https://github.com/randan96mk/BitCoinAlgo).

---

## 1. What this is

A local-first, single-process **Bitcoin *Futures*** trading-**alert** platform
(it does not place orders). Trades are driven by **candlestick patterns + price
action**; technical indicators act only as **optional, individually toggleable
confirmations**. Every signal records and displays **which candlestick pattern
caused the entry** — on the chart, in history, and in Telegram.

Data source: **Delta Exchange India — BTCUSD perpetual futures** (public API, no
key), with automatic fallback to Binance/Bybit. Default timeframe **1m**,
configurable. Signals fire on **closed candles only**.

## 2. Reuse relationship with BitCoinAlgo

Reused essentially unchanged: `exchange/data_feed.py`, `indicators/technical.py`
(RSI/SMA/ATR/ADX; **EMA added**), `config.py`, DB engine/session infra, the
FastAPI app shell + dashboard/chart, `telegram/notifier.py` transport,
`run.py`/`start.sh`.

Replaced: the Cardwell-RSI **entry decider** → a candlestick engine. Cardwell's
RSI-range regime is **kept** as one optional confirmation (`enable_cardwell`),
not deleted.

## 3. Architecture

```
run.py → backend/main.py (FastAPI + lifespan)
  └─ TradingEngine (services/trading_engine.py)   async loop, one tick per bar close
       ├─ DataFeed (exchange/data_feed.py)         Delta futures → Binance → Bybit
       ├─ CandlestickStrategy (strategy/candlestick_strategy.py)  ← the ONE engine
       │    ├─ candlestick_patterns.py   Candle anatomy + 18 detectors
       │    ├─ price_action.py           swings, S/R, breakout, structure
       │    ├─ scorer.py                 configurable weighted confluence score
       │    └─ indicators/technical.py   EMA/RSI/ADX/ATR confirmations
       ├─ database/models.py             SQLite; Signal carries pattern columns
       └─ telegram/notifier.py           pattern-led entry alerts
  └─ api/routes.py    status, signals, analytics, pattern-analytics, chart, config
```

## 4. Strategy hierarchy (candlestick-first)

1. **Candlestick pattern** — the primary trade reason.
2. **Pattern quality** — body/wick proportions, location.
3. **Price-action context** — support/resistance rejection, breakout, structure.
4. **Optional indicator confirmations** — EMA, RSI, ADX, Cardwell regime, volume.
5. **Entry confirmation** — pattern breakout (default), close, or none.
6. **Risk management** — SL from pattern structure + ATR buffer, R-multiple TPs.
7. **Trade management** — SL/TP1-3, break-even, trailing, reversal, max-duration.

### Patterns (18)
Bullish: Hammer, Bullish Pin Bar, Bullish Engulfing, Morning Star, Bullish
Harami, Inverted Hammer, Three White Soldiers.
Bearish: Shooting Star, Bearish Pin Bar, Bearish Engulfing, Evening Star,
Bearish Harami, Three Black Crows.
Other: Doji, Inside Bar, Marubozu, Strong Bullish/Bearish Rejection.

### Scoring (weights configurable in `strategy.weights`)
`pattern_strength 40 · pattern_quality 10 · price_action 10 · ema 10 · rsi 5 ·
adx 5 · cardwell 5 · volume 5 · breakout 10 · risk_reward 5`. Disabled
confirmations are removed from the denominator, so `min_signal_score` (default
70) stays meaningful whatever combination is enabled.

### Entry flow
Closed bar → detect all patterns → rank → **primary** (+ secondaries) → price
action → confirmations → weighted score → breakout confirmation within
`confirmation_window` → one LONG/SHORT signal. One signal per setup
(`setup_id = pattern|direction|pattern_bar_time`) + `cooldown_bars`.

### Exits
SL / TP1 / TP2 / TP3, break-even after `breakeven_trigger_r`, optional ATR
trailing, opposite-pattern reversal, optional max trade duration.

**Exit timing.** Entries are decided once per closed bar, but exits must react
to price the moment it is touched — so a dedicated **exit-monitor loop**
(`_exit_monitor_loop`) polls the live price every `exit_poll_seconds`
(default 5) and runs SL/TP checks independently of the bar-close signal loop.
An `asyncio.Lock` serialises the two so a position is never double-closed.
Without this, a stop could sit un-triggered for up to a full bar.

## 5. No look-ahead / no repaint

* The DataFrame's **last row is the still-forming bar**; the last **closed** bar
  is index `-2` (`t`). Every computation reads indices `<= t` only.
* A breakout confirmation fires on the bar that **first** breaks the pattern's
  extreme — so replaying history reproduces live signals bar-for-bar.
* The engine is **stateless** (rebuilt each tick), which is also restart-safe.
* Enforced by `tests/test_strategy_no_lookahead.py`.

## 6. Live / replay parity

`CandlestickStrategy.evaluate(df, htf_df)` is the single decision function used
live. Any offline replay must feed it the same closed-bar-only frames.

## 7. Database (`database/trading_candle.db`)

`Signal` extends the reference schema with: `primary_pattern`,
`secondary_patterns` (JSON), `confirmation_type`, `price_action_context`,
`indicator_confirmations` (JSON), `rejection_strength`, `pattern_high/low`,
`pattern_open/hi/lo/close`. A tiny idempotent migration adds them to old DBs.

## 8. Configuration (`config/default.json`, overridden by `config/user.json`)

Key blocks under `strategy`: `timeframe`, `min_signal_score`,
`confirmation_mode` (`breakout`/`close`/`none`), `confirmation_window`,
`cooldown_bars`, `candle.*` thresholds, `price_action.*`, `confirmations.*`
(each `enable_*` toggle + params), `weights.*`, `atr_length`,
`sl_buffer_atr_mult`, `tp1_r/tp2_r/tp3_r`, exit/break-even/trailing flags.
Changing an `exchange.*` or `strategy.*` key from Settings hot-reloads the engine.

## 9. UI / Telegram — "why did this trade enter?"

* Chart markers: `LONG 🕯 <Pattern> <score>`.
* Last-signal card, signal feed, active-trade panel and history table all show
  the pattern, secondaries, confirmation and price-action context.
* Analytics page: **Pattern Performance** and **Pattern + Confirmation** tables
  (trades, win rate, net/avg PnL, avg R) → which patterns actually work on BTC
  futures. Endpoint: `/api/pattern-analytics`.
* Telegram entry alert leads with the pattern, confirmation, score and active
  confirmations.

## 10. Tests

`tests/test_patterns.py` (known OHLC → expected pattern) and
`tests/test_strategy_no_lookahead.py` (signal names a pattern; replay parity;
closed-bar-only). Run: `python tests/test_patterns.py` /
`python tests/test_strategy_no_lookahead.py`.

## 11. Run

`./start.sh` or `pip install -r requirements.txt && python run.py` →
http://localhost:8000.
