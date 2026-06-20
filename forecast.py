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
    "KRONOS_URL", "https://devansh-86031--superclaw-kronos-serve.modal.run"
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
    """Hit the sidecar's on-demand endpoint, patiently waiting out a GPU cold start.

    A scaled-to-zero sidecar can take ~30-90s to boot the container + load the model.
    During that window it either returns {"status":"warming"} or the socket hangs.
    We poll for up to ~100s before giving up so the first request after idle still
    returns a real forecast instead of a spurious failure.
    """
    if not KRONOS_URL:
        return {"ok": False, "error": "sidecar URL not set"}
    url = KRONOS_URL + "/forecast/symbol?" + urllib.parse.urlencode({"q": query})
    deadline = time.time() + 100.0
    last = {"ok": False, "status": "warming", "error": "unreachable"}
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            data = json.loads(_get(url, timeout=25).decode())
            if data.get("ok"):
                return data
            last = data
            # Hard errors (could-not-resolve, etc.) -> return immediately.
            # Only "warming" is worth waiting on.
            if data.get("status") != "warming":
                return data
        except Exception as e:
            # Timeout/hang during container boot looks like an exception; keep waiting.
            last = {"ok": False, "status": "warming", "error": str(e)}
        time.sleep(min(8.0, 3.0 + attempt))
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


# ---- SVG forecast terminal (self-contained, renders inline in SuperClaw) ----
def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _ccy_fmt(ccy: str, v: float) -> str:
    if abs(v) >= 100:
        s = f"{v:,.0f}"
    elif abs(v) >= 1:
        s = f"{v:,.2f}"
    else:
        s = f"{v:.4f}"
    return f"{ccy}{s}"


