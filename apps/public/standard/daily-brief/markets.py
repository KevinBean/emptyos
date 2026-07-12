"""daily-brief — market pulse (quotes + indicators).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Ticker resolution, quote fetching, technical-indicator computation (SMA/EMA/RSI/MACD via shared math helpers) and the LLM market-pulse distillation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._cfg / self._locale (spine); shared._compute_indicators + DEFAULT_TICKERS.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import aiohttp
from .shared import _compute_indicators, MARKET_SYSTEM, DEFAULT_TICKERS, _LANG
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DailyBriefApp  # noqa: F401 — for type hints only


# ─── Bind to DailyBriefApp class as ────────────────────────────────
#   _markets_enabled  = _markets._markets_enabled
#   _tickers          = _markets._tickers
#   _fetch_quotes     = _markets._fetch_quotes
#   _fetch_quote      = _markets._fetch_quote
#   _distill_markets  = _markets._distill_markets
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── Market pulse ───────────────────────────────────────────
def _markets_enabled(self) -> bool:
    return bool(self._cfg("markets_enabled", False))


def _tickers(self) -> list[dict]:
    """Resolved instrument list: config list-of-dicts, settings CSV, or
    the energy-slanted default. Capped at 20."""
    raw = self.app_config("tickers", None)
    if isinstance(raw, list) and raw:
        out = []
        for t in raw:
            if isinstance(t, dict) and t.get("symbol"):
                out.append({"symbol": t["symbol"], "name": t.get("name") or t["symbol"]})
            elif isinstance(t, str) and t.strip():
                out.append({"symbol": t.strip(), "name": t.strip()})
        if out:
            return out[:20]
    csv = str(self._cfg("tickers_csv", "") or "").strip()
    if csv:
        syms = [s.strip() for s in csv.split(",") if s.strip()]
        return [{"symbol": s, "name": s} for s in syms][:20]
    return DEFAULT_TICKERS


async def _fetch_quotes(self) -> list[dict]:
    """Fetch 3mo daily closes per instrument from Yahoo's public chart API,
    compute indicators. Fail-soft per symbol (a dead/blocked symbol drops)."""
    tickers = self._tickers()
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; EmptyOS-DailyBrief/0.1)"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        results = await asyncio.gather(
            *(self._fetch_quote(session, t) for t in tickers),
            return_exceptions=True,
        )
    return [r for r in results if isinstance(r, dict)]


async def _fetch_quote(self, session, t: dict) -> dict | None:
    from urllib.parse import quote as _q
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{_q(t['symbol'], safe='=^.')}?range=3mo&interval=1d")
    async with session.get(url) as resp:
        resp.raise_for_status()
        body = await self._read_body(resp)
    import json as _json
    data = _json.loads(body)
    res = ((data.get("chart") or {}).get("result") or [None])[0] or {}
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quote.get("close") or []) if c is not None]
    ind = _compute_indicators(closes)
    if not ind:
        return None
    return {"symbol": t["symbol"], "name": t["name"], **ind}


async def _distill_markets(self, quotes: list[dict]) -> str:
    loc = self._locale()
    lang_name, lang = _LANG[loc]
    focus = str(self._cfg("focus", "") or "").strip()
    focus_clause = (
        f"The reader's focus: {focus}. Where an instrument touches that, say why it matters to them."
        if focus else "No special-interest lens — read the signals straight."
    )
    system = MARKET_SYSTEM.format(lang=lang, lang_name=lang_name, focus_clause=focus_clause)
    rows = []
    for q in quotes:
        rows.append(
            f"{q['name']} ({q['symbol']}): last {q['last']}, {q['change_pct']:+.2f}% d/d, "
            f"RSI {q['rsi']}, SMA20 {q['sma20']}, SMA50 {q['sma50']}, "
            f"MACD-hist {q['macd_hist']}, trend {q['trend']}"
        )
    out = await self.think("Indicators (daily):\n" + "\n".join(rows),
                           domain="reason", system=system, temperature=0.4)
    return (out if isinstance(out, str) else str(out)).strip()
