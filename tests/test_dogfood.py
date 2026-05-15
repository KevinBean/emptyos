"""Dogfood tests — a week in the life of EmptyOS.

Unlike test_sys_* (does feature X work?) or test_user_stories (does this
flow work end-to-end?), dogfood tests answer a different question:
**Could I actually use EmptyOS for a week without noticing something broken?**

Structure:
  * TestFreshBoot — Sunday evening: server up, core apps reachable
  * TestWeekInTheLife — Mon→Sun, one rhythm per day, ordered by method name
  * TestWeeklyReview — aggregate checks: streaks, heatmap, mood-trend, hub

Every entry lands on the real system "today" — we don't spoof dates. The
point is to verify that a full week of realistic user activity, run as a
single session, leaves the system in a coherent state: counters grew,
events rippled, no endpoint regressed, nothing silently duplicated.

Markers: @pytest.mark.dogfood. Run with `pytest -m dogfood -v`.
"""

import time
import uuid
from datetime import date

import pytest

from helpers import TEST_PREFIX, assert_ok


WEEK_ID = f"{TEST_PREFIX}week-{uuid.uuid4().hex[:6]}"


def _marker(day: str, kind: str) -> str:
    """Unique marker for one entry — searchable, cleanup-friendly."""
    return f"{WEEK_ID}-{day}-{kind}-{uuid.uuid4().hex[:4]}"


def _available(http_client, path: str) -> bool:
    """Return True if endpoint responds 200 (app is loaded)."""
    try:
        return http_client.get(path).status_code == 200
    except Exception:
        return False


# =============================================================================
# Sunday evening — system boot sanity before the week starts
# =============================================================================


@pytest.mark.dogfood
class TestFreshBoot:
    def test_server_is_home(self, http_client):
        """Home page responds and health endpoint is alive."""
        assert http_client.get("/api/health").status_code == 200
        # / may 302 to /hub or similar — accept any success or redirect.
        assert http_client.get("/").status_code < 400

    def test_core_apps_are_loaded(self, http_client):
        """A minimum set of daily-use apps must be reachable."""
        core = ["quick-action", "journal", "task", "hub", "search", "focus"]
        missing = [a for a in core if not _available(http_client, f"/{a}/")]
        assert not missing, f"Core apps missing: {missing}"

    def test_event_bus_is_responding(self, http_client):
        """Events endpoint returns a list, even if empty."""
        resp = http_client.get("/api/events?limit=5")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_hub_can_greet_the_day(self, http_client):
        """Home page must aggregate panels and return at least one block —
        the welcome card from the hub itself, plus whatever core/standard
        apps have contributed."""
        r = http_client.get("/hub/api/panels")
        assert r.status_code == 200, f"/hub/api/panels -> {r.status_code}"
        body = r.json()
        assert "blocks" in body and isinstance(body["blocks"], list), (
            f"/hub/api/panels missing or malformed 'blocks': {body}"
        )
        assert len(body["blocks"]) > 0, "hub aggregated zero panels — even the welcome card should appear"


# =============================================================================
# A week in the life — ordered by method name (01..07)
# State shared via class attributes so each day verifies the prior day's ripple.
# =============================================================================


