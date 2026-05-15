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
        """Path-traversal-shaped run_ids must be rejected — either by the router
        (404 after %2F is URL-decoded to / and the path doesn't match) or by the
        handler (200 with JSON error). Both are valid defenses; what's banned is
        500 or any 2xx-success that returns real data."""
        resp = http_client.get("/fix-agent/api/runs/..%2Fetc")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert "error" in resp.json()

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

    def test_run_null_filename_does_not_crash(self, http_client):
        """{"filename": null} is the JSON shape that crashes ``dict.get(K, "").strip()``
        because the default fires only on *absent* keys, not present-but-None.
        The handler must coerce None → "" before calling .strip()."""
        for body in ({"filename": None}, {"filename": ""}, None):
            resp = http_client.post("/fix-agent/api/run", json=body)
            assert resp.status_code == 200, (
                f"body={body!r} returned {resp.status_code} (expected 200 with error body)"
            )
            data = resp.json()
            assert "error" in data, f"body={body!r} did not return a structured error: {data}"

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

    # ── Repro loop (browse capability) ──────────────────────────────
    # Full /api/repro launches headless Chromium; exercised manually.
    # These tests cover the validation-shape + screenshot-serving paths
    # so CI catches route regressions without paying for a browser.

    def test_repro_missing_url(self, http_client):
        """No url → early error, no browser launch."""
        resp = http_client.post("/fix-agent/api/repro", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") == "url required"

    def test_repro_shot_not_found(self, http_client):
        """Missing screenshot file → 404."""
        resp = http_client.get("/fix-agent/api/repros/zzz-no-such-ts/foo/missing.png")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_repro_shot_rejects_traversal(self, http_client):
        """Path-traversal-shaped components are rejected."""
        for ts, label, name in (
            ("..", "lbl", "shot.png"),
            ("ok", "..", "shot.png"),
            ("ok", "lbl", "..%2Fshot.png"),
        ):
            resp = http_client.get(f"/fix-agent/api/repros/{ts}/{label}/{name}")
            assert resp.status_code in (400, 404), (
                f"traversal {ts!r}/{label!r}/{name!r} returned {resp.status_code}"
            )


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
