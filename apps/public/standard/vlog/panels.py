"""Vlog — hub panel contribution.

Extracted from vlog/app.py per the multi-module pattern. Bound to VlogApp via
attribute assignment (`panel_vlog_today = _panels.panel_vlog_today`).
Pure read of the day list — no `self` state mutated. Do not import from `.app`.
"""

from __future__ import annotations

from datetime import date, timedelta


async def panel_vlog_today(self) -> dict | None:
    """stat-tile: did you vlog today + current streak of consecutive days."""
    try:
        dates = {
            it["date"]
            for it in self._scan_days()
            if (it["clip_count"] or len(self._list_clip_files(it["date"]))) > 0
        }
    except Exception:
        return None
    if not dates:
        return None

    today = date.today()
    recorded_today = today.isoformat() in dates

    streak = 0
    d = today
    # If today isn't recorded yet, the streak can still run up to yesterday.
    if not recorded_today:
        d = today - timedelta(days=1)
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)

    return {
        "label": "Vlog streak" if streak else "Vlog",
        "value": f"{streak}d" if streak else ("✓" if recorded_today else "—"),
        "detail": "recorded today" if recorded_today else "not yet today",
        "href": "/vlog/",
    }