def _svg_card(d: dict):
    """Dark, self-contained SVG forecast terminal. Returns an SVG string, or None
    if the payload lacks the chart data (old sidecar) so the caller can fall back."""
    hc = d.get("hist_c") or []
    b = d.get("bands") or {}
    if len(hc) < 10 or not b.get("p50"):
        return None
    ccy = d.get("ccy", "$")
    up = d.get("direction") == "up"
    acc = "#3fb950" if up else "#f85149"
    acc_dim = "rgba(63,185,80,0.16)" if up else "rgba(248,81,73,0.16)"
    acc_mid = "rgba(63,185,80,0.30)" if up else "rgba(248,81,73,0.30)"
    spot = float(d["spot"])
    nh, nf = len(hc), len(b["p50"])
    nx = nh + nf
    allv = hc + b["p10"] + b["p90"] + [spot]
    ymin, ymax = min(allv), max(allv)
    pad = (ymax - ymin) * 0.08 or 1.0
    ymin -= pad
    ymax += pad
    X0, XW, Y0, YH = 56, 588, 92, 196

    def px(i):
        return X0 + (i / (nx - 1)) * XW

    def py(v):
        return Y0 + (ymax - v) / (ymax - ymin) * YH

    hpts = " ".join(f"{px(i):.1f},{py(hc[i]):.1f}" for i in range(nh))
    jx, jy = px(nh - 1), py(spot)

    def cone(lo, hi):
        up_pts = [(jx, jy)] + [(px(nh + i), py(hi[i])) for i in range(nf)]
        dn_pts = [(px(nh + i), py(lo[i])) for i in range(nf - 1, -1, -1)] + [(jx, jy)]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in up_pts + dn_pts)

    outer = cone(b["p10"], b["p90"])
    inner = cone(b["p25"], b["p75"])
    mpts = f"{jx:.1f},{jy:.1f} " + " ".join(
        f"{px(nh + i):.1f},{py(b['p50'][i]):.1f}" for i in range(nf))

    grid = ""
    for k in range(4):
        gv = ymin + (ymax - ymin) * (k + 0.5) / 4
        gy = py(gv)
        grid += (f'<line x1="{X0}" y1="{gy:.1f}" x2="{X0 + XW}" y2="{gy:.1f}" '
                 f'stroke="#1c2128" stroke-width="0.5"/>'
                 f'<text x="{X0 - 8}" y="{gy + 3:.1f}" fill="#6e7681" font-size="11" '
                 f'text-anchor="end">{ccy}{gv:,.0f}</text>')
    nowx, spoty = px(nh - 1), py(spot)
    conv = d["prob_up"] if up else 100 - d["prob_up"]
    arr = "▲" if up else "▼"

    tiles = [
        ("Conviction", f"{conv}% {arr}", acc),
        ("Exp. close", _ccy_fmt(ccy, d["exp_close"]), "#e6edf3"),
        (f"{nf}d range", f"{_ccy_fmt(ccy, d['exp_low'])}–{d['exp_high']:,.0f}", "#e6edf3"),
        ("Move odds", f"▲{d['odds_up_move']}% ▼{d['odds_dn_move']}%", "#c9d1d9"),
    ]
    tw, tg, tx0, ty = 140, 10, 56, 322
    tiles_svg = ""
    for i, (lab, val, col) in enumerate(tiles):
        x = tx0 + i * (tw + tg)
        tiles_svg += (
            f'<rect x="{x}" y="{ty}" width="{tw}" height="56" rx="8" fill="#0f141a" '
            f'stroke="#1f2630" stroke-width="0.5"/>'
            f'<text x="{x + 12}" y="{ty + 22}" fill="#8b949e" font-size="11">{_esc(lab)}</text>'
            f'<text x="{x + 12}" y="{ty + 42}" fill="{col}" font-size="15" '
            f'font-weight="600">{_esc(val)}</text>')

    name = _esc(d["name"])
    sym = _esc(d["symbol"])
    typ = (d.get("type", "") or "").replace("_", "-").title()
    sub = _esc(typ + (f" · {d['exchange']}" if d.get("exchange") else ""))
    badge = "Bullish" if up else "Bearish"
    H = 398
    return (
        f'<svg width="100%" viewBox="0 0 700 {H}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" font-family="ui-sans-serif,system-ui">'
        f'<title>{name} Kronos forecast</title>'
        f'<desc>{nf}-day Kronos price forecast for {name}, {badge}, '
        f'spot {_ccy_fmt(ccy, spot)}, expected close {_ccy_fmt(ccy, d["exp_close"])}.</desc>'
        f'<rect x="0.5" y="0.5" width="699" height="{H - 1}" rx="14" fill="#0d1117" stroke="#1f2630"/>'
        f'<text x="28" y="38" fill="#e6edf3" font-size="20" font-weight="600">{name}</text>'
        f'<text x="28" y="60" fill="#8b949e" font-size="12">{sym} · {sub}</text>'
        f'<text x="672" y="36" fill="#e6edf3" font-size="22" font-weight="600" '
        f'text-anchor="end">{_ccy_fmt(ccy, spot)}</text>'
        f'<rect x="588" y="46" width="84" height="22" rx="11" fill="{acc_dim}"/>'
        f'<text x="630" y="61" fill="{acc}" font-size="12" font-weight="600" '
        f'text-anchor="middle">{arr} {badge}</text>'
        f'{grid}'
        f'<polygon points="{outer}" fill="{acc_dim}"/>'
        f'<polygon points="{inner}" fill="{acc_mid}"/>'
        f'<polyline points="{hpts}" fill="none" stroke="#58a6ff" stroke-width="1.6"/>'
        f'<line x1="{X0}" y1="{spoty:.1f}" x2="{X0 + XW}" y2="{spoty:.1f}" '
        f'stroke="#6e7681" stroke-width="0.7" stroke-dasharray="3 3"/>'
        f'<line x1="{nowx:.1f}" y1="{Y0}" x2="{nowx:.1f}" y2="{Y0 + YH}" '
        f'stroke="#30363d" stroke-width="0.7" stroke-dasharray="2 3"/>'
        f'<polyline points="{mpts}" fill="none" stroke="{acc}" stroke-width="2" '
        f'stroke-dasharray="5 3"/>'
        f'<text x="{nowx - 6:.0f}" y="{Y0 + YH + 16}" fill="#6e7681" font-size="11" '
        f'text-anchor="end">now</text>'
        f'<text x="{X0 + XW}" y="{Y0 + YH + 16}" fill="#6e7681" font-size="11" '
        f'text-anchor="end">+{nf}d forecast</text>'
        f'{tiles_svg}'
        f'<text x="28" y="388" fill="#6e7681" font-size="11">Kronos · {nf}-day '
        f'probabilistic forecast · not advice. Shaded band = 10–90% range.</text>'
        f'</svg>')


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
    text_card = "\n".join(L)

    # Prefer the visual SVG terminal; fall back to the text card if the sidecar
    # didn't return chart data (older build) or there aren't enough candles.
    svg = _safe(_svg_card, data)
    card = svg if svg else text_card
    is_svg = bool(svg)

    headlines = _news(name, sym) or []
    hl_block = "\n".join(f"- {h}" for h in headlines) if headlines \
        else "- (no recent headlines fetched)"

    card_directive = (
        ("Everything ABOVE this line is the forecast card, as an inline SVG. Output it AS-IS, "
         "exactly once, as a raw inline HTML/SVG block — do NOT wrap it in a code fence, do NOT "
         "escape it, do NOT describe or redraw it, and do NOT add a separate text version of the "
         "numbers. It is the chart.")
        if is_svg else
        ("Everything ABOVE this line is the forecast card. Print it AS-IS as Markdown — keep each "
         "line on its OWN line, do not wrap in a code block, do not reword or reorder. The spot, "
         "odds, expected close/range and path are FINAL — never invent or change a number.")
    )

    instructions = (
        "\n\n[AGENT INSTRUCTIONS — do not print this line]\n"
        f"{card_directive}\n\n"
        "Then build the rest yourself, BELOW the card:\n\n"
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
