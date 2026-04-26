"""Shared practice statistics for learning/practice apps.

Used by: shadowing, speaking, voice-review, english, reader, interview-studio.

Usage:
    from emptyos.sdk.stats import practice_stats, rolling_average
    stats = practice_stats(scores=[0.8, 0.9, 0.7, 0.85], dates=["2026-04-01", ...])
    avg = rolling_average([0.8, 0.9, 0.7, 0.85, 0.95], window=5)
"""

from __future__ import annotations

from datetime import date, timedelta

from emptyos.sdk.utils import streak_from_dates


def practice_stats(
    scores: list[float | int],
    dates: list[str] | None = None,
    recent_window: int = 20,
) -> dict:
    """Compute standard practice statistics.

    Args:
        scores: List of score values (any numeric).
        dates: Optional list of ISO date strings (for today/streak calculations).
        recent_window: Number of recent scores for rolling average.

    Returns:
        dict with: total, average, recent_avg, best, worst, today_count, streak.
    """
    result: dict = {
        "total": len(scores),
        "average": 0,
        "recent_avg": 0,
        "best": 0,
        "worst": 0,
        "today_count": 0,
        "streak": 0,
    }
    if not scores:
        return result

    result["average"] = round(sum(scores) / len(scores), 3)
    result["best"] = max(scores)
    result["worst"] = min(scores)

    recent = scores[-recent_window:] if len(scores) >= recent_window else scores
    result["recent_avg"] = round(sum(recent) / len(recent), 3)

    if dates:
        today_str = date.today().isoformat()
        result["today_count"] = sum(1 for d in dates if d.startswith(today_str))
        result["streak"] = streak_from_dates(set(d[:10] for d in dates))

    return result


def rolling_average(values: list[float | int], window: int = 20) -> float:
    """Compute rolling average of the last `window` values."""
    if not values:
        return 0
    recent = values[-window:]
    return round(sum(recent) / len(recent), 3)


def progress_percent(current: float, target: float) -> int:
    """Calculate progress percentage toward a target. Clamped to 0-100."""
    if target <= 0:
        return 0
    return max(0, min(100, round(current / target * 100)))


def daily_counts(dates: list[str], days: int = 30) -> dict[str, int]:
    """Count entries per day for the last N days. Returns {date_str: count}.

    Useful for heatmaps and activity charts.
    """
    today = date.today()
    counts: dict[str, int] = {}
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        counts[d] = 0
    for d in dates:
        key = d[:10]
        if key in counts:
            counts[key] += 1
    return counts
