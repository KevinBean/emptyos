"""System app tests: Fix Agent — read-side endpoints + invalid-input rejection.

The /api/run endpoint spawns claude-cli (heavy, not for CI), and the verify/
revert/merge endpoints mutate git state. These tests cover the surfaces that
are safe to hit from CI: status shape, queue proxy, runs list, not-found and
invalid-input paths, and the UI's basic load. Real-flow smoke is manual.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestFixAgentAPI:
    def test_status_shape(self, http_client):
        """Status answers regardless of worktree / claude-cli presence."""
        data = assert_dict_response(
            http_client.get("/fix-agent/api/status"),
            required_keys=["repo_root", "worktree_path", "worktree_exists",
                           "claude_available", "runs", "busy"],
        )
        assert isinstance(data["runs"], list)
        assert isinstance(data["worktree_exists"], bool)
        assert isinstance(data["busy"], bool)

    def test_queue_proxy(self, http_client):
        """Queue is a proxy of dogfood-agent's; both error and queue shapes are valid."""
        resp = http_client.get("/fix-agent/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "queue" in data or "error" in data

    def test_runs_list(self, http_client):
        data = assert_dict_response(
            http_client.get("/fix-agent/api/runs"),
            required_keys=["runs"],
        )
        assert isinstance(data["runs"], list)

    def test_run_detail_not_found(self, http_client):
        resp = http_client.get("/fix-agent/api/runs/zzz-nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_run_invalid_id_traversal(self, http_client):
        """Path-traversal-shaped run_ids must error, not 500."""
        resp = http_client.get("/fix-agent/api/runs/..%2Fetc")
        assert resp.status_code == 200

    def test_run_missing_filename(self, http_client):
        resp = http_client.post("/fix-agent/api/run", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_run_invalid_filename_traversal(self, http_client):
        """Filenames with slashes / leading-dot must be rejected."""
        for bad in ("../etc", "foo/bar.md", ".hidden.md", "x\\y.md"):
            resp = http_client.post("/fix-agent/api/run", json={"filename": bad})
            assert resp.status_code == 200
            assert "error" in resp.json(), f"accepted traversal-shaped filename: {bad!r}"

    def test_merge_unknown_run(self, http_client):
        resp = http_client.post("/fix-agent/api/runs/zzz-nonexistent/merge")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_verify_unknown_run(self, http_client):
        resp = http_client.post("/fix-agent/api/runs/zzz-nonexistent/verify")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_revert_unknown_run(self, http_client):
        resp = http_client.post("/fix-agent/api/runs/zzz-nonexistent/revert")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_discard_unknown_run(self, http_client):
        resp = http_client.post("/fix-agent/api/runs/zzz-nonexistent/discard")
        assert resp.status_code == 200
        assert "error" in resp.json()


@pytest.mark.interactive
class TestFixAgentUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("fix-agent")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("fix-agent")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical
