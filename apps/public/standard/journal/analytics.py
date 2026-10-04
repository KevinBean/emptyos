"""journal — read-side journal analytics — heatmap, mood trend, streak, word count, search, export.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: All non-mutating aggregations over the daily-note corpus. Pure reads; no write path. Source of truth for the heatmap/mood-trend/streak/word-count/search/export endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._daily_path (spine) for note resolution.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from emptyos.sdk import scheduled, web_route

from .parser import MOOD_SCORE, parse_entries

if TYPE_CHECKING:
    from .app import JournalApp  # noqa: F401 — for type hints only


# ─── Bind to JournalApp class as ────────────────────────────────
#   api_heatmap     = _analytics.api_heatmap
#   api_mood_trend  = _analytics.api_mood_trend
#   api_search      = _analytics.api_search
#   api_export      = _analytics.api_export
#   api_streak      = _analytics.api_streak
#   api_word_count  = _analytics.api_word_count
#   _streak_as_of   = _analytics._streak_as_of
#   _nudge_enabled  = _analytics._nudge_enabled
#   scheduled_no_entry_nudge = _analytics.scheduled_no_entry_nudge
#   _heatmap        = _analytics._heatmap
#   _mood_trend     = _analytics._mood_trend
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/heatmap")
async def api_heatmap(self, request):
    months = int(request.query_params.get("months", "3"))
    return await self._heatmap(months)


@web_route("GET", "/api/mood-trend")
async def api_mood_trend(self, request):
    days = int(request.query_params.get("days", "30"))
    return await self._mood_trend(days)


@web_route("GET", "/api/search")
async def api_search(self, request):
    """Search journal entries across dates."""
    q = request.query_params.get("q", "").strip().lower()
    if not q:
        return {"results": [], "error": "q parameter required"}
    days = int(request.query_params.get("days", "90"))
    today = date.today()
    results = []
    for i in range(days):
        d = today - timedelta(days=i)
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))
        except Exception:
            continue
        entries = parse_entries(content)
        matching = [e for e in entries if q in e.get("text", "").lower()]
        if matching:
            results.append({"date": d.isoformat(), "entries": matching})
    return {
        "results": results,
        "total_matches": sum(len(r["entries"]) for r in results),
        "query": q,
    }


@web_route("GET", "/api/export")
async def api_export(self, request):
    """Export journal entries as markdown text."""
    days = int(request.query_params.get("days", "30"))
    today = date.today()
    lines = [f"# Journal Export ({days} days)\n"]
    for i in range(days):
        d = today - timedelta(days=i)
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))
            entries = parse_entries(content)
            if entries:
                lines.append(f"\n## {d.isoformat()} {d.strftime('%A')}\n")
                for e in entries:
                    lines.append(f"- **{e['time']}** {e['emoji']} {e['text']}")
        except Exception:
            continue
    return {"markdown": "\n".join(lines), "days": days}


async def _streak_as_of(self, start) -> int:
    """Consecutive journaled days counting backward from `start`
    (inclusive). Factored out of `api_streak` so the nudge below can ask
    "what's the streak as of yesterday" without duplicating the loop."""
    streak = 0
    d = start
    while True:
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))
            entries = parse_entries(content)
            if entries:
                streak += 1
                d -= timedelta(days=1)
                continue
        except Exception:
            pass
        break
    return streak


@web_route("GET", "/api/streak")
async def api_streak(self, request):
    """Journaling streak — consecutive days with entries."""
    return {"streak": await self._streak_as_of(date.today())}


def _nudge_enabled(self) -> bool:
    """Evening no-entry-today nudge toggle (dark default)."""
    return bool(self.setting_or_config(
        "journal.feature.journal-no-entry-nudge.enabled",
        False,
        config_key="feature.journal-no-entry-nudge.enabled",
    ))


@scheduled("0 20 * * *", id="journal-no-entry-nudge")
async def scheduled_no_entry_nudge(self):
    """Evening nudge if today has no journal entry yet — protects the streak.

    Flagged in gap analysis (adoption of the shared proactive_notify_or_raw
    pattern already used by countdown/vlog/quotes/task/people this pass):
    journal has a real streak concept (`api_streak`) but nothing ever
    surfaces "you haven't written today" before the day ends.
    """
    if not self._nudge_enabled():
        return {"enabled": False, "sent": False}
    today = date.today()
    try:
        content = await self.read(str(self._daily_path(today)))
        entries = parse_entries(content)
    except Exception:
        entries = []
    if entries:
        return {"enabled": True, "sent": False, "reason": "already journaled today"}

    streak = await self._streak_as_of(today - timedelta(days=1))
    text = (
        f"No journal entry yet today — write one to keep your {streak}-day streak alive."
        if streak > 0 else
        "No journal entry yet today."
    )
    # A brand-new nudge source (nothing delivered before this feature
    # existed) — call proactive_notify() directly rather than the _or_raw
    # migration shim, which doesn't accept `link` and exists only for
    # pushers that worked before the gate did.
    await self.proactive_notify(
        kind="journal-nudge",
        text=text,
        dedup_key=f"journal-nudge:{today.isoformat()}",
        priority="info",
        link={"text": "Open journal", "href": "/journal/"},
    )
    return {"enabled": True, "sent": True, "streak": streak}


@web_route("GET", "/api/word-count")
async def api_word_count(self, request):
    """Word count stats for recent journal entries."""
    days = int(request.query_params.get("days", "30"))
    today = date.today()
    daily = []
    total = 0
    for i in range(days):
        d = today - timedelta(days=i)
        try:
            content = await self.read(str(self._daily_path(d)))
            wc = len(content.split())
            daily.append({"date": d.isoformat(), "words": wc})
            total += wc
        except Exception:
            continue
    return {
        "total_words": total,
        "days_written": len(daily),
        "avg_words": round(total / len(daily)) if daily else 0,
        "daily": daily,
    }


async def _heatmap(self, months: int) -> list[dict]:
    today = date.today()
    start = today - timedelta(days=months * 30)
    data = []
    d = start
    while d <= today:
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))
            entries = parse_entries(content)
            data.append({"date": d.isoformat(), "count": len(entries)})
        except Exception:
            data.append({"date": d.isoformat(), "count": 0})
        d += timedelta(days=1)
    return data


async def _mood_trend(self, days: int) -> list[dict]:
    results = []
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))
            entries = parse_entries(content)
            if entries:
                avg = sum(MOOD_SCORE.get(e["mood"], 3) for e in entries) / len(entries)
                results.append({"date": d.isoformat(), "score": round(avg, 1)})
        except Exception:
            continue
    return list(reversed(results))
