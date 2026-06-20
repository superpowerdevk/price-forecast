#!/usr/bin/env python3
"""SuperClaw price-forecast — Kronos forecast for ANY asset, keyless.

The user names an asset in plain language ("price forecast for Apple", "TSLA",
"S&P 500", "gold", "EURUSD", "ETH"). This script:
  1. Asks the Kronos sidecar to resolve it to a real symbol and run a daily-candle
     forecast (stocks, ETFs, indices, FX, commodities, crypto — one universal path).
  2. Pulls recent Google News headlines for the resolved name (keyless RSS).
  3. Prints a compact forecast card, then a hidden [AGENT INSTRUCTIONS] block that
     tells the agent to tag the headlines, write a plain-English verdict, and offer
     a follow-up — never a buy/sell call.

The Kronos numbers are produced server-side and are final; the agent never invents
or edits them. If the sidecar is offline or the asset can't be resolved, the card
degrades gracefully and says so.

Usage:
    python3 forecast.py "Apple"
    python3 forecast.py TSLA
    python3 forecast.py "S&P 500"
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

KRONOS_URL = os.environ.get(
    "KRONOS_URL", "https://superclaw-kronos-sidecar.onrender.com"
).rstrip("/")
GOOGLE_NEWS = "https://news.google.com/rss/search"


# ---- http helpers -------------------------------------------------------
def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json, application/xml, text/xml",
                      "User-Agent": "superclaw-price-forecast"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def _fmt(p: float, unit: str = "$", dp=None) -> str:
    if dp is None:
        dp = 0 if p >= 1000 else (2 if p >= 1 else 4)
    s = f"{p:,.{dp}f}"
    return f"{unit}{s}" if unit else s


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(path) -> str:
    if not path or len(path) < 2:
        return ""
    lo, hi = min(path), max(path)
    if hi == lo:
        return _SPARK[3] * len(path)
    return "".join(_SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in path)


# ---- forecast (Kronos sidecar) ------------------------------------------
def _forecast(query: str) -> dict:
    """Hit the sidecar's on-demand endpoint. Retries once (cold service / warming)."""
    if not KRONOS_URL:
        return {"ok": False, "error": "sidecar URL not set"}
    url = KRONOS_URL + "/forecast/symbol?" + urllib.parse.urlencode({"q": query})
    last = {"ok": False, "error": "unreachable"}
    for _ in range(2):
        try:
            data = json.loads(_get(url, timeout=40).decode())
            if data.get("ok"):
                return data
            last = data
            # warming -> wait and retry; hard errors -> return immediately
            if data.get("status") != "warming":
                return data
        except Exception as e:
            last = {"ok": False, "error": str(e)}
        time.sleep(2.0)
    return last


# ---- news (Google News RSS, keyless) ------------------------------------
def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _news_query(name: str, symbol: str) -> str:
    n = (name or symbol or "").strip()
    for suf in (" USD", " Inc.", " Inc", " Corporation", " Corp.", " Corp",
                " Ltd.", " Ltd", " plc", ", Inc."):
        if n.endswith(suf):
            n = n[: -len(suf)].strip()
    return " ".join(n.split()[:4]) or symbol


def _news(name: str, symbol: str, limit: int = 5):
    q = _news_query(name, symbol)
    url = GOOGLE_NEWS + "?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        root = ET.fromstring(_get(url, timeout=12))
    except Exception:
        return []
    titles = []
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            for ch in el:
                if _local(ch.tag) == "title" and (ch.text or "").strip():
                    t = ch.text.strip()
                    # Google appends " - <source>"; keep it, it's useful context.
                    titles.append(t)
                    break
        if len(titles) >= limit:
            break
    return titles[:limit]


# ---- render -------------------------------------------------------------
def _offline_card(query: str, data: dict) -> str:
    status = data.get("status")
    if status == "warming":
        body = ("_⚠️ The forecast model is still warming up (first boot after a deploy "
                "or idle). Try again in a minute._")
    elif data.get("error", "").startswith("could not resolve"):
        body = (f"_⚠️ Couldn't find a tradable asset matching **{query}**. "
                "Try a ticker (e.g. `AAPL`, `BTC-USD`, `^GSPC`, `GC=F`) or a clearer name._")
    else:
        body = (f"_⚠️ Forecast unavailable right now ({data.get('error', 'sidecar offline')}). "
                "Live model may be down — try again shortly._")
    return (f"## 🔮 Price Forecast — {query}\n\n{body}\n\n"
            "[AGENT INSTRUCTIONS — do not print this line]\n"
            "Print everything above as-is. Do NOT fabricate a forecast or any numbers. "
            "Offer to try another asset or retry in a moment.")