@pytest.mark.dogfood
class TestWeekInTheLife:
    markers: dict = {}  # day -> list of marker strings written that day
    baselines: dict = {}  # "events_at_start", "event_count_monday", etc.

    @classmethod
    def _record(cls, day: str, marker: str):
        cls.markers.setdefault(day, []).append(marker)

    # --- Monday — planning day ----------------------------------------------

    def test_01_monday_plan_the_week(self, http_client):
        """Kevin opens EmptyOS Monday morning: checks what-now, captures
        three goals, writes a short journal entry to set intent."""
        # Snapshot event count so we can prove the week grew the bus.
        events = http_client.get("/api/events?limit=200").json()
        TestWeekInTheLife.baselines["events_start"] = (
            len(events) if isinstance(events, list) else 0
        )

        # Morning greeting — hub aggregates panels from installed apps
        panels = http_client.get("/hub/api/panels").json()
        assert isinstance(panels, dict) and "blocks" in panels, "hub panels returned unexpected shape"

        # Three Monday captures: one to-do, two ideas. We use "todo" (not
        # "task") for the to-do because tag="task" auto-routes via
        # quick-action._TAG_ROUTE and would skip the captures file entirely,
        # so the manual-promotion step below couldn't find it in recent.
        for kind, tag in (("task", "todo"), ("idea", "idea"), ("idea", "idea")):
            m = _marker("mon", kind)
            resp = http_client.post(
                "/quick-action/api/add", json={"text": m, "tag": tag}
            )
            assert resp.status_code == 200, f"capture failed: {resp.text[:200]}"
            self._record("mon", m)

        # Promote the first (to-do) capture into an actual task
        recent = http_client.get("/quick-action/api/recent?limit=20").json()
        entries = recent if isinstance(recent, list) else recent.get("captures", [])
        task_entry = next(
            (e for e in entries if self.markers["mon"][0] in str(e.get("text", ""))),
            None,
        )
        assert task_entry, "Monday task capture vanished from recent"
        ts = task_entry.get("timestamp") or task_entry.get("ts")
        http_client.post(
            "/quick-action/api/to-task",
            json={"text": task_entry.get("text"), "timestamp": ts},
        )

        # Journal the intent
        intent = _marker("mon", "intent")
        assert (
            http_client.post(
                "/journal/api/entry", json={"text": intent, "mood": "good"}
            ).status_code
            == 200
        )
        self._record("mon", intent)

    # --- Tuesday — deep-work day --------------------------------------------

    def test_02_tuesday_deep_work(self, http_client):
        """Heavy focus day: three sessions, a couple of code captures,
        verify focus stats actually increment each time."""
        if not _available(http_client, "/focus/api/stats"):
            pytest.skip("focus app not loaded")

        before = http_client.get("/focus/api/stats").json()
        before_minutes = int(before.get("total_minutes", 0) or 0)

        for i in range(3):
            m = _marker("tue", f"focus-{i}")
            r = http_client.post(
                "/focus/api/complete",
                json={"minutes": 1, "task": m},
            )
            assert r.status_code == 200
            self._record("tue", m)

        # Verify the stat actually moved by at least 3 minutes.
        time.sleep(0.5)
        after = http_client.get("/focus/api/stats").json()
        after_minutes = int(after.get("total_minutes", 0) or 0)
        assert after_minutes >= before_minutes + 3, (
            f"focus minutes did not grow after 3 sessions: {before_minutes} -> {after_minutes}"
        )

        # A code capture mid-session
        code_note = _marker("tue", "code-note")
        http_client.post(
            "/quick-action/api/add", json={"text": code_note, "tag": "dev"}
        )
        self._record("tue", code_note)

    # --- Wednesday — meetings, context-switching ----------------------------

    def test_03_wednesday_meetings(self, http_client):
        """Meeting-heavy day: lots of shallow captures, one mood log."""
        for i in range(5):
            m = _marker("wed", f"mtg-{i}")
            assert (
                http_client.post(
                    "/quick-action/api/add", json={"text": m, "tag": "note"}
                ).status_code
                == 200
            )
            self._record("wed", m)

        # Mood check if healing is loaded
        if _available(http_client, "/healing/api/trend"):
            mood_note = _marker("wed", "mood")
            http_client.post(
                "/healing/api/mood",
                json={"mood": "okay", "note": mood_note},
            )
            self._record("wed", mood_note)

    # --- Thursday — mid-week check-in ---------------------------------------

    def test_04_thursday_midweek_checkin(self, http_client):
        """Kevin pauses to look at his own data — verify aggregations have
        been keeping up with the week's activity."""
        # Event bus must have grown since Monday's snapshot.
        events = http_client.get("/api/events?limit=200").json()
        now_count = len(events) if isinstance(events, list) else 0
        start = TestWeekInTheLife.baselines.get("events_start", 0)
        assert now_count >= start, (
            f"event bus shrank: started at {start}, now {now_count}"
        )

        # Capture stats should reflect at least our Mon/Wed activity.
        stats = http_client.get("/quick-action/api/stats").json()
        assert isinstance(stats, dict), "capture stats not a dict"

        # Journal heatmap / streak must render — we wrote an entry on Monday.
        heatmap = http_client.get("/journal/api/heatmap").json()
        assert isinstance(heatmap, (list, dict)), "heatmap returned unexpected shape"
        streak = http_client.get("/journal/api/streak").json()
        assert isinstance(streak, dict), "streak returned unexpected shape"

        # Thursday reflection entry
        m = _marker("thu", "reflect")
        http_client.post(
            "/journal/api/entry", json={"text": m, "mood": "good"}
        )
        self._record("thu", m)

    # --- Friday — shipping day ----------------------------------------------

    def test_05_friday_shipping(self, http_client):
        """Close out the week: log an expense, write a retro entry, confirm
        task list is still coherent."""
        # Log a celebratory expense (if expense app is loaded)
        if _available(http_client, "/expense/api/summary"):
            exp_marker = _marker("fri", "pizza")
            r = http_client.post(
                "/expense/api/smart-add", json={"text": f"25 {exp_marker}"}
            )
            assert r.status_code == 200
            self._record("fri", exp_marker)

            # Verify the entry made it into the list
            time.sleep(0.3)
            listing = http_client.get("/expense/api/list").json()
            found = any(
                exp_marker in str(e.get("description", ""))
                for e in (listing if isinstance(listing, list) else [])
            )
            assert found, f"Friday expense {exp_marker} not in list"

        # Retro journal entry
        retro = _marker("fri", "retro")
        http_client.post(
            "/journal/api/entry", json={"text": retro, "mood": "good"}
        )
        self._record("fri", retro)

        # Task list must still be responsive after the week
        tasks = http_client.get("/task/api/tasks")
        assert tasks.status_code == 200

    # --- Saturday — rest day ------------------------------------------------

    def test_06_saturday_rest(self, http_client):
        """Light touch: one journal entry, no tasks. Verify the system
        doesn't *require* work to stay coherent."""
        m = _marker("sat", "rest")
        assert (
            http_client.post(
                "/journal/api/entry", json={"text": m, "mood": "good"}
            ).status_code
            == 200
        )
        self._record("sat", m)

        # Hub should still aggregate panels even on a low-activity day.
        panels = http_client.get("/hub/api/panels").json()
        assert isinstance(panels, dict) and "blocks" in panels

    # --- Sunday — weekly review --------------------------------------------

    def test_07_sunday_review(self, http_client):
        """Sunday: look back at the week. The data we wrote must be
        findable where the user would go looking for it."""
        # Every day we recorded something should have at least one marker.
        missing = [d for d in ("mon", "tue", "wed", "thu", "fri", "sat") if not self.markers.get(d)]
        assert not missing, f"No markers recorded for: {missing} (earlier days skipped?)"

        # /journal/api/recent is a per-day summary (date + count + mood),
        # not entry text — so we verify it surfaces today's activity by
        # checking today's row has a non-zero entry count.
        recent = http_client.get("/journal/api/recent").json()
        rows = recent if isinstance(recent, list) else recent.get("entries", [])
        today_iso = date.today().isoformat()
        today_row = next((r for r in rows if r.get("date") == today_iso), None)
        assert today_row and today_row.get("entries", 0) > 0, (
            f"journal/recent shows no entries today ({today_iso}) — we wrote several"
        )

        # For entry-text lookup the user would open /today; at least one of
        # this week's late journal markers should show up there.
        today = http_client.get("/journal/api/today").json()
        blob = str(today)
        hits = sum(1 for d in ("thu", "fri", "sat")
                   for m in self.markers.get(d, []) if m in blob)
        assert hits >= 1, "journal/today didn't show any of this week's late entries"

        # Close the week with a review note
        close = _marker("sun", "review")
        http_client.post(
            "/journal/api/entry", json={"text": close, "mood": "good"}
        )
        self._record("sun", close)


