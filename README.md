# BitCoinAlgoCandle — BTC Futures Candlestick Strategy

A **local-first, self-hosted Bitcoin *Futures* trading-alert platform** whose trades
are driven by **candlestick patterns + price action**. Every signal states *which*
candlestick pattern caused the entry — on the chart, in the history table, and in
Telegram — so you can always answer **"why did this trade happen?"**

Built on the proven infrastructure of
[BitCoinAlgo](https://github.com/randan96mk/BitCoinAlgo) (exchange data feed,
indicators, FastAPI dashboard, SQLite, Telegram), with the Cardwell-RSI strategy
replaced by a **candlestick-first engine**. Technical indicators
(EMA / RSI / ADX / higher-timeframe / Cardwell-regime / volume) are kept as
**optional, individually toggleable confirmations** — never the primary trade reason.

> ⚠️ **Alerts only.** This does **not** place orders or execute trades. It is for
> signal generation, analysis, and education. Nothing here is financial advice.

---

## Highlights

- 🕯 **Candlestick-first strategy** — 18 patterns with real candle-anatomy analysis
  (body/wick ratios, multi-candle relationships), not naive detection.
- 🎯 **Every trade names its pattern** — chart markers read `LONG 🕯 Bullish
  Engulfing · 86`; the same reasoning shows in the feed, history, and Telegram.
- 🧭 **Quality filters that raise win probability** — pullback-in-trend entry bias,
  anti-chase filter, noise-pattern exclusion, gated reversals, cooldown.
- 🛡 **Smart trade management** — pattern-structure stops, R-multiple targets,
  break-even, **trailing-stop-only mode** (default), reversal & max-duration exits.
- ⚡ **Near-real-time exits** — a dedicated monitor polls price every few seconds, so
  stops react in seconds instead of once per bar.
- 🎛 **Manual overrides** — Close-Trade-at-market button and an Auto-trade pause.
- 🔔 **Telegram alerts** — pattern-led entry alerts + trailing/break-even stop-move
  alerts (throttled).
- 📊 **Pattern-performance analytics** — discover which patterns/combos actually work
  on BTC futures.
- 🔁 **No look-ahead / no repaint** — closed-bar only; replay reproduces live signals.

---

## Getting started

**Requirements:** Python 3.10+ (3.11+ recommended), macOS or Linux.

```bash
git clone https://github.com/randan96mk/BitCoinAlgoCandle.git
cd BitCoinAlgoCandle
./start.sh              # sets up a venv, installs deps, launches the server
# or manually:
pip install -r requirements.txt && python run.py
```

Then open **http://localhost:8000**.

Market data comes from the **public Delta Exchange India API** (BTCUSD perpetual
futures) with automatic fallback to **Binance** and **Bybit** — no API keys needed.

---

## How the strategy works

The decision hierarchy is deliberately **candlestick-first**:

1. **Candlestick pattern** — the primary trade reason.
2. **Pattern quality** — body/wick proportions, location.
3. **Price-action context** — support/resistance rejection, breakout, structure.
4. **Optional indicator confirmations** — EMA, RSI, ADX, Cardwell regime, volume.
5. **Entry confirmation** — pattern breakout (default), close, or none.
6. **Risk management** — stop from pattern structure + ATR buffer, R-multiple targets.
7. **Trade management** — SL / TP1-3, break-even, trailing, reversal, max duration.

### Patterns (18)

**Bullish:** Hammer · Bullish Pin Bar · Bullish Engulfing · Morning Star ·
Bullish Harami · Inverted Hammer · Three White Soldiers
**Bearish:** Shooting Star · Bearish Pin Bar · Bearish Engulfing · Evening Star ·
Bearish Harami · Three Black Crows
**Other:** Doji · Inside Bar · Marubozu · Strong Bullish/Bearish Rejection

It trades **both directions** — LONG confirms on a break above the pattern high,
SHORT on a break below the pattern low.

### Confluence score

A weighted 0–100 score (weights configurable):

```
pattern_strength 40 · pattern_quality 10 · price_action 10 · ema 10 · rsi 5 ·
adx 5 · cardwell 5 · volume 5 · breakout 10 · risk_reward 5
```

Disabled confirmations drop out of the denominator, so `min_signal_score`
(default 72) stays meaningful whatever combination you enable. Each confirmation
(`enable_ema`, `enable_rsi`, `enable_adx`, `enable_htf`, `enable_cardwell`,
`enable_volume`) is toggleable in **Settings**.

### Win-probability filters

- **Entry bias** (`pullback` default) — buy dips in an uptrend / sell rallies in a
  downtrend: the pattern must return to value (at support/resistance or the fast
  EMA) with trend alignment, instead of chasing breakouts into open air. Also
  `breakout` and `any` modes.
- **Anti-chase** (`max_entry_ext_atr`) — rejects entries stretched too far beyond
  the slow EMA.
- **Noise-pattern exclusion** (`excluded_trigger_patterns`) — Doji / Inside Bar
  can't trigger a trade on their own (they still ride as context).