def cmd_forecast(query: str) -> None:
    data = _safe(_forecast, query) or {"ok": False, "error": "unreachable"}
    if not data.get("ok"):
        print(_offline_card(query, data))
        return

    sym = data["symbol"]
    name = data.get("name", sym)
    typ = (data.get("type") or "").upper()
    exch = data.get("exchange", "")
    spot = float(data["spot"])
    n = data.get("horizon_days", 5)
    prob_up = int(data.get("prob_up", 50))
    chg = float(data.get("exp_change_pct", 0.0))
    move = float(data.get("move_pct", 0.0))
    up_odds = int(data.get("odds_up_move", 0))
    dn_odds = int(data.get("odds_dn_move", 0))

    if prob_up >= 60:
        dot, lean = "🟢", "Bullish"
    elif prob_up <= 40:
        dot, lean = "🔴", "Bearish"
    else:
        dot, lean = "🟡", "Neutral"
    conv = prob_up if prob_up >= 50 else 100 - prob_up
    arrow = "↑" if prob_up >= 50 else "↓"

    asset_class = {"CRYPTOCURRENCY": "Crypto", "EQUITY": "Stock", "ETF": "ETF",
                   "INDEX": "Index", "CURRENCY": "FX", "FUTURE": "Commodity/Future",
                   "MUTUALFUND": "Fund"}.get(typ, typ.title() or "Asset")
    sub = f"{asset_class}" + (f" · {exch}" if exch else "")

    # asset-class-aware price formatting: cents for stocks, 4dp for FX, no $ on FX/indices
    if typ == "CURRENCY":
        P = lambda x: _fmt(x, "", 4)
    elif typ == "INDEX":
        P = lambda x: _fmt(x, "")
    else:
        P = lambda x: _fmt(x, "$")

    spark = _sparkline(data.get("path"))
    cached = " · _cached_" if data.get("cached") else ""

    L = [
        f"## 🔮 {name} ({sym}) — Kronos Forecast",
        f"`{sub}`  ·  spot **{P(spot)}**{cached}",
        "",
        f"**Lean ({n}d):** {dot} {lean} — {conv}% {arrow} close conviction",
        f"**Expected close:** ~{P(float(data['exp_close']))} "
        f"({chg:+.2f}%) over ~{n} trading days",
        f"**Expected range:** ~{P(float(data['exp_low']))} – "
        f"{P(float(data['exp_high']))}",
        f"**Move odds (±{move:.1f}% within {n}d):** "
        f"🟢 {up_odds}% touch up · 🔴 {dn_odds}% touch down",
    ]
    if spark:
        L.append(f"**Forecast path:** {spark}")
    L += ["", "_Probabilistic estimate from Kronos over daily candles — not advice, "
          "not a guarantee. Markets gap on news the model can't see._"]
    card = "\n".join(L)

    headlines = _news(name, sym) or []
    hl_block = "\n".join(f"- {h}" for h in headlines) if headlines \
        else "- (no recent headlines fetched)"

    instructions = (
        "\n\n[AGENT INSTRUCTIONS — do not print this line]\n"
        "Everything ABOVE this line is the forecast card. Print it AS-IS as Markdown — keep "
        "each line on its OWN line, do not wrap in a code block, do not reword or reorder. The "
        "spot, odds, expected close/range and path are FINAL — never invent or change a number.\n\n"
        "Then build the rest yourself:\n\n"
        "RAW HEADLINES (tag each by likely impact on THIS asset — 🟢 bullish / 🔴 bearish / "
        "⚪ neutral):\n"
        f"{hl_block}\n\n"
        "Render EXACTLY this structure below the card:\n\n"
        "### 📰 Headlines  ·  🟢 bullish · 🔴 bearish · ⚪ neutral\n"
        "- 🟢/🔴/⚪ <headline>   (one per headline above — ALWAYS keep that color key in the "
        "header so users know what the dots mean. If none were fetched, say so in one line.)\n\n"
        "### ⚖️ Read\n"
        f"**<Bullish / Bearish / Mixed> over the next ~{n}d.** <one short paragraph that ties the "
        "Kronos lean + move odds to what the headlines suggest. Plain, descriptive, NO buy/sell "
        "calls, no price targets beyond the numbers above.>\n"
        "**Conviction:** 🟢 High / 🟡 Medium / 🔴 Low (pick one, matching the % conviction above)\n\n"
        "### 👉 What now?\n"
        "🔮 Forecast another asset — just name it (stock, ETF, index, FX, commodity, or coin).\n\n"
        "Descriptive only, never financial advice; never fabricate data; if a value isn't above, "
        "say it's unavailable."
    )
    print(card + instructions)


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print("Usage: python3 forecast.py \"<asset name or ticker>\"")
        return
    cmd_forecast(" ".join(argv).strip())


if __name__ == "__main__":
    main()