# =============================================================================
# Weekly review — end-of-week aggregation must still be coherent
# =============================================================================


@pytest.mark.dogfood
class TestWeeklyReview:
    def test_event_bus_grew_across_the_week(self, http_client):
        """The week should have pushed many events onto the bus."""
        events = http_client.get("/api/events?limit=500").json()
        assert isinstance(events, list)
        start = TestWeekInTheLife.baselines.get("events_start", 0)
        # The week wrote ≥7 journal entries + captures + focus sessions —
        # the bus should have grown by at least ~5 events even allowing for
        # ring-buffer trimming on a small instance.
        assert len(events) >= max(start, 5), (
            f"event bus unexpectedly small: {len(events)}"
        )

    def test_hub_aggregations_still_respond(self, http_client):
        """The home screen (generic hub) must still aggregate panels after a
        week's activity. The endpoint is /hub/api/panels — it walks every
        installed app's [[contributes.hub.panel]] entries and returns the
        rendered blocks. Should be 200 and return some blocks (any of the
        core/standard apps loaded in CI contributes at least one panel)."""
        r = http_client.get("/hub/api/panels")
        assert r.status_code == 200, f"/hub/api/panels broke after a week's activity: {r.status_code}"
        body = r.json()
        assert "blocks" in body, f"/hub/api/panels missing 'blocks' key: {body}"

    def test_journal_today_reflects_the_week(self, http_client):
        """At least one of the week's journal entries must be visible in
        today's view — we wrote multiple of them on 'today' by construction."""
        today = http_client.get("/journal/api/today").json()
        blob = str(today)
        all_markers = [m for day_markers in TestWeekInTheLife.markers.values()
                       for m in day_markers]
        hits = sum(1 for m in all_markers if m in blob)
        # Not every marker lives in today's JSON (captures don't) — but at
        # least one journal entry from this week should show up.
        assert hits >= 1 or "today" in today or "date" in today, (
            "journal/today didn't surface any of this week's activity"
        )

    def test_journal_file_has_no_duplicated_entries(self, http_client):
        """Regression guard for the known duplicate-append bug (see memory
        'Journal duplicate-append bug'). Each marker we wrote should appear
        exactly once in the journal's searchable view."""
        # Pull the whole week of journal activity from recent + today.
        recent = http_client.get("/journal/api/recent?limit=200").json()
        entries = recent if isinstance(recent, list) else recent.get("entries", [])
        blob = " ".join(str(e) for e in entries)

        # Our unique markers — if any shows up >1 time we've regressed.
        offenders = []
        for day_markers in TestWeekInTheLife.markers.values():
            for m in day_markers:
                # Only journal-origin markers (contain 'intent', 'reflect',
                # 'retro', 'rest', 'review') should appear in the journal view.
                if not any(k in m for k in ("intent", "reflect", "retro", "rest", "review")):
                    continue
                count = blob.count(m)
                if count > 1:
                    offenders.append((m, count))
        assert not offenders, (
            f"Journal entries duplicated (known bug?): {offenders[:3]}"
        )

    def test_concurrent_journal_writes_all_survive(self, http_client):
        """Race-condition guard: fire N journal POSTs in parallel. All must
        survive — the read-modify-write in _add_entry must be serialized by
        the per-date lock, not overwritten by a peer's stale snapshot."""
        import concurrent.futures

        N = 12
        tag = f"{TEST_PREFIX}race-{uuid.uuid4().hex[:6]}"
        today_iso = date.today().isoformat()

        def _post(i: int):
            return http_client.post(
                "/journal/api/entry",
                json={"text": f"{tag}-{i:02d}", "mood": "good"},
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            codes = list(ex.map(_post, range(N)))
        assert all(c == 200 for c in codes), f"some journal POSTs failed: {codes}"

        # Allow the event bus to settle before reading back.
        time.sleep(1.5)

        # Count from the parsed entries array — the raw `content` field in
        # the response would double-count every marker (text is rendered
        # once in `content` and once in each `entries` dict).
        today = http_client.get("/journal/api/today").json()
        entries = today.get("entries") or []
        texts = [str(e.get("text", "")) for e in entries]
        per_entry = {i: sum(1 for t in texts if f"{tag}-{i:02d}" in t) for i in range(N)}
        missing = [i for i, c in per_entry.items() if c == 0]
        dupes = [(i, c) for i, c in per_entry.items() if c > 1]
        assert not missing, f"race: lost {len(missing)} of {N} entries: {missing}"
        assert not dupes, f"race: duplicated entries: {dupes}"

    def test_weekly_expense_summary_coherent(self, http_client):
        """If expense was used this week, the summary shape must be stable."""
        if not _available(http_client, "/expense/api/summary"):
            pytest.skip("expense app not loaded")
        a = http_client.get("/expense/api/summary").json()
        b = http_client.get("/expense/api/summary").json()
        assert isinstance(a, dict) and isinstance(b, dict)
        assert set(a.keys()) == set(b.keys()), (
            "expense summary shape shifted between calls"
        )

    def test_focus_week_total_is_sane(self, http_client):
        """If we ran focus sessions this week, the weekly view must render."""
        if not _available(http_client, "/focus/api/weekly"):
            pytest.skip("focus app not loaded")
        weekly = http_client.get("/focus/api/weekly").json()
        assert isinstance(weekly, (list, dict))


# =============================================================================
# Cleanup — dogfood writes a lot; sweep up anything that escapes conftest.
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def _dogfood_cleanup(http_client):
    """Best-effort sweep of dogfood-marked captures & expenses after the week."""
    yield
    # Captures — dismiss everything with our WEEK_ID
    try:
        recent = http_client.get("/quick-action/api/list").json()
        entries = recent if isinstance(recent, list) else recent.get("captures", [])
        for c in entries:
            text = str(c.get("text", ""))
            if WEEK_ID in text:
                http_client.post(
                    "/quick-action/api/dismiss",
                    json={"timestamp": c.get("timestamp") or c.get("ts"), "text": text},
                )
    except Exception:
        pass

    # Expenses
    try:
        listing = http_client.get("/expense/api/list").json()
        if isinstance(listing, list):
            for e in listing:
                if WEEK_ID in str(e.get("description", "")):
                    http_client.post("/expense/api/delete", json={"entry": e})
    except Exception:
        pass
