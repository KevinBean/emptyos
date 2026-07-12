"""System app tests: Run Center — aggregated cross-harness run dashboard.

Run Center holds no run state of its own — it unions every harness app's
list_all() and adds the live control surface. These tests pin the aggregation
envelope + capability map (which the frontend's affordance gating depends on)
and the page shell. Run data itself is environment-dependent, so row-shape
checks skip gracefully when no runs exist.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly

CANON_PHASES = ["running", "needs-review", "done", "failed"]
HARNESSES = ["fix-agent", "app-builder", "dogfood-agent", "agent"]


@pytest.mark.api
class TestRunCenterAPI:
    def test_runs_envelope(self, http_client):
        data = assert_dict_response(http_client.get("/run-center/api/runs"))
        for key in ("runs", "missing", "summary", "phases", "phase_colors", "harnesses", "caps"):
            assert key in data, f"runs payload missing {key}: {list(data.keys())}"
        assert isinstance(data["runs"], list)
        assert isinstance(data["missing"], list)

    def test_phases_are_canonical(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        assert data["phases"] == CANON_PHASES, f"phase order drifted: {data['phases']}"

    def test_phase_colors_cover_phases(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        colors = data.get("phase_colors", {})
        for p in CANON_PHASES:
            assert p in colors, f"phase_colors missing {p}: {colors}"

    def test_harnesses_list(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        assert data["harnesses"] == HARNESSES, f"harness set drifted: {data['harnesses']}"

    def test_caps_map_shape(self, http_client):
        caps = http_client.get("/run-center/api/runs").json().get("caps", {})
        # fix-agent is the actionable harness — full gate verbs + stream.
        fix = caps.get("fix-agent", {})
        assert fix.get("stream") is True
        for verb in ("merge", "verify", "revert", "discard"):
            assert verb in fix.get("actions", []), f"fix-agent missing action {verb}"
        # agent is read-only — no gate actions.
        assert caps.get("agent", {}).get("actions") == []

    def test_summary_has_all_phases(self, http_client):
        summary = http_client.get("/run-center/api/runs").json().get("summary", {})
        phase = summary.get("phase", {})
        for p in CANON_PHASES:
            assert p in phase, f"summary.phase missing {p}: {phase}"
        assert isinstance(summary.get("total"), int)

    def test_summary_total_matches_runs(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        assert data["summary"]["total"] == len(data["runs"])

    def test_missing_subset_of_harnesses(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        for m in data["missing"]:
            assert m in HARNESSES, f"unknown harness in missing: {m}"

    def test_run_rows_normalized(self, http_client):
        data = http_client.get("/run-center/api/runs").json()
        rows = data["runs"]
        if not rows:
            pytest.skip("no runs present in this environment")
        for r in rows[:20]:
            for key in ("_key", "id", "harness", "phase", "status", "title"):
                assert key in r, f"run row missing {key}: {list(r.keys())}"
            assert r["phase"] in CANON_PHASES, f"row phase off-vocab: {r['phase']}"
            assert r["harness"] in HARNESSES, f"row harness unknown: {r['harness']}"

    def test_runs_sorted_newest_first(self, http_client):
        rows = http_client.get("/run-center/api/runs").json()["runs"]
        started = [r.get("started", "") for r in rows if r.get("started")]
        if len(started) < 2:
            pytest.skip("not enough timestamped runs to check ordering")
        assert started == sorted(started, reverse=True), "runs not newest-first by started"


@pytest.mark.interactive
class TestRunCenterUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/run-center/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1200)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_summary_and_sections(self, page, base_url, page_errors):
        page.goto(base_url + "/run-center/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        assert page.locator("#summary").count() == 1, "summary header missing"
        assert page.locator("#sections").count() == 1, "sections container missing"
        # Phase chips render from the summary payload.
        assert page.locator(".rc-chip").count() >= 1, "no summary chips rendered"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_auto_refresh_toggle(self, page, base_url, page_errors):
        page.goto(base_url + "/run-center/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1000)
        auto = page.locator("#auto")
        assert auto.count() == 1, "auto-refresh toggle missing"
        assert auto.is_checked(), "auto-refresh should default on"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
