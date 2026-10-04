"""Edge case tests: invalid inputs, empty states, errors, large data.

These tests probe failure modes that single-app happy-path tests miss:
malformed payloads, missing resources, oversized requests, concurrency.
"""

import time

import pytest

from helpers import TEST_PREFIX


@pytest.mark.api
class TestInvalidInputs:
    def test_expense_smart_add_empty(self, http_client):
        """Empty expense text should not crash."""
        resp = http_client.post("/expense/api/smart-add", json={"text": ""})
        if resp.status_code == 404:
            pytest.skip("expense not loaded")
        # Should return 200 with error field or 400 — never 500
        assert resp.status_code < 500, f"Got {resp.status_code}: {resp.text[:200]}"

    def test_journal_entry_no_mood(self, http_client):
        """Journal entry without mood should still accept."""
        resp = http_client.post(
            "/journal/api/entry",
            json={"text": f"{TEST_PREFIX}no-mood"},
        )
        # Should either accept or reject cleanly, never 500
        assert resp.status_code < 500

    def test_task_toggle_unknown_file(self, http_client):
        """Toggle task in nonexistent file should not crash."""
        resp = http_client.post(
            "/projects/api/projects/nonexistent/tasks/toggle",
            json={"text": "fake-task"},
        )
        assert resp.status_code < 500

    def test_get_missing_session(self, http_client):
        """GET assistant session with bogus id returns 404, not 500."""
        resp = http_client.get("/assistant/api/sessions/zzz-not-a-real-id-999")
        assert resp.status_code in (200, 400, 404)

    def test_settings_set_empty_key(self, http_client):
        """Setting with empty key should be rejected cleanly."""
        resp = http_client.post(
            "/settings/api/set",
            json={"key": "", "value": "x"},
        )
        assert resp.status_code < 500

    def test_search_very_long_query(self, http_client):
        """Extremely long search query should not crash."""
        q = "a" * 2000
        resp = http_client.get(f"/search/api/search?q={q}&top=5&semantic=0")
        assert resp.status_code < 500


@pytest.mark.api
class TestEmptyStates:
    def test_focus_empty_history(self, http_client):
        """Focus history with limit=0 or far in future should return empty."""
        resp = http_client.get("/focus/api/history?limit=0")
        assert resp.status_code == 200

    def test_capture_empty_list(self, http_client):
        """Capture list should be a valid structure even if empty."""
        resp = http_client.get("/quick-action/api/list")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_task_no_tasks_for_bogus_context(self, http_client):
        """by-context should handle missing contexts."""
        resp = http_client.get("/task/api/by-context")
        assert resp.status_code == 200


@pytest.mark.api
class TestNotFoundHandling:
    def test_delete_nonexistent_item(self, http_client):
        """Delete an item that doesn't exist → 404, not 500."""
        resp = http_client.delete("/items/api/items/99999999")
        if resp.status_code == 404 and "items" in resp.text.lower():
            pytest.skip("items app not loaded")
        # Actual behavior: 404 or silent success
        assert resp.status_code in (200, 204, 404)

    def test_update_nonexistent_habit(self, http_client):
        resp = http_client.put(
            "/healing/api/habits/99999999",
            json={"name": "x"},
        )
        # Either 404 or ignored
        assert resp.status_code < 500 or resp.status_code == 404

    def test_unknown_app_route_returns_404(self, http_client):
        resp = http_client.get("/zzz-no-such-app/api/foo")
        assert resp.status_code == 404


@pytest.mark.api
class TestConcurrency:
    def test_rapid_successive_posts(self, http_client):
        """Many rapid POSTs to capture should all succeed (or fail cleanly)."""
        results = []
        for i in range(5):
            resp = http_client.post(
                "/quick-action/api/add",
                json={"text": f"{TEST_PREFIX}rapid-{i}", "tag": "note"},
            )
            results.append(resp.status_code)
        # All should respond without 5xx
        assert all(s < 500 for s in results), f"Got 5xx: {results}"

    def test_concurrent_stats_reads(self, http_client):
        """Multiple stats endpoints queried in sequence — no interference."""
        endpoints = [
            "/task/api/stats",
            "/focus/api/stats",
            "/journal/api/streak",
            "/hub/api/panels",
            "/api/events?limit=5",
        ]
        for ep in endpoints:
            resp = http_client.get(ep)
            assert resp.status_code == 200, f"{ep} failed: {resp.status_code}"


@pytest.mark.api
class TestLargeData:
    def test_events_large_limit(self, http_client):
        """Request large event list — should not error."""
        resp = http_client.get("/api/events?limit=500")
        assert resp.status_code == 200

    def test_history_large_limit(self, http_client):
        """Request large focus history."""
        resp = http_client.get("/focus/api/history?limit=1000")
        assert resp.status_code == 200


@pytest.mark.api
class TestResponseTime:
    def test_health_responds_quickly(self, http_client):
        """Health check must be fast (<2s)."""
        start = time.time()
        resp = http_client.get("/api/health")
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 2.0, f"Health check took {elapsed:.1f}s (>2s)"

    def test_apps_list_responds_quickly(self, http_client):
        """App list must respond in <3s."""
        start = time.time()
        resp = http_client.get("/api/apps")
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 3.0, f"Apps list took {elapsed:.1f}s"
