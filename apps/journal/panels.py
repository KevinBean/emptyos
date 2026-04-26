"""Journal — hub slot + panel contributions.

Extracted from journal/app.py to keep the core under 800 lines (P4 Atomic).
Methods are bound to JournalApp via attribute assignment.
"""

from __future__ import annotations

from datetime import date, timedelta

from .parser import MOOD_EMOJI, parse_entries


async def slot_today(self) -> list[dict]:
    """If today has no journal entry yet, nudge to write one."""
    today = date.today()
    try:
        content = await self.read(str(self._daily_path(today)))
        entries = parse_entries(content)
    except Exception:
        entries = []
    if entries:
        return []
    return [{
        "title": "Write today's journal",
        "subtitle": "no entry yet",
        "href": "/journal/",
        "badge": "ritual",
        "priority": 5,
    }]


async def slot_recent_thinking(self) -> list[dict]:
    """Recent journal days with entries — glanceable mood trail."""
    recent = await self._recent_days(3)
    out = []
    for day in recent:
        n = day.get("entries") or 0
        if not n:
            continue
        # TODO(journal-bug): daily notes can be corrupted with ~7× duplicate
        # appends (see memory project_journal_corruption_bug.md). Until the
        # vault repair lands, cap the displayed count so the hub doesn't
        # show "141 entries · good" on a broken day.
        count_label = "many" if n > 30 else str(n)
        emoji = day.get("emoji") or ""
        out.append({
            "title": f"{emoji} {day['date']}".strip(),
            "subtitle": f"{count_label} entries" + (f" · {day['mood']}" if day.get("mood") else ""),
            "href": "/journal/",
            "badge": None,
            "priority": 0,
        })
    return out


async def panel_yesterday(self) -> dict | None:
    """Summary of yesterday — entry count + completed tasks."""
    y = date.today() - timedelta(days=1)
    try:
        content = await self.read(str(self._daily_path(y)))
    except Exception:
        return None
    entries = parse_entries(content)
    done_count = content.count("- [x]") + content.count("- [X]")
    if not entries and not done_count:
        return None
    dominant = ""
    if entries:
        moods = [e.get("mood", "") for e in entries if e.get("mood")]
        if moods:
            dominant = max(set(moods), key=moods.count)
    emoji = MOOD_EMOJI.get(dominant, "")
    parts = []
    if done_count:
        parts.append(f"{done_count} tasks completed")
    if entries:
        # TODO(journal-bug): cap entry count when daily note is corrupted
        # (~7× appends). See memory project_journal_corruption_bug.md.
        n = len(entries)
        parts.append(("many entries" if n > 30 else f"{n} entries"))
    body = ", ".join(parts) + (f" · {emoji} {dominant}" if dominant else "")
    return {
        "title": "📝 Yesterday",
        "body": body,
        "href": "/journal/",
    }


async def panel_journal_today(self) -> dict | None:
    """Dashboard tile: today's journal entry count + dominant mood."""
    today = date.today()
    try:
        content = await self.read(str(self._daily_path(today)))
    except Exception:
        return None
    entries = parse_entries(content)
    if not entries:
        return None
    # TODO(journal-bug): same duplicate-append corruption possible here.
    n = len(entries)
    value = "many" if n > 30 else str(n)
    return self.stat_tile("📓", value, "today", "/journal/")


async def panel_month_compare(self) -> list[dict] | None:
    """This month vs last month — journal days + entry count."""
    today = date.today()
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    async def _count_days(start: date, end: date) -> tuple[int, int]:
        days_with_entries = 0
        total_entries = 0
        d = start
        while d <= end:
            try:
                raw = await self.read(str(self._daily_path(d)))
                entries = parse_entries(raw) if raw else []
                if entries:
                    days_with_entries += 1
                    total_entries += len(entries)
            except Exception:
                pass
            d += timedelta(days=1)
        return days_with_entries, total_entries

    curr_days, curr_entries = await _count_days(month_start, today)
    prev_days, prev_entries = await _count_days(prev_start, prev_end)
    return [
        {
            "name": "Journal days",
            "curr": curr_days,
            "prev": prev_days,
            "delta": curr_days - prev_days,
            "unit": " days",
        },
        {
            "name": "Entries",
            "curr": curr_entries,
            "prev": prev_entries,
            "delta": curr_entries - prev_entries,
            "unit": "",
        },
    ]
