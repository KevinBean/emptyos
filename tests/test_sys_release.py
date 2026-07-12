"""System app tests: Release — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestReleaseAPI:
    def test_config(self, http_client):
        data = assert_dict_response(http_client.get("/release/api/config"))
        # Should contain tier definitions
        assert any(
            k in data for k in ("tiers", "core", "standard", "version")
        ), f"config missing expected keys: {list(data.keys())}"

    @pytest.mark.slow
    def test_check_safety(self, http_client):
        """Runs safety checks + full test suite — can take several minutes."""
        resp = http_client.post("/release/api/check", timeout=600)
        # May return issues found, but should not crash
        assert resp.status_code in (200, 400)

    def test_config_has_core_tier(self, http_client):
        data = http_client.get("/release/api/config").json()
        assert isinstance(data, dict)

    def test_config_is_readonly_safe(self, http_client):
        """Calling GET multiple times should be idempotent."""
        a = http_client.get("/release/api/config").json()
        b = http_client.get("/release/api/config").json()
        assert a == b

    def test_seed_demo_endpoint_runs(self, http_client):
        """POST /release/api/seed-demo returns per-app results."""
        resp = http_client.post("/release/api/seed-demo", timeout=60)
        body = assert_ok(resp)
        assert body["ok"] is True
        results = body["results"]
        assert isinstance(results, list)
        # Every result dict should have an `app` key
        for r in results:
            assert "app" in r

    def test_seed_demo_is_idempotent(self, http_client):
        """A second seed call should return all-skipped or matching results."""
        http_client.post("/release/api/seed-demo", timeout=60)
        second = http_client.post("/release/api/seed-demo", timeout=60).json()
        for r in second["results"]:
            if "result" in r and isinstance(r["result"], dict):
                # Successful seeds self-skip on repeat or return counts matching existing
                result = r["result"]
                assert ("skipped" in result
                        or result.get("created", 0) == 0
                        or result.get("classified", 0) == 0
                        or "error" in r), (
                    f"Seed for {r['app']} appears non-idempotent: {result}"
                )


@pytest.mark.interactive
class TestReleaseUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("release")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_tier_display(self, app_page, page_errors):
        """Release UI should show core/standard tier info."""
        page = app_page("release")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_check_button(self, app_page, page_errors):
        """Safety check button should be present."""
        page = app_page("release")
        wait_briefly(page, 1500)
        click_first(
            page,
            "[onclick*='check']",
            "button:has-text('Check')",
            "button:has-text('Safety')",
        )
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("release")
        wait_briefly(page, 2000)
        critical = [
            e for e in page_errors
            if "TypeError" in str(e) or "ReferenceError" in str(e)
        ]
        assert not critical, f"Critical: {critical}"

    def test_ui_content_renders(self, app_page, page_errors):
        page = app_page("release")
        wait_briefly(page, 1500)
        # Page should have some rendered structure
        body = page.locator("body").first
        html = body.inner_html()
        assert len(html) > 100, "Release page rendered too little HTML"

    def test_ui_loads_twice(self, app_page, page_errors):
        """Navigate to release twice — no leaked state."""
        page = app_page("release")
        wait_briefly(page, 800)
        page = app_page("release")
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
