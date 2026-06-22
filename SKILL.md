---
name: price-forecast
description: Kronos-powered price forecast for ANY asset — stocks, ETFs, indices, FX, commodities, and crypto. Use this whenever the user asks for a price forecast, outlook, prediction, or "where is X going" for a named asset (e.g. "price forecast for Apple", "what's TSLA going to do", "forecast gold", "outlook on the S&P 500", "BTC next week"). It resolves the asset, runs the Kronos foundation model over recent daily candles to produce a probabilistic forecast (directional lean, expected close + range, odds of a meaningful move, a forecast path), pulls recent headlines for that asset, and renders a forecast card with a plain-English read. Forecast-only — it never holds funds, places trades, or holds keys.
---

# price-forecast — Kronos forecast for any asset

The user names an asset in plain language; you return a Kronos-backed forecast card plus a headline read. Coverage spans **US stocks/ETFs/indices, FX, commodities, crypto, Chinese A-shares (Shanghai & Shenzhen), and Hong Kong stocks** — including assets named in **Chinese** (e.g. 贵州茅台), by pinyin, or by numeric code (`600519`, `0700.HK`).

> Descriptive, never advice. The forecast is a model estimate over daily candles — present it as a probability, never a guarantee, never a buy/sell call.

## FIRST ACTION (on invoke — do this immediately)

1. **Take the user's asset EXACTLY as they wrote it** — an English name ("Apple", "gold"), a ticker (`AAPL`, `BTC-USD`, `^GSPC`), a **Chinese name** (`贵州茅台`), pinyin (`maotai`), an **A-share code** (`600519`), or a **HK code** (`0700.HK`). Do **not** translate it, romanize it, or guess a ticker yourself — the script resolves it server-side.
2. **Run the forecast exactly once:** `python3 forecast.py "<the user's exact input>"` (in this skill's directory). Run it **one time**. Do **not** re-run it with alternate ticker formats, translations, or guesses.
3. **Render the card via the `render_ui` tool.** The script's stdout begins with an `[AGENT INSTRUCTIONS]` block followed by an HTML card between `<<<RENDER_UI_HTML>>>` and `<<<END_HTML>>>` markers. Call `render_ui` once with `surfaceId: "forecast"`, `mode: "replace"`, a `title`, and `html` set to **everything between those markers, verbatim** — do not alter, re-indent, truncate, or wrap it. The card is a self-contained interactive forecast terminal (price history → forecast cone, four gauges, projected-levels tiles; candlesticks on desktop, line on mobile; tap/hover a gauge or candle for detail). Do **not** print the HTML in chat — it renders on the card surface. Then write the **Headlines + Read + What-now** sections as your chat message, following the structure the instructions specify. (If the sidecar returns no chart data, there's no HTML block — the script prints a Markdown text card instead with its own instructions; print that verbatim. And if you ever lack a `render_ui` tool, the stdout includes a PLAIN FALLBACK card to post in chat instead.)

## CRITICAL — never improvise around the script

These rules override everything else. The script is the single source of truth.

- **Run it once, print what it returns.** If the script returns an error or offline card, print THAT card verbatim. Do **not** re-run with different inputs, and do **not** write your own explanation of why it failed.
- **Never claim an asset or market "isn't supported" or "isn't covered."** This skill covers Chinese A-shares, Hong Kong, US, FX, commodities, and crypto. Do **not** tell the user to use ADRs (BABA/JD/NIO), indices, or any workaround — that advice is wrong and forbidden.
- **"warming" just means the model is spinning up.** If the card says the model is warming/unavailable, tell the user plainly: "the forecast model is spinning up — ask again in a few seconds." Nothing is broken. Do not editorialize or invent a cause.
- **Never fabricate** a number, a ticker, a forecast, or a reason. If it's not in the script's output, it doesn't go in your reply.

## How the forecast works (so you can explain it)

- The asset is resolved to a real symbol, then ~2 years of **daily** candles are pulled and fed to **Kronos** (a foundation model trained on candlestick data across many exchanges and asset classes).
- Kronos is sampled ~20 times to generate independent forward paths over the horizon (default ~5 trading days). From those paths the sidecar computes **close conviction** (share of paths closing above spot), **expected close/high/low**, and **move odds** (share of paths touching a ±~1σ level scaled to the asset's own volatility).
- Daily candles mean the same logic works for 24/7 crypto and market-hours stocks alike. Horizon is in **trading days**, so ~5d ≈ one week.

## Hard rules

- **Forecast-only.** Never holds funds, never places trades, never holds keys. It only reads data and forecasts.
- **Not investment advice.** A model estimate; no outcome guaranteed; the user bears all risk. No buy/sell calls, no price targets beyond the numbers the script printed.
- If the user names something ambiguous, the card shows the **resolved name + symbol** — if it picked the wrong one, point that out and suggest a more specific code/ticker. Don't silently substitute.

## Dependencies & notes

- **Kronos sidecar (Modal GPU):** forecasts come from the GPU-hosted Kronos sidecar. The script targets it by default; override with `KRONOS_URL` (env) if self-hosting. Endpoint: `GET /forecast/symbol?q=<asset>`.
- **Cold starts:** if the sidecar has been idle it may take ~30–90s to spin up; the script waits this out automatically and only reports "warming" if it's still not ready. Repeat forecasts for the same symbol are cached (~10 min) and return instantly.
- **Data sources (all server-side):** FMP (US stocks/ETF/index/FX/commodity), EastMoney (Chinese A-share + HK, incl. Chinese-name/pinyin/code resolution), Hyperliquid + CoinGecko (crypto), SEC directory (US name→ticker), Google News RSS (headlines), Kronos (forecast).
