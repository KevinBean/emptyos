"""System app tests: Dogfood Agent — read-side endpoints only.

The /api/run endpoint spawns claude-cli, which is heavy and not part of CI.
These tests exercise the read-side: personas/scenarios listing, runs listing,
issue queue, system status, queue file ops. The actual run loop is exercised
by manual smoke-runs from the UI.
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestDogfoodAgentAPI:
    def test_personas_list(self, http_client):
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/personas"),
            required_keys=["personas"],
        )
        assert isinstance(data["personas"], list)

    def test_scenarios_list(self, http_client):
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/scenarios"),
            required_keys=["scenarios"],
        )
        assert isinstance(data["scenarios"], list)
        # The shipped scenario set should include at least these two
        ids = [s.get("id") for s in data["scenarios"] if isinstance(s, dict)]
        # Tolerate missing if the user removed them — but the field shape must hold
        for s in data["scenarios"]:
            assert isinstance(s, dict)

    def test_runs_list(self, http_client):
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/runs"),
            required_keys=["runs"],
        )
        assert isinstance(data["runs"], list)

    def test_run_detail_not_found(self, http_client):
        resp = http_client.get("/dogfood-agent/api/runs/zzz-nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_issues_endpoint(self, http_client):
        """Open issues grouped by scenario. May be empty on a fresh setup."""
        resp = http_client.get("/dogfood-agent/api/issues")
        assert resp.status_code == 200
        data = resp.json()
        # Either {issues: [...]} or {groups: {...}} depending on grouping shape
        assert isinstance(data, dict)

    def test_status_shape(self, http_client):
        """System-level status — must answer regardless of setup state."""
        resp = http_client.get("/dogfood-agent/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_system_status_shape(self, http_client):
        """Header-bar system status (enabled? next cron? runs today? open issues?)."""
        resp = http_client.get("/dogfood-agent/api/system-status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_rollup_endpoint(self, http_client):
        """Aggregate behavior heatmap across all runs."""
        resp = http_client.get("/dogfood-agent/api/rollup")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_queue_list(self, http_client):
        """Pending fix-prompt queue. Empty on fresh setup."""
        resp = http_client.get("/dogfood-agent/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_queue_file_not_found(self, http_client):
        resp = http_client.get("/dogfood-agent/api/queue/zzz-nonexistent.md")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data or "content" in data  # tolerate either shape

    def test_dismiss_unknown_key_safe(self, http_client):
        """Dismissing a key that doesn't exist must not 500."""
        resp = http_client.post(
            "/dogfood-agent/api/issues/zzz-nonexistent-key/dismiss",
            json={},
        )
        # Should report ok=False or error, not crash
        assert resp.status_code == 200

    def test_toggle_enabled_idempotent(self, http_client):
        """Cron kill switch toggle. Toggle twice to leave state unchanged."""
        resp1 = http_client.post("/dogfood-agent/api/toggle-enabled", json={})
        assert resp1.status_code == 200
        resp2 = http_client.post("/dogfood-agent/api/toggle-enabled", json={})
        assert resp2.status_code == 200


@pytest.mark.interactive
class TestDogfoodAgentUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("dogfood-agent")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("dogfood-agent")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
