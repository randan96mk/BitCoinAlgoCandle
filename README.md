# BitCoinAlgoCandle — BTC Futures Candlestick Strategy

A self-hosted **Bitcoin *Futures*** trading-alert platform whose trades are driven
by **candlestick patterns + price action**. Every signal states *which* candlestick
pattern caused the entry — on the chart, in the history, and in Telegram.

Built on the proven infrastructure of
[BitCoinAlgo](https://github.com/randan96mk/BitCoinAlgo) (data feed, indicators,
FastAPI dashboard, SQLite, Telegram), with the Cardwell-RSI strategy replaced by a
candlestick-first engine. Indicators (EMA / RSI / ADX / HTF / Cardwell-regime /
volume) are kept as **optional, individually toggleable confirmations** — never the
primary trade reason.

> ⚠️ Alerts only — this does not place orders. Educational use, not financial advice.

## Quick start
```bash
./start.sh          # or: pip install -r requirements.txt && python run.py
```
Then open http://localhost:8000

See `CLAUDE.md` for full architecture and strategy documentation.
