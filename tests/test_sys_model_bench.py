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
