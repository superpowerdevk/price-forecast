---
name: price-forecast
description: Kronos-powered price forecast for ANY asset — stocks, ETFs, indices, FX, commodities, and crypto. Use this whenever the user asks for a price forecast, outlook, prediction, or "where is X going" for a named asset (e.g. "price forecast for Apple", "what's TSLA going to do", "forecast gold", "outlook on the S&P 500", "BTC next week"). It resolves the asset, runs the Kronos foundation model over recent daily candles to produce a probabilistic forecast (directional lean, expected close + range, odds of a meaningful move, a forecast path), pulls recent headlines for that asset, and renders a forecast card with a plain-English read. Forecast-only — it never holds funds, places trades, or holds keys.
---

# price-forecast — Kronos forecast for any asset

The user names an asset in plain language; you return a Kronos-backed forecast card plus a headline read. This works across asset classes through a multi-source resolver: **stocks, ETFs, indices, FX pairs, commodities/futures, and crypto.**

> Descriptive, never advice. The forecast is a model estimate over daily candles — present it as a probability, never a guarantee, never a buy/sell call.

## FIRST ACTION (on invoke — do this immediately)

1. **Identify the asset the user named.** Pass it through verbatim — a name ("Apple", "gold", "S&P 500") or a ticker (`AAPL`, `BTC-USD`, `^GSPC`, `GC=F`, `EURUSD=X`). Don't pre-guess the ticker; the script resolves it.
2. **Run the forecast:** `python3 forecast.py "<what the user named>"` (in this skill's directory). It prints, keyless:
   - a **forecast card** — resolved name + symbol + asset class, live spot, directional lean with conviction %, expected close (+%) and range over the horizon, **move odds** (probability of touching ±~1σ within the horizon), and a forecast-path sparkline,
   - raw headlines for that asset, plus the exact format to render the rest.
3. **Show the card, then build the rest.** Print everything above the `[AGENT INSTRUCTIONS]` line **AS-IS as Markdown** — each line on its own line, no code block, no rewording. The dot + arrow are the **directional lean** (🟢 bullish ↑ / 🔴 bearish ↓ / 🟡 neutral), the `%` is Kronos's **close conviction** over the horizon, `~price` is its **expected close**, and the **move odds** are the chance of touching the ±X% level within the window. Then follow the embedded instructions to add **### 📰 Headlines** (tag each 🟢/🔴/⚪ and ALWAYS keep the color key in the header), **### ⚖️ Read** (bold Bullish/Bearish/Mixed lead + one short paragraph grounded in the lean, move odds, and headlines + a Conviction badge), and **### 👉 What now?**. Never invent or alter a number from the card.

## How the forecast works (so you can explain it)

- The asset is resolved to a real symbol, then ~2 years of **daily** candles are pulled and fed to **Kronos** (a foundation model trained on candlestick data across 45+ exchanges and many asset classes).
- Kronos is sampled ~20 times to generate independent forward paths over the horizon (default ~5 trading days). From those paths the sidecar computes: **close conviction** (share of paths closing above spot), **expected close/high/low**, and **move odds** (share of paths whose high/low touches a ±~1σ level scaled to the asset's own volatility).
- Daily candles are used so the same logic works identically for 24/7 crypto and market-hours stocks. Horizon is in **trading days**, so a stock's ~5d ≈ one week.

## Hard rules

- **Forecast-only.** This skill never holds funds, never places trades, never holds keys. It only reads data and forecasts.
- **Not investment advice.** The forecast is a model estimate; no outcome is guaranteed; the user bears all risk. Keep everything descriptive — no buy/sell calls, no price targets beyond the numbers the script printed.
- **Never fabricate.** Use only the numbers the script prints. If a value is n/a — sidecar warming, asset unresolved, no candles — say so plainly and offer to retry or try another asset. Don't invent a forecast.
- If the user names something ambiguous (e.g. "gold" could be the metal, a miner, or an ETF), the card shows the **resolved name + symbol** — if it picked the wrong one, tell the user and suggest a more specific ticker.

## Dependencies & notes

- **Kronos sidecar:** forecasts come from the shared Kronos sidecar (same service the trader skill uses). The script defaults to the live URL; override with `KRONOS_URL` (env) if self-hosting. The new on-demand endpoint is `GET /forecast/symbol?q=<asset>`.
- **First call after an idle/deploy may be slow** (model warm-up + first inference on CPU); the script retries once and reports "warming" if it's not ready. Repeat forecasts for the same symbol are cached server-side (~10 min) and return instantly.
- All data is keyless: Stooq (stocks/ETF/index/FX/commodity candles), Hyperliquid + CoinGecko (crypto), SEC directory (company name -> ticker), Google News RSS (headlines), Kronos (forecast). No API keys anywhere.
