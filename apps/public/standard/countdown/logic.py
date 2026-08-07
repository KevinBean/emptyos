"""Countdown — pure date math.

No ``self``, no kernel, no I/O. Given an event's ``date`` (ISO, YYYY-MM-DD)
and an optional ``repeat`` (none | yearly | monthly | weekly), computes the
signed day count to the relevant occurrence relative to *today*.

Direction is never a stored field — it falls out of the sign of ``days``:
positive = counting down to a future date, negative = counting up from a
past one. A one-off event (repeat="none") in the past is exactly a "days
since" tile; a recurring event always resolves to its next occurrence.
"""

from __future__ import annotations

from datetime import date, timedelta

REPEATS = ("none", "yearly", "monthly", "weekly")

# repeat -> icon shown when the note doesn't set its own
CATEGORY_ICONS = {
    "birthday": "🎂",
    "anniversary": "💍",
    "holiday": "🎉",
    "travel": "✈️",
    "deadline": "⏰",
    "work": "💼",
    "health": "❤️‍🩹",
    "personal": "⭐",
    "other": "📌",
}

# Theme-token colour names an event may pick (survive every theme — no hex).
COLORS = ("accent", "success", "warning", "danger", "info", "purple")

# Closed category vocabulary — kept in sync with the icon map above, and with
# the <select> options in pages/index.html. A category outside this set is
# coerced to "other" at the write boundary so an edit-and-resave can never
# silently drift a note's category to whatever option happens to sort first.
CATEGORIES = tuple(CATEGORY_ICONS.keys())


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def _clamp_day(year: int, month: int, day: int) -> date:
    """Build a date, clamping an out-of-range day (e.g. Feb 29/30/31) down
    to the last real day of that month — so a Jan-31 monthly repeat still
    fires in February instead of raising."""
    return date(year, month, min(day, _days_in_month(year, month)))


def next_occurrence(base: date, repeat: str, today: date) -> date:
    """Next occurrence of *base* on or after *today*, per *repeat*.

    ``repeat == "none"`` returns *base* unchanged (may be in the past).
    """
    if repeat == "yearly":
        candidate = _clamp_day(today.year, base.month, base.day)
        if candidate < today:
            candidate = _clamp_day(today.year + 1, base.month, base.day)
        return candidate
    if repeat == "monthly":
        candidate = _clamp_day(today.year, today.month, base.day)
        if candidate < today:
            y, m = today.year, today.month + 1
            if m > 12:
                y, m = y + 1, 1
            candidate = _clamp_day(y, m, base.day)
        return candidate
    if repeat == "weekly":
        delta = (base.weekday() - today.weekday()) % 7
        return today + timedelta(days=delta)
    return base


def event_status(event_date: str, repeat: str = "none", today: date | None = None) -> dict:
    """Resolve one event's countdown state.

    Returns ``{"days", "target_date", "base_date", "occurrence", "error"}``.
    ``days`` is signed (negative = past/elapsed). ``occurrence`` is the
    1-based year count for a yearly repeat (e.g. a 3rd anniversary),
    ``None`` otherwise. On an unparsable date, every numeric field is
    ``None`` and ``error`` names the problem — callers should skip the
    tile rather than crash the whole list.
    """
    today = today or date.today()
    repeat = (repeat or "none").strip().lower()
    if repeat not in REPEATS:
        repeat = "none"
    raw = (event_date or "")[:10]
    try:
        base = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return {"days": None, "target_date": "", "base_date": raw, "occurrence": None, "error": "invalid date"}

    target = next_occurrence(base, repeat, today)
    days = (target - today).days
    occurrence = (target.year - base.year) if repeat == "yearly" else None
    return {
        "days": days,
        "target_date": target.isoformat(),
        "base_date": base.isoformat(),
        "occurrence": occurrence,
        "error": None,
    }


def icon_for(category: str) -> str:
    return CATEGORY_ICONS.get((category or "").strip().lower(), CATEGORY_ICONS["other"])
