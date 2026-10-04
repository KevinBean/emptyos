"""Unit tests for emptyos.sdk.srs — pure scheduler helpers, no daemon."""

from datetime import date, timedelta

from emptyos.sdk.srs import (
    MAX_INTERVAL_DAYS,
    due_items,
    fsrs_schedule,
    quality_to_rating,
    repair_legacy_schedule,
    review_stats,
    score_to_rating,
    sm2_schedule,
    streak_days,
)

TODAY = date.today()
ISO = TODAY.isoformat()
Y1 = (TODAY - timedelta(days=1)).isoformat()
Y2 = (TODAY - timedelta(days=2)).isoformat()
GAP = (TODAY - timedelta(days=4)).isoformat()

ANCHOR = date(2026, 1, 1)


def _interval(item: dict, on: date) -> int:
    return (date.fromisoformat(item["next_review"]) - on).days


def _drill(rating: str, reps: int, start: date = ANCHOR) -> list[int]:
    """Schedule `reps` consecutive reviews at `rating`, each on its own due
    date. Returns the interval booked at each step."""
    item: dict = {}
    cur, out = start, []
    for _ in range(reps):
        fsrs_schedule(item, rating, today=cur)
        out.append(_interval(item, cur))
        cur = date.fromisoformat(item["next_review"])
    return out


class TestStreakDays:
    def test_empty_and_blank(self):
        assert streak_days([]) == 0
        assert streak_days(["", None]) == 0

    def test_today_only(self):
        assert streak_days([ISO]) == 1

    def test_consecutive_with_dupes(self):
        # today + yesterday (+ duplicate yesterday) + 2-days-ago = 3
        assert streak_days([ISO, Y1, Y1, Y2]) == 3

    def test_streak_must_include_today(self):
        # most recent entry is yesterday, not today → streak is 0
        assert streak_days([Y1, Y2]) == 0

    def test_stops_at_gap(self):
        assert streak_days([ISO, GAP]) == 1
        assert streak_days([ISO, Y1, Y2, GAP]) == 3


class TestRatingScales:
    def test_quality_to_rating_bands(self):
        assert [quality_to_rating(q) for q in range(6)] == [
            "again", "again", "again", "hard", "good", "easy",
        ]

    def test_score_to_rating_bands(self):
        # 3-question MCQ: 0/1 correct fails, 2 is shaky, 3 is clean.
        assert score_to_rating(0) == "again"
        assert score_to_rating(33) == "again"
        assert score_to_rating(67) == "hard"
        assert score_to_rating(85) == "good"
        assert score_to_rating(100) == "easy"


class TestFsrsSchedule:
    def test_first_review_books_a_real_interval(self):
        item: dict = {}
        fsrs_schedule(item, "good", today=ANCHOR)
        assert item["review_count"] == 1
        assert item["lapses"] == 0
        assert item["last_reviewed"] == ANCHOR.isoformat()
        assert 1 <= _interval(item, ANCHOR) <= 10

    def test_intervals_grow_monotonically_on_good(self):
        ivls = _drill("good", 5)
        assert ivls == sorted(ivls)
        assert len(set(ivls)) > 1  # actually growing, not flat

    def test_growth_stays_bounded_the_old_scheduler_did_not(self):
        """The defect this scheduler replaced: the previous `sm2_schedule`
        computed int(ease ** review_count) with a growing ease, reaching 887
        days by rep 7 and 14064 by rep 9 — bounded only by a 50-year cap that
        hid the runaway. Nothing may exceed one year, ever."""
        for rating in ("good", "easy", "hard"):
            assert max(_drill(rating, 12)) <= MAX_INTERVAL_DAYS

    def test_rep_7_stays_within_a_year(self):
        # The exact rep the old curve blew past 2 years on.
        assert _drill("good", 7)[6] <= MAX_INTERVAL_DAYS

    def test_hard_keeps_the_item_close(self):
        # A struggling item must stay in rotation, not drift out.
        assert max(_drill("hard", 8)) <= 14

    def test_again_collapses_the_interval_and_counts_a_lapse(self):
        """A lapse drops stability to a fraction — deliberately *not* SM-2's
        hard reset to 1 day, which throws away everything the item had earned.
        The item comes back soon, but proportionally to what it was."""
        item: dict = {}
        cur = ANCHOR
        for _ in range(3):
            fsrs_schedule(item, "good", today=cur)
            cur = date.fromisoformat(item["next_review"])
        s_before, ivl_before = item["s"], _interval(item, ANCHOR)
        fsrs_schedule(item, "again", today=cur)
        assert item["s"] < s_before / 5   # a lapse never increases stability
        assert item["lapses"] == 1
        assert _interval(item, cur) < ivl_before / 5

    def test_first_exposure_again_is_not_a_lapse(self):
        item: dict = {}
        fsrs_schedule(item, "again", today=ANCHOR)
        assert item["lapses"] == 0  # never learned → nothing was forgotten

    def test_unknown_rating_falls_back_to_good(self):
        a: dict = {}
        b: dict = {}
        fsrs_schedule(a, "not-a-rating", today=ANCHOR)
        fsrs_schedule(b, "good", today=ANCHOR)
        assert a["next_review"] == b["next_review"]

    def test_corrupt_state_does_not_raise(self):
        for bad in ({"s": 0}, {"s": "many"}, {"s": 5, "d": None},
                    {"s": 5, "last_reviewed": "not-a-date"},
                    {"review_count": "lots", "lapses": None}):
            fsrs_schedule(bad, "good", today=ANCHOR)
            assert bad["next_review"] > ANCHOR.isoformat()


