"""Spaced Repetition Scheduling — FSRS-4.5.

Reusable SRS primitives for any app that needs review scheduling.
Used by: learn (KB quizzes), dictionary (pronunciation drills), media
(highlights/flashcards, personal).

Items are plain dicts. The scheduler owns these fields:

    s:             float — stability, days until recall probability hits `retention`
    d:             float — difficulty, 1..10
    last_reviewed: str   — ISO date of the review that produced the current schedule
    next_review:   str   — ISO date the item is due
    review_count:  int   — total reviews (kept under this name for existing readers)
    lapses:        int   — times the item was forgotten after being learned

An item with no `s` is treated as a first exposure, which is what makes
migration off the old scheduler non-destructive: a legacy entry keeps its
existing `next_review` until it is next graded, then re-anchors onto a correct
curve. Nothing inside the legal horizon is retroactively rescheduled.

The one exception is repair, not rescheduling: an entry the *old* scheduler
booked beyond `MAX_INTERVAL_DAYS` would never come due, so it would never be
graded, so it would never re-anchor — stranded. `repair_legacy_schedule` pulls
exactly those back to the horizon, which is the longest interval this scheduler
could itself produce; it can never make an item due sooner than a year out.

Why FSRS and not SM-2: the previous implementation here was labelled SM-2 but
computed `int(ease ** review_count)` while `ease` itself grew each pass — a
double compounding that produced 887-day intervals by the 7th successful review
and 14064-day intervals by the 9th, bounded only by a 50-year cap that hid the
runaway rather than fixing it. Correcting it to true SM-2 (`I(n) = I(n-1) × EF`)
would have required storing the previous interval, which legacy entries do not
carry — the same state migration FSRS needs, for a weaker memory model. See
`docs/OPEN-SOURCE-BORROWING-PLAN.md` § Engram (2026-08-06).

FSRS-4.5 weights and update rules are from open-spaced-repetition, transcribed
from github.com/nagisanzenin/engram (MIT) `scripts/engram.py`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, timedelta

# FSRS-4.5 default parameters (open-spaced-repetition). w[0..3] are the initial
# stabilities for again/hard/good/easy; the rest shape difficulty and growth.
W = [
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031,
    1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
]
DECAY = -0.5
FACTOR = 19.0 / 81.0  # chosen so R(t=S) = 0.9

RATINGS: dict[str, int] = {"again": 1, "hard": 2, "good": 3, "easy": 4}

RETENTION_DEFAULT = 0.90
RETENTION_MIN, RETENTION_MAX = 0.70, 0.97
# One year. The old 50-year ceiling existed to absorb an exponential blow-up that
# no longer happens; a review booked beyond a year is indistinguishable from
# "retired" for every surface that renders a due queue.
MAX_INTERVAL_DAYS = 365


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _num(x, default=None):
    """Tolerant float read — a hand-edited or corrupt field must not raise."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if v != v or v in (float("inf"), float("-inf")) else v


