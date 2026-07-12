"""Spaced Repetition Scheduling — SM-2 algorithm.

Reusable SRS primitives for any app that needs review scheduling.
Used by: media (highlights/flashcards), dictionary (vocabulary).

Items are plain dicts with these SRS fields:
    ease: float (default 2.5) — difficulty factor
    review_count: int (default 0) — total reviews
    next_review: str — ISO date of next scheduled review
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

# Cap the scheduled interval so the exponential SM-2 growth can never push
# next_review past the date range (year 9999 ≈ 2.9M days out). Without this,
# a card passed ~16+ times has 2.5**count exceed the range and date arithmetic
# raises "date value out of range" (500). 50 years is generous beyond any real
# review schedule. The cap also absorbs float overflow at very large counts.
MAX_INTERVAL_DAYS = 365 * 50


def sm2_schedule(item: dict, quality: int) -> None:
    """SM-2 variant scheduling. Mutates item in-place.

    quality: 0-5 (0-2 = fail → reset to 1 day, 3-5 = pass → exponential growth).
    """
    ease = item.get("ease", 2.5)
    count = item.get("review_count", 0)
    if count == 0:
        days = 0
    elif quality < 3:
        ease = max(1.3, ease - 0.2)
        days = 1
    else:
        ease = ease + 0.1 * (quality - 3)
        try:
            raw = int(ease**count)
        except (OverflowError, ValueError):
            raw = MAX_INTERVAL_DAYS
        days = max(1, min(raw, MAX_INTERVAL_DAYS))
    item["ease"] = round(ease, 2)
    item["review_count"] = count + 1
    item["next_review"] = (date.today() + timedelta(days=days)).isoformat()


def due_items(items: list[dict], today_str: str | None = None) -> list[dict]:
    """Filter items where next_review <= today."""
    today_str = today_str or date.today().isoformat()
    return [i for i in items if i.get("next_review", "") <= today_str]


def review_stats(items: list[dict]) -> dict:
    """Aggregate review statistics."""
    today = date.today().isoformat()
    due = sum(1 for i in items if i.get("next_review", "") <= today)
    reviewed = sum(
        1 for i in items if i.get("review_count", 0) > 0 and i.get("next_review", "") > today
    )
    return {"total": len(items), "due_today": due, "reviewed_today": reviewed}


def streak_days(dates: Iterable[str]) -> int:
    """Consecutive-days-with-at-least-one-entry streak ending today.

    `dates` is any iterable of ISO date strings (typically each item's
    `last_reviewed`); empties and duplicates are tolerated. Counts back from
    today while each prior day has an entry, stopping at the first gap.
    """
    seen = sorted({d for d in dates if d}, reverse=True)
    streak = 0
    check = date.today()
    for d in seen:
        if d == check.isoformat():
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak
