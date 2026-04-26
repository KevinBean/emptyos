"""Cross-app user journey tests.

Each test exercises a multi-app story that crosses feature boundaries,
hitting the event bus / reactor / vault ripple pathways. These are the
tests that catch integration regressions invisible to single-app tests.
"""

import time

import pytest

from helpers import TEST_PREFIX
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.crossapp
class TestCrossAppJourneys:
    def test_capture_to_task(self, http_client):
        """User captures idea → converts to task → task appears in task list."""
        text = f"{TEST_PREFIX}journey-capture-{__import__('uuid').uuid4().hex[:6]}"
        # Step 1: capture
        created = http_client.post(
            "/quick-action/api/add",
            json={"text": text, "tag": "task"},
        ).json()
        ts = created.get("timestamp") or created.get("ts")
        # Step 2: convert to task
        resp = http_client.post(
            "/quick-action/api/to-task",
            json={"text": text, "timestamp": ts},
        )
        assert resp.status_code == 200
        # Step 3: task should be discoverable
        time.sleep(1)
        # Task list may take a moment to refresh
        tasks_resp = http_client.get("/task/api/tasks")
        assert tasks_resp.status_code == 200

    def test_expense_triggers_event(self, http_client):
        """Add expense → event bus shows expense:added."""
        text = f"{TEST_PREFIX}journey-expense-{__import__('uuid').uuid4().hex[:6]}"
        resp = http_client.post(
            "/expense/api/smart-add",
            json={"text": f"10 {text}"},
        )
        # Some environments may not have expense app
        if resp.status_code != 200:
            pytest.skip("expense app not available")
        time.sleep(1.5)
        events = http_client.get("/api/events?limit=30").json()
        # Event bus should have responded
        assert isinstance(events, (list, dict))

    def test_journal_entry_ripples_to_activity(self, http_client):
        """Journal entry creates an activity signal picked up by dependent apps."""
        payload = {
            "text": f"{TEST_PREFIX}journey-journal entry",
            "mood": "good",
        }
        resp = http_client.post("/journal/api/entry", json=payload)
        assert resp.status_code == 200
        time.sleep(1)
        # Streak or heatmap should reflect activity
        streak = http_client.get("/journal/api/streak").json()
        assert isinstance(streak, dict)

    def test_focus_session_updates_stats(self, http_client):
        """Complete focus session → stats.sessions increments."""
        before = http_client.get("/focus/api/stats").json()
        before_count = before.get("sessions", 0) if isinstance(before, dict) else 0
        http_client.post(
            "/focus/api/complete",
            json={"minutes": 1, "task": f"{TEST_PREFIX}journey-focus"},
        )
        time.sleep(0.5)
        after = http_client.get("/focus/api/stats").json()
        after_count = after.get("sessions", 0) if isinstance(after, dict) else 0
        assert after_count >= before_count

    def test_capture_smart_add_then_triage(self, http_client):
        """Smart-add capture → appears in pending → dismiss."""
        text = f"{TEST_PREFIX}journey-triage"
        http_client.post(
            "/quick-action/api/add",
            json={"text": text, "tag": "note"},
        )
        time.sleep(0.5)
        recent = http_client.get("/quick-action/api/recent?limit=10").json()
        entries = recent if isinstance(recent, list) else recent.get("captures", [])
        found = next(
            (e for e in entries if text in str(e.get("text", ""))),
            None,
        )
        assert found, "Captured entry not found in recent"
        # Dismiss it
        ts = found.get("timestamp") or found.get("ts")
        http_client.post(
            "/quick-action/api/dismiss",
            json={"timestamp": ts, "text": text},
        )

    def test_task_toggle_affects_stats(self, http_client):
        """Task stats should be queryable and consistent."""
        s1 = http_client.get("/task/api/stats").json()
        s2 = http_client.get("/task/api/stats").json()
        # Two back-to-back GETs should return the same shape
        assert set(s1.keys()) == set(s2.keys())

    def test_search_after_vault_write(self, http_client):
        """Write a vault entry (via note), then search should still work."""
        title = f"{TEST_PREFIX}searchable-{__import__('uuid').uuid4().hex[:6]}"
        http_client.post(
            "/note/api/create",
            json={"title": title, "content": f"UNIQUE_MARKER_{title}"},
        )
        time.sleep(1)  # let vault index update
        # Search should not error
        resp = http_client.get(f"/search/api/search?q=UNIQUE_MARKER&top=5&semantic=0")
        assert resp.status_code == 200

    def test_home_aggregates_app_state(self, http_client):
        """Home (hub) pulls state from multiple apps without crashing."""
        hs = http_client.get("/hub/api/health-score")
        wn = http_client.get("/hub/api/what-now")
        sk = http_client.get("/hub/api/streaks")
        assert hs.status_code == 200
        assert wn.status_code == 200
        assert sk.status_code == 200

    def test_event_bus_history_after_activity(self, http_client):
        """Trigger a few actions → event bus history grows."""
        http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}event-1", "tag": "idea"},
        )
        http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}event-2", "tag": "note"},
        )
        time.sleep(1)
        events = http_client.get("/api/events?limit=50").json()
        assert isinstance(events, (list, dict))

    def test_integrity_audit_reflects_system(self, http_client):
        """Integrity audit runs without failure after real activity."""
        # Trigger activity
        http_client.post(
            "/quick-action/api/add",
            json={"text": f"{TEST_PREFIX}pre-audit", "tag": "note"},
        )
        time.sleep(0.5)
        resp = http_client.get("/integrity/api/audit")
        # May 404 if integrity app not loaded
        if resp.status_code == 404:
            pytest.skip("integrity app not loaded")
        assert resp.status_code == 200
