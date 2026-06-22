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

import base64
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


def _sentiment_score(headlines):
    """Lightweight keyword sentiment from the real headlines. 0-100, 50 = neutral."""
    if not headlines:
        return 50
    bull = ("surge", "rise", "rises", "gain", "gains", "beat", "beats", "high", "jump", "jumps",
            "rally", "upgrade", "growth", "record", "soar", "soars", "climb", "climbs", "boost",
            "strong", "wins", "outperform", "raise", "raises", "hike")
    bear = ("fall", "falls", "drop", "drops", "decline", "declines", "miss", "misses", "loss",
            "losses", "cut", "cuts", "plunge", "plunges", "slump", "downgrade", "weak", "warn",
            "warns", "fear", "selloff", "sell-off", "sinks", "tumble", "tumbles", "crash",
            "lawsuit", "probe", "retreat", "retreats")
    b = s = 0
    for h in headlines:
        hl = h.lower()
        b += sum(1 for w in bull if w in hl)
        s += sum(1 for w in bear if w in hl)
    if b + s == 0:
        return 50
    return int(max(8, min(92, round(50 + 42 * (b - s) / (b + s)))))


def _composite_confidence(scores, sentiment):
    """Transparent blend of the four gauges, capped under 95 (never certainty)."""
    d = scores.get("direction", 50); m = scores.get("momentum", 50); r = scores.get("risk", 50)
    conf = 0.45 * d + 0.25 * m + 0.30 * sentiment - 0.25 * (r - 50)
    return int(max(0, min(95, round(conf))))


def _gauge_svg(cx, cy, score, label, tag, kind):
    import math
    if kind == "risk":
        col = "#3fb950" if score < 40 else ("#d29922" if score < 65 else "#f85149")
    else:
        col = "#f85149" if score < 40 else ("#d29922" if score < 60 else "#3fb950")
    def pol(a):
        rad = math.radians(a); return cx + 26 * math.cos(rad), cy + 26 * math.sin(rad)
    def ap(a0, a1):
        x0, y0 = pol(a0); x1, y1 = pol(a1); lg = 1 if (a1 - a0) % 360 > 180 else 0
        return f"M {x0:.0f} {y0:.0f} A 26 26 0 {lg} 1 {x1:.0f} {y1:.0f}"
    return (f'<path d="{ap(135, 135 + 270)}" fill="none" stroke="#21262d" stroke-width="5.5" stroke-linecap="round"/>'
            f'<path d="{ap(135, 135 + 270 * score / 100)}" fill="none" stroke="{col}" stroke-width="5.5" stroke-linecap="round"/>'
            f'<text x="{cx}" y="{cy + 5:.0f}" fill="#e6edf3" font-size="16" font-weight="600" text-anchor="middle">{score}</text>'
            f'<text x="{cx}" y="{cy + 44:.0f}" fill="#8b949e" font-size="11.5" text-anchor="middle">{label}</text>'
            f'<text x="{cx}" y="{cy + 60:.0f}" fill="{col}" font-size="11" font-weight="600" text-anchor="middle">{tag}</text>')


