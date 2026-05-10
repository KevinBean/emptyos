"""Spaced Repetition Scheduling — SM-2 algorithm.

Reusable SRS primitives for any app that needs review scheduling.
Used by: media (highlights/flashcards), dictionary (vocabulary).

Items are plain dicts with these SRS fields:
    ease: float (default 2.5) — difficulty factor
    review_count: int (default 0) — total reviews
    next_review: str — ISO date of next scheduled review
"""

from __future__ import annotations

from datetime import date, timedelta


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
        days = max(1, int(ease**count))
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