# ─── FSRS core (pure) ───────────────────────────────────────────────


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability the item is still recalled after `elapsed_days`."""
    if stability <= 0:
        return 0.0
    return (1.0 + FACTOR * elapsed_days / stability) ** DECAY


def interval_for(stability: float, retention: float = RETENTION_DEFAULT) -> int:
    """Days until recall probability decays to `retention`."""
    retention = _clamp(retention, RETENTION_MIN, RETENTION_MAX)
    days = stability / FACTOR * (retention ** (1.0 / DECAY) - 1.0)
    return int(_clamp(round(days), 1, MAX_INTERVAL_DAYS))


def init_stability(g: int) -> float:
    return _clamp(W[g - 1], 0.1, 100.0)


def init_difficulty(g: int) -> float:
    return _clamp(W[4] - (g - 3) * W[5], 1.0, 10.0)


def next_difficulty(d: float, g: int) -> float:
    nd = d - W[6] * (g - 3)
    # FSRS-4.5 mean-reverts toward D0(3) (good), not D0(4) — the latter is the
    # FSRS-5 rule and would inflate stability growth under this weight vector.
    nd = W[7] * init_difficulty(3) + (1.0 - W[7]) * nd
    return _clamp(nd, 1.0, 10.0)


def next_stability_recall(d: float, s: float, r: float, g: int) -> float:
    hard_penalty = W[15] if g == 2 else 1.0
    easy_bonus = W[16] if g == 4 else 1.0
    grow = (
        math.exp(W[8]) * (11.0 - d) * (s ** -W[9])
        * (math.exp(W[10] * (1.0 - r)) - 1.0) * hard_penalty * easy_bonus
    )
    return _clamp(s * (1.0 + grow), 0.1, 36500.0)


def next_stability_forget(d: float, s: float, r: float) -> float:
    sf = W[11] * (d ** -W[12]) * (((s + 1.0) ** W[13]) - 1.0) * math.exp(W[14] * (1.0 - r))
    return _clamp(min(sf, s), 0.1, 36500.0)  # a lapse never increases stability


# ─── Rating scales ──────────────────────────────────────────────────


def quality_to_rating(quality: int) -> str:
    """Legacy 0-5 SM-2 quality → FSRS rating."""
    q = int(quality)
    if q < 3:
        return "again"
    if q == 3:
        return "hard"
    if q == 4:
        return "good"
    return "easy"


def score_to_rating(score: int) -> str:
    """Quiz score (0-100) → FSRS rating. Thresholds match the bands the learn
    app has always used to grade a 3-question MCQ (0 / 1 / 2 / 3 correct)."""
    s = int(score)
    if s < 51:
        return "again"
    if s < 76:
        return "hard"
    if s < 91:
        return "good"
    return "easy"


# ─── Scheduler ──────────────────────────────────────────────────────


def fsrs_schedule(
    item: dict,
    rating: str,
    *,
    retention: float = RETENTION_DEFAULT,
    today: date | None = None,
) -> None:
    """Advance an item's schedule by one review. Mutates `item` in place.

    `rating` is one of again / hard / good / easy. An unknown rating is treated
    as "good" rather than raising — a scheduler is not the right place to fail a
    review the user already answered.
    """
    g = RATINGS.get(str(rating).lower(), 3)
    now = today or date.today()

    s0 = _num(item.get("s"))
    if s0 is not None:
        s0 = _clamp(s0, 0.1, 36500.0)  # a corrupt s=0 would make s ** -w blow up

    if s0 is None:
        # First exposure — also the path every legacy (ease/review_count) entry
        # takes on its first graded review after migration.
        s, d, r = init_stability(g), init_difficulty(g), None
    else:
        d0 = _num(item.get("d"))
        if d0 is None:
            d0 = init_difficulty(3)  # corrupt difficulty → re-anchor
        last = item.get("last_reviewed") or ""
        try:
            elapsed = max(0, (now - date.fromisoformat(last)).days)
        except (TypeError, ValueError):
            elapsed = 0
        r = retrievability(elapsed, s0)
        d = next_difficulty(d0, g)
        s = next_stability_forget(d0, s0, r) if g == 1 else next_stability_recall(d0, s0, r, g)

    reps = _num(item.get("review_count"), 0) or 0
    lapses = _num(item.get("lapses"), 0) or 0

    item["s"] = round(s, 4)
    item["d"] = round(d, 4)
    item["last_reviewed"] = now.isoformat()
    item["next_review"] = (now + timedelta(days=interval_for(s, retention))).isoformat()
    item["review_count"] = max(0, int(reps)) + 1
    item["lapses"] = max(0, int(lapses)) + (1 if (g == 1 and s0 is not None) else 0)
    item.pop("ease", None)  # SM-2 vestige; meaningless under FSRS


def sm2_schedule(item: dict, quality: int) -> None:
    """DEPRECATED quality-scale shim over `fsrs_schedule`.

    Kept so out-of-tree callers (personal apps) keep working and pick up the
    corrected curve; the name is historical. New code calls `fsrs_schedule`
    with a rating.
    """
    fsrs_schedule(item, quality_to_rating(quality))


def repair_legacy_schedule(items: Iterable[dict], today: date | None = None) -> int:
    """Pull pre-FSRS runaway bookings back inside the review horizon.

    The old scheduler compounded intervals exponentially and leaned on a
    50-year ceiling to hide it, so a card answered well seven times could be
    booked decades out. Such an entry never comes due, so it is never graded,
    so the soft re-anchor in `fsrs_schedule` never reaches it — it is
    *stranded*, not merely misdated. Fixing the formula does not repair the
    dates the old formula already wrote.

    Only entries that have never been through FSRS (no `s`) **and** are booked
    beyond `MAX_INTERVAL_DAYS` are touched, and they are pulled to the horizon
    rather than to today — the longest interval the new scheduler could itself
    produce. So this can never make an item due sooner than a year out, and it
    leaves every legacy booking already inside the legal range exactly where
    the user left it. That is the line between repairing a bug's output and
    retroactively rescheduling someone's deck.

    Mutates in place. Returns the number of entries changed.
    """
    now = today or date.today()
    horizon = (now + timedelta(days=MAX_INTERVAL_DAYS)).isoformat()
    fixed = 0
    for item in items:
        if not isinstance(item, dict) or item.get("s") is not None:
            continue
        if (item.get("next_review") or "") > horizon:
            item["next_review"] = horizon
            fixed += 1
    return fixed


# ─── Queue helpers ──────────────────────────────────────────────────


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
