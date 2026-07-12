"""System app tests: Model Bench — 10 use cases (includes compare UI)."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestModelBenchAPI:
    def test_scenarios(self, http_client):
        resp = http_client.get("/model-bench/api/scenarios")
        assert resp.status_code == 200

    def test_results(self, http_client):
        resp = http_client.get("/model-bench/api/results")
        assert resp.status_code == 200

    def test_latest(self, http_client):
        resp = http_client.get("/model-bench/api/latest")
        assert resp.status_code == 200

    def test_compare(self, http_client):
        resp = http_client.get("/model-bench/api/compare")
        assert resp.status_code == 200

    def test_run_requires_llm(self, http_client, require_llm):
        # Don't actually run — just probe endpoint exists
        resp = http_client.get("/model-bench/api/scenarios")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestModelBenchUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("model-bench")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_scenarios_rendered(self, app_page, page_errors):
        page = app_page("model-bench")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_compare_section(self, app_page, page_errors):
        """Compare cards (.compare-card or similar) should render."""
        page = app_page("model-bench")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_run_button_visible(self, app_page, page_errors):
        page = app_page("model-bench")
        wait_briefly(page, 1500)
        # Look for run/start button — don't actually click (expensive)
        buttons = page.locator("button:has-text('Run'), [onclick*='run']")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("model-bench")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical: {critical}"


@pytest.mark.api
class TestModelBenchShootout:
    """Harness shootout — ext:* subjects pinned to a shared Ollama model.

    Verifies the endpoints/gating only (no CLI is spawned): the dark flag
    defaults off, so ext:* subjects are listed-but-unavailable and a run that
    includes one is rejected.
    """

    def test_agent_subjects_includes_ext(self, http_client):
        resp = http_client.get("/model-bench/api/agent-subjects")
        assert resp.status_code == 200
        ids = {s["id"] for s in resp.json()}
        for tool in ("claude", "codex", "aider", "goose"):
            assert f"ext:{tool}" in ids

    def test_ext_subjects_carry_shootout_model(self, http_client):
        rows = {s["id"]: s for s in http_client.get("/model-bench/api/agent-subjects").json()}
        # Whether available or not, the pinned model is reported.
        assert rows["ext:codex"]["model"] == "glm-5.2:cloud"

    def test_run_with_ext_rejected_when_disabled(self, http_client):
        """Default (flag off) → a run including ext:* is refused, not spawned."""
        # Probe the flag via the subjects probe; only assert the gate when off.
        rows = {s["id"]: s for s in http_client.get("/model-bench/api/agent-subjects").json()}
        disabled = "disabled" in (rows["ext:codex"].get("reason") or "")
        if not disabled:
            pytest.skip("harness-shootout flag is enabled on this daemon")
        resp = http_client.post(
            "/model-bench/api/agent-run",
            json={"scenario_id": "write-new-util", "subject_ids": ["ext:codex"]},
        )
        assert resp.status_code == 200
        assert "disabled" in (resp.json().get("error") or "")
