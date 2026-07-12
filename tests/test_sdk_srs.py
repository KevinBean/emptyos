"""Unit tests for emptyos.sdk.srs — pure scheduler helpers, no daemon."""

from datetime import date, timedelta

from emptyos.sdk.srs import due_items, review_stats, sm2_schedule, streak_days

TODAY = date.today()
ISO = TODAY.isoformat()
Y1 = (TODAY - timedelta(days=1)).isoformat()
Y2 = (TODAY - timedelta(days=2)).isoformat()
GAP = (TODAY - timedelta(days=4)).isoformat()


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


class TestSm2Schedule:
    def test_first_review_due_now(self):
        item = {}
        sm2_schedule(item, 5)
        assert item["review_count"] == 1
        assert item["next_review"] == ISO  # count==0 → days==0

    def test_fail_resets_to_one_day(self):
        item = {"ease": 2.5, "review_count": 3}
        sm2_schedule(item, 1)
        assert item["next_review"] == (TODAY + timedelta(days=1)).isoformat()
        assert item["ease"] < 2.5

    def test_due_items_and_stats(self):
        items = [{"next_review": ISO}, {"next_review": GAP},
                 {"next_review": (TODAY + timedelta(days=3)).isoformat(), "review_count": 2}]
        assert len(due_items(items)) == 2
        st = review_stats(items)
        assert st["total"] == 3 and st["due_today"] == 2 and st["reviewed_today"] == 1