def _svg_card(d: dict):
    """Mobile-first forecast report (self-contained dark SVG). Returns SVG, or None
    if the payload lacks chart data / scores (older sidecar) so caller falls back."""
    ho = d.get("hist_o") or []; hh = d.get("hist_h") or []
    hl = d.get("hist_l") or []; hc = d.get("hist_c") or []
    b = d.get("bands") or {}; sc = d.get("scores") or {}
    if len(hc) < 12 or not b.get("p50") or "direction" not in sc:
        return None
    n = min(30, len(hc))
    ho, hh, hl, hc = ho[-n:], hh[-n:], hl[-n:], hc[-n:]
    ccy = d.get("ccy", "$")
    spot = float(d["spot"]); up = d["direction"] == "up"
    acc = "#3fb950" if up else "#f85149"
    acc_dim = "rgba(63,185,80,0.18)" if up else "rgba(248,81,73,0.18)"
    sig = d.get("signal", "Neutral")
    sig_up = "buy" in sig.lower(); sig_dn = "sell" in sig.lower()
    sig_col = "#3fb950" if sig_up else ("#f85149" if sig_dn else "#d29922")
    sig_bg = ("rgba(63,185,80,0.14)" if sig_up else
              ("rgba(248,81,73,0.14)" if sig_dn else "rgba(210,153,34,0.14)"))
    conf = int(d.get("confidence", 50)); sent = int(d.get("sentiment", 50))
    direction = int(sc["direction"]); momentum = int(sc["momentum"]); risk = int(sc["risk"])
    p10, p50, p90 = b["p10"], b["p50"], b["p90"]; nf = len(p50)
    X0, XW, Y0, YH = 34, 340, 142, 150
    allv = hh + hl + p10 + p90
    ymin, ymax = min(allv), max(allv); pad = (ymax - ymin) * 0.08 or 1.0; ymin -= pad; ymax += pad
    HXW = XW * 0.60; FXW = XW * 0.40
    hx = lambda i: X0 + (i / (n - 1)) * HXW
    fx = lambda i: X0 + HXW + (i / nf) * FXW
    py = lambda v: Y0 + (ymax - v) / (ymax - ymin) * YH
    base_y = Y0 + YH
    hist_pts = " ".join(f"{hx(i):.0f},{py(hc[i]):.0f}" for i in range(n))
    cs = (f'<polygon points="{hist_pts} {hx(n-1):.0f},{base_y:.0f} {hx(0):.0f},{base_y:.0f}" fill="rgba(110,118,129,0.10)"/>'
          f'<polyline points="{hist_pts}" fill="none" stroke="#768390" stroke-width="1.6"/>')
    jx, jy = hx(n - 1), py(spot)
    cone = (f"{jx:.0f},{jy:.0f} " + " ".join(f"{fx(i+1):.0f},{py(p90[i]):.0f}" for i in range(nf))
            + " " + " ".join(f"{fx(nf-i):.0f},{py(p10[nf-1-i]):.0f}" for i in range(nf)))
    med = f"{jx:.0f},{jy:.0f} " + " ".join(f"{fx(i+1):.0f},{py(p50[i]):.0f}" for i in range(nf))
    grid = ""
    for kk in range(3):
        gv = ymin + (ymax - ymin) * (kk + 0.5) / 3; gy = py(gv)
        grid += (f'<line x1="{X0}" y1="{gy:.0f}" x2="{X0+XW}" y2="{gy:.0f}" stroke="#1c2128" stroke-width="0.5"/>'
                 f'<text x="{X0-6}" y="{gy+3:.0f}" fill="#6e7681" font-size="11" text-anchor="end">{ccy}{gv:,.0f}</text>')
    def rtag(s): return "Low" if s < 40 else ("Elevated" if s < 65 else "High")
    def dtag(s): return "Weak" if s < 40 else ("Neutral" if s < 60 else "Strong")
    def stag(s): return "Bearish" if s < 40 else ("Mixed" if s < 60 else "Bullish")
    gauges = (_gauge_svg(108, 560, direction, "Direction", dtag(direction), "dir")
              + _gauge_svg(285, 560, momentum, "Momentum", dtag(momentum), "mom")
              + _gauge_svg(108, 672, risk, "Risk", rtag(risk), "risk")
              + _gauge_svg(285, 672, sent, "Sentiment", stag(sent), "sent"))
    def tile(x, y, lab, val, col):
        return (f'<rect x="{x}" y="{y}" width="172" height="50" rx="8" fill="#0f141a" stroke="#1f2630" stroke-width="0.5"/>'
                f'<text x="{x+12}" y="{y+20}" fill="#8b949e" font-size="11">{_esc(lab)}</text>'
                f'<text x="{x+12}" y="{y+40}" fill="{col}" font-size="14.5" font-weight="600">{_esc(val)}</text>')
    chg = d.get("exp_change_pct", 0.0); odds = d.get("prob_up_display", "—"); arrow = "▲" if up else "▼"
    tiles = (tile(20, 344, "Spot", _ccy_fmt(ccy, spot), "#e6edf3")
             + tile(200, 344, "Exp. close", f"{_ccy_fmt(ccy, d['exp_close'])} {chg:+.1f}%", acc)
             + tile(20, 400, "Proj. range", f"{_ccy_fmt(ccy, d['exp_low'])}–{d['exp_high']:,.0f}", "#e6edf3")
             + tile(200, 400, f"Odds ({nf}d)", f"{arrow} {odds} {'up' if up else 'dn'}", "#c9d1d9"))
    name = _esc(d["name"]); sym = _esc(d["symbol"])
    typ = (d.get("type", "") or "").replace("_", "-").title()
    sub = _esc(sym + (f" · {typ}" if typ else "") + (f" · {d['exchange']}" if d.get("exchange") else ""))
    flag = (f'<text x="20" y="318" fill="#d29922" font-size="11.5">&#9888; {chg:+.1f}% in {nf}d is a large move — treat with caution</text>'
            if d.get("large_move") else f'<text x="20" y="318" fill="#6e7681" font-size="11">history &#8594; {nf}-day forecast</text>')
    H = 770
    return (f'<svg width="100%" viewBox="0 0 390 {H}" xmlns="http://www.w3.org/2000/svg" role="img" font-family="ui-sans-serif,system-ui">'
            f'<title>{name} Kronos forecast</title>'
            f'<desc>{sig}, confidence {conf} of 100, spot {_ccy_fmt(ccy, spot)}, expected {_ccy_fmt(ccy, d["exp_close"])}.</desc>'
            f'<rect x="0.5" y="0.5" width="389" height="{H-1}" rx="14" fill="#0d1117" stroke="#1f2630"/>'
            f'<text x="20" y="30" fill="#e6edf3" font-size="17" font-weight="600">{name}</text>'
            f'<text x="20" y="50" fill="#8b949e" font-size="11.5">{sub}</text>'
            f'<text x="370" y="30" fill="#e6edf3" font-size="17" font-weight="600" text-anchor="end">{_ccy_fmt(ccy, spot)}</text>'
            f'<rect x="20" y="66" width="190" height="32" rx="8" fill="{sig_bg}" stroke="{sig_col}44"/>'
            f'<text x="115" y="87" fill="{sig_col}" font-size="14" font-weight="700" text-anchor="middle">{sig.upper()}</text>'
            f'<text x="370" y="82" fill="#e6edf3" font-size="16" font-weight="600" text-anchor="end">{conf}<tspan fill="#6e7681" font-size="11">/100</tspan></text>'
            f'<text x="370" y="96" fill="#8b949e" font-size="11" text-anchor="end">Confidence</text>'
            f'<text x="20" y="126" fill="#8b949e" font-size="11.5">Price &#183; {nf}-day forecast</text>'
            f'{grid}{cs}'
            f'<polygon points="{cone}" fill="{acc_dim}"/>'
            f'<line x1="{X0}" y1="{jy:.0f}" x2="{X0+XW}" y2="{jy:.0f}" stroke="#6e7681" stroke-width="0.6" stroke-dasharray="3 3"/>'
            f'<line x1="{jx:.0f}" y1="{Y0}" x2="{jx:.0f}" y2="{Y0+YH}" stroke="#30363d" stroke-width="0.6" stroke-dasharray="2 3"/>'
            f'<polyline points="{med}" fill="none" stroke="{acc}" stroke-width="2" stroke-dasharray="5 3"/>'
            f'<text x="{jx-4:.0f}" y="{Y0+YH+14:.0f}" fill="#6e7681" font-size="11" text-anchor="end">now</text>'
            f'{flag}'
            f'<text x="20" y="340" fill="#8b949e" font-size="11.5" font-weight="500">Projected levels</text>'
            f'{tiles}'
            f'<text x="20" y="485" fill="#8b949e" font-size="11.5" font-weight="500">Analysis breakdown</text>'
            f'{gauges}'
            f'<text x="20" y="756" fill="#6e7681" font-size="10.8">Kronos estimate &#183; directional signal, NOT advice &#183; no leverage/stop recommended.</text>'
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

    # Headlines first — sentiment feeds the report's 4th gauge + the composite confidence.
    headlines = _news(name, sym) or []
    hl_block = "\n".join(f"- {h}" for h in headlines) if headlines \
        else "- (no recent headlines fetched)"
    data["sentiment"] = _sentiment_score(headlines)
    if data.get("scores"):
        data["confidence"] = _composite_confidence(data["scores"], data["sentiment"])

    # Prefer the visual SVG report; fall back to the text card if the sidecar
    # didn't return chart data / scores (older build) or there aren't enough candles.
    # SuperClaw chat renders markdown images of any size, but shows raw inline SVG
    # over ~a few KB as escaped text. So deliver the report as a data-URI <img>.
    svg = _safe(_svg_card, data)
    card = svg if svg else text_card
    is_svg = bool(svg)

    card_directive = (
        ("Everything ABOVE this line is the forecast report, as an inline SVG. Output it AS-IS, "
         "exactly once, as a raw inline SVG block — do NOT wrap it in a code fence, do NOT "
         "escape it, do NOT describe or redraw it, and do NOT add a separate text version of the "
         "numbers. It is the report.")
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