- **Strict Marubozu trigger** — Marubozu can trigger, but only as a genuine
  conviction thrust: clean body ≥ 0.92, volume ≥ 1.5× the 20-bar average,
  trend-aligned, and score ≥ 80. Configurable via `strategy.marubozu_trigger`.
- **Gated reversals** — an open trade is only flipped by a real reversal-class
  pattern with score ≥ `reversal_min_score` (no opposite-Marubozu whipsaws).
- **Cooldown** (`cooldown_bars`) and one-signal-per-setup de-duplication.

---

## Trade management & exits

Stops come from the **pattern structure** (below the pattern low for longs, above
the pattern high for shorts) plus an ATR buffer, capped by `max_loss_points`.
Targets are R-multiples of that risk (`tp1_r`/`tp2_r`/`tp3_r`).

### Trailing-stop-only mode (default ON)

With `use_trailing` on, **TP1/2/3 are still calculated and displayed everywhere but
do not book profit** — the position rides until the trailing / break-even stop is
hit (reversal and max-duration exits still apply). Break-even snaps the stop to
entry after `breakeven_trigger_r` (default 0.75R); the trailing stop then ratchets
by `trailing_atr_mult` × ATR and never moves backward.

Turn `use_trailing` **off** for classic fixed-target booking — the whole position
closes at the first TP reached (normally TP1).

When the stop moves, the chart relabels the SL line **TRAIL SL** (amber) and a
**Telegram alert** fires — throttled by `trail_alert_min_atr` so a trailing stop
doesn't spam on every tick (break-even is always alerted once).

### Manual controls (dashboard)

- **Close Trade (Market)** — closes all open positions now at market.
- **Auto-trade ON / PAUSED** — pause new entries while exits on open trades keep
  running (your override to "stop the automation" on a live trade).

---

## Dashboard, alerts & analytics

- **Terminal** — candlestick chart (TradingView Lightweight Charts) with EMA lines,
  volume, an RSI sub-chart, live entry/SL/TP levels, and pattern-labelled markers.
  The signal feed shows the pattern, score, and (for closed trades) the
  entry→exit time and duration.
- **History** — every signal with pattern, confirmation, PnL, R, and duration.
- **Analytics → Pattern Performance** — per-pattern and pattern+confirmation
  win-rate / net & avg PnL / avg R, so you can prune the losers. (`/api/pattern-analytics`)
- **Settings** — timeframe, symbol, exchange, score threshold, confirmation mode,
  every confirmation toggle, Marubozu gate, risk/exit parameters, Telegram
  credentials with a **Send-test** button, and **Reset to defaults**.
- **Telegram** — pattern-led entry alerts and stop-move alerts. Enable in Settings.

---

## No look-ahead / no repaint

- The DataFrame's last row is the still-forming bar; the last **closed** bar is
  index `-2`. Every computation reads indices `≤ t` only.
- A breakout confirmation fires on the bar that **first** breaks the pattern's
  extreme, so replaying history reproduces live signals bar-for-bar.
- The strategy engine is **stateless** (rebuilt each tick) and therefore
  restart-safe. Enforced by `tests/test_strategy_no_lookahead.py`.
- The same `CandlestickStrategy.evaluate()` is the single decision function used
  live and for any offline replay.

---

## Configuration

All parameters live in `config/default.json`. Your changes are saved as a **sparse
overlay** in `config/user.json` (only the keys you actually change), so future
default updates flow through on restart. Editing an `exchange.*` or `strategy.*`
value from Settings hot-reloads the engine. **Reset to defaults** clears the overlay.

---

## Project structure

```
BitCoinAlgoCandle/
├── run.py / start.sh                 # entry point + launcher
├── config/default.json               # all parameters (user.json overlays it)
├── CLAUDE.md                         # full architecture & strategy reference
└── backend/
    ├── main.py                       # FastAPI app + pages
    ├── config.py                     # sparse-overlay config loader
    ├── strategy/
    │   ├── candlestick_patterns.py   # candle anatomy + 18 detectors
    │   ├── candlestick_strategy.py   # the ONE decision engine
    │   ├── price_action.py           # swings, S/R, breakout, structure
    │   └── scorer.py                 # configurable confluence score
    ├── indicators/technical.py       # EMA/RSI/SMA/ATR/ADX (Wilder RMA)
    ├── services/trading_engine.py    # loop, exits, trailing, manual controls
    ├── exchange/data_feed.py         # Delta futures → Binance → Bybit
    ├── database/models.py            # SQLite; Signal carries pattern columns
    ├── telegram/notifier.py          # pattern-led + stop-move alerts
    ├── api/routes.py                 # status, signals, analytics, chart, config, trade
    └── templates/                    # dashboard, history, analytics, settings
```

## Tests

```bash
python tests/test_patterns.py                # known OHLC → expected pattern
python tests/test_strategy_no_lookahead.py   # replay parity, filters, exits
```

---

## Disclaimer

Provided for **educational and informational purposes only**. It does not execute
trades and is not financial advice. Trading cryptocurrency carries significant risk.
Use at your own risk.
