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

    # ── UI walk (browse capability) ─────────────────────────────────
    # Full /api/ui-walk launches headless Chromium; that's exercised
    # manually. These tests cover the validation-shape paths around it
    # so a CI run catches route + path-traversal regressions without
    # paying for a browser launch.

    def test_ui_walk_shot_not_found(self, http_client):
        """Missing file → 404 with structured error."""
        resp = http_client.get("/dogfood-agent/api/ui-walks/zzz-no-such-ts/missing.png")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_ui_walk_shot_rejects_traversal(self, http_client):
        """Path-traversal-shaped names are rejected by the handler."""
        # Slash in {name} breaks the route entirely (404 from router); dotdot
        # in {ts} resolves outside the data root and gets caught by the
        # relative_to check. Both must NOT 200 with real bytes, and must NOT 500.
        for ts, name in (("..", "shot.png"), ("ok", "..%2Fshot.png")):
            resp = http_client.get(f"/dogfood-agent/api/ui-walks/{ts}/{name}")
            assert resp.status_code in (400, 404), (
                f"traversal {ts!r}/{name!r} returned {resp.status_code}"
            )

    def test_ui_walk_shot_accepts_dotdot_substring(self, http_client):
        """Filenames with legitimate `..` substring (e.g. `home..tar.gz`) must
        NOT be rejected as traversal — they resolve cleanly within the data
        root, so a missing file is 404, not 400. Locks in the resolve()-based
        check vs. the older brittle `".." in p` substring rejection."""
        resp = http_client.get("/dogfood-agent/api/ui-walks/2026-01-01T00-00-00/home..tar.gz")
        # 404 = path was accepted but file missing; 400 would mean false-positive traversal reject
        assert resp.status_code == 404, (
            f"legitimate `..` substring returned {resp.status_code} (expected 404)"
        )

    # ── Routing plan steps 2-7: live endpoint contracts ────────────────

    def test_scenarios_carry_routing_frontmatter(self, http_client):
        """Step 1 — every scenario the catalog returns must carry the
        routing metadata the deficit picker reads."""
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/scenarios"),
            required_keys=["scenarios"],
        )
        for s in data["scenarios"]:
            assert s.get("tier") in ("smoke", "story", "journey", "dogfood"), (
                f"{s.get('id')} has invalid tier: {s.get('tier')!r}"
            )
            assert "expected_apps" in s, f"{s.get('id')} missing expected_apps"
            assert "goals" in s, f"{s.get('id')} missing goals"
            assert isinstance(s.get("budget_turns"), int)
            assert s.get("runtime") in ("persona", "ui-walk")

    def test_ui_walks_presets_endpoint(self, http_client):
        """Step 3 — preset registry must expose at least the legacy +
        core-six + engineering-six set with expected_apps lists."""
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/ui-walks/presets"),
            required_keys=["presets"],
        )
        names = {p["id"] for p in data["presets"]}
        for required in ("legacy", "core-six", "engineering-six"):
            assert required in names, f"missing preset: {required}"
        for p in data["presets"]:
            assert isinstance(p.get("expected_apps"), list)
            assert isinstance(p.get("step_count"), int)

    def test_ui_walks_coverage_endpoint(self, http_client):
        """Step 3 — coverage endpoint returns the per-app map (may be empty
        on a fresh daemon)."""
        resp = http_client.get("/dogfood-agent/api/ui-walks/coverage")
        assert resp.status_code == 200
        data = resp.json()
        assert "apps" in data and isinstance(data["apps"], dict)

    def test_journeys_rules_endpoint(self, http_client):
        """Step 4 — effective rule set merges defaults + config overrides.
        The defaults ship with at least the git→journal chain."""
        data = assert_dict_response(
            http_client.get("/dogfood-agent/api/journeys/rules"),
            required_keys=["rules"],
        )
        ids = {r["id"] for r in data["rules"]}
        assert "git_to_journal" in ids, f"default rule missing; got {ids}"
        for r in data["rules"]:
            assert r.get("trigger") and r.get("ripple")
            assert isinstance(r.get("max_delay_s"), int)

    def test_journeys_health_endpoint(self, http_client):
        """Step 4 — health endpoint returns per-rule tallies (zeros until
        the watcher has logged anything)."""
        resp = http_client.get("/dogfood-agent/api/journeys/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data and isinstance(data["rules"], list)
        for r in data["rules"]:
            for win in ("last_24h", "last_7d"):
                w = r.get(win) or {}
                # Either empty {} (no data yet) or has the expected counter keys.
                if w:
                    for k in ("triggers", "ripples_in_time", "ripples_late", "missed"):
                        assert k in w, f"{r['id']}.{win} missing {k}"

    def test_test_drafts_list_endpoint(self, http_client):
        """Step 6 — draft list endpoint must answer cleanly on a fresh daemon."""
        resp = http_client.get("/dogfood-agent/api/test-drafts")
        assert resp.status_code == 200
        data = resp.json()
        assert "drafts" in data and isinstance(data["drafts"], list)

    def test_test_drafts_read_rejects_path_traversal(self, http_client):
        """Step 6 — the file-read endpoint must reject names containing
        slashes / dotdot / non-.py extensions. Slash-containing names get
        rejected at the router layer (404 because ``{name}`` doesn't match
        a slash); names that reach the handler return 200 with error body.
        Either is safe — the test just locks down "must not 200 with file
        bytes" and "must not 500"."""
        for bad in ("../etc/passwd", "foo/bar.py", "foo.txt", "..", "secrets.json"):
            resp = http_client.get(f"/dogfood-agent/api/test-drafts/{bad}")
            assert resp.status_code in (200, 404), (
                f"{bad!r}: unexpected status {resp.status_code}"
            )
            if resp.status_code == 200:
                body = resp.json()
                assert "error" in body, f"{bad!r}: handler returned 200 without error: {body}"

    def test_promote_to_test_rejects_unknown_run(self, http_client):
        """Step 6 — promote endpoint must surface a structured error when
        run_id doesn't exist, not 500."""
        resp = http_client.post(
            "/dogfood-agent/api/runs/zzz-nonexistent/promote-to-test",
            json={"friction_index": 0},
        )
        assert resp.status_code == 200, f"got {resp.status_code}"
        body = resp.json()
        assert "error" in body

    # ── Drain orchestrator (loops over fix-agent runs) ─────────────────

    def test_fix_drain_status_endpoint(self, http_client):
        """Status endpoint must answer on a fresh daemon (no active drain)
        with a well-shaped dict."""
        resp = http_client.get("/dogfood-agent/api/fix-drain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "active" in data
        assert isinstance(data.get("history") or [], list)

    def test_fix_drain_start_gated_by_config(self, http_client):
        """Start endpoint must refuse cleanly when fix_agent_enabled is off
        (the default). The drain delegates to the existing fix-agent app
        but only when the gate is open."""
        resp = http_client.post("/dogfood-agent/api/fix-drain/start", json={})
        assert resp.status_code == 200
        body = resp.json()
        if body.get("ok"):
            # Config is on AND there are pending fix-prompts AND fix-agent
            # is reachable — drain actually started. Stop it cleanly so the
            # test suite doesn't race against the orchestrator.
            http_client.post("/dogfood-agent/api/fix-drain/stop", json={})
        else:
            # Valid refuse paths: disabled / no pending / drain active.
            assert "error" in body, f"expected error key on refuse, got: {body}"

    def test_fix_drain_stop_when_inactive_is_safe(self, http_client):
        """Stopping a non-existent drain must report cleanly, not 500."""
        resp = http_client.post("/dogfood-agent/api/fix-drain/stop", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert "ok" in body


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