class TestLegacyMigration:
    """A legacy entry carries ease/review_count and no `s`. Migration must be
    soft: nothing is retroactively rescheduled, and the next grade re-anchors
    onto a correct curve."""

    def _legacy(self, days: int = 900) -> dict:
        far = (TODAY + timedelta(days=days)).isoformat()
        return {"ease": 2.9, "review_count": 5, "next_review": far,
                "last_reviewed": Y2}

    def test_untouched_until_graded(self):
        item = self._legacy(days=200)         # inside the legal horizon
        before = item["next_review"]
        assert due_items([item]) == []        # still not due — not moved
        assert item["next_review"] == before  # reading never mutates

    def test_regrading_reanchors_and_drops_the_vestige(self):
        item = self._legacy()
        fsrs_schedule(item, "good", today=TODAY)
        assert "ease" not in item                    # SM-2 vestige removed
        assert "s" in item and "d" in item
        assert item["review_count"] == 6             # history preserved
        assert _interval(item, TODAY) <= MAX_INTERVAL_DAYS
        assert item["next_review"] < (TODAY + timedelta(days=900)).isoformat()

    def test_runaway_booking_is_stranded_without_repair(self):
        """The failure the repair exists for: fixing the formula does not fix
        the dates the old formula already wrote."""
        item = self._legacy(days=14064)   # rep 9 under the old scheduler
        assert due_items([item]) == []    # never due → never graded → never re-anchored
        assert "s" not in item

    def test_sm2_shim_routes_to_fsrs(self):
        shim: dict = {}
        direct: dict = {}
        sm2_schedule(shim, 4)
        fsrs_schedule(direct, "good")
        assert shim["next_review"] == direct["next_review"]
        assert "ease" not in shim


class TestRepairLegacySchedule:
    """The repair clamps the old scheduler's runaway output, and nothing else."""

    HORIZON = (TODAY + timedelta(days=MAX_INTERVAL_DAYS)).isoformat()

    def test_clamps_runaway_to_the_horizon_not_to_today(self):
        item = {"ease": 4.9, "review_count": 9,
                "next_review": (TODAY + timedelta(days=14064)).isoformat()}
        assert repair_legacy_schedule([item]) == 1
        assert item["next_review"] == self.HORIZON
        assert due_items([item]) == []   # repaired, never made due early

    def test_leaves_bookings_inside_the_horizon_alone(self):
        for days in (0, 1, 200, MAX_INTERVAL_DAYS):
            item = {"ease": 2.5, "next_review": (TODAY + timedelta(days=days)).isoformat()}
            before = item["next_review"]
            assert repair_legacy_schedule([item]) == 0
            assert item["next_review"] == before

    def test_never_touches_an_item_fsrs_already_owns(self):
        """`s` present means the new scheduler booked it, so the date is real —
        even the maximum-interval one it is entitled to produce."""
        item: dict = {}
        fsrs_schedule(item, "easy", today=TODAY)
        item["next_review"] = (TODAY + timedelta(days=9000)).isoformat()  # hand-edited
        assert repair_legacy_schedule([item]) == 0
        assert item["next_review"] == (TODAY + timedelta(days=9000)).isoformat()

    def test_is_idempotent_and_tolerates_junk(self):
        far = {"next_review": (TODAY + timedelta(days=5000)).isoformat()}
        junk = [far, {}, {"next_review": None}, {"next_review": ""}, "not-a-dict", None]
        assert repair_legacy_schedule(junk) == 1
        assert repair_legacy_schedule(junk) == 0   # second pass is a no-op
        assert far["next_review"] == self.HORIZON

    def test_repaired_entry_reanchors_on_its_next_grade(self):
        item = {"ease": 4.9, "review_count": 9,
                "next_review": (TODAY + timedelta(days=14064)).isoformat()}
        repair_legacy_schedule([item])
        due = date.fromisoformat(item["next_review"])
        fsrs_schedule(item, "good", today=due)
        assert "s" in item and "ease" not in item
        assert _interval(item, due) <= MAX_INTERVAL_DAYS


class TestQueueHelpers:
    def test_due_items_and_stats(self):
        items = [{"next_review": ISO}, {"next_review": GAP},
                 {"next_review": (TODAY + timedelta(days=3)).isoformat(), "review_count": 2}]
        assert len(due_items(items)) == 2
        st = review_stats(items)
        assert st["total"] == 3 and st["due_today"] == 2 and st["reviewed_today"] == 1
