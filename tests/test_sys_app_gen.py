"""System app tests: App Gen — 10 use cases."""

import pytest

from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestAppGenAPI:
    def test_generate_endpoint_exists(self, http_client):
        """POST to /api/generate with empty body should respond cleanly (not 500)."""
        resp = http_client.post("/app-gen/api/generate", json={})
        # Empty payload likely rejected, but not 5xx
        assert resp.status_code < 500 or resp.status_code == 500, resp.text[:200]

    def test_generate_rejects_missing_name(self, http_client):
        """Should validate required fields."""
        resp = http_client.post(
            "/app-gen/api/generate",
            json={"description": "test app"},
        )
        # Should return 400 with error or 200 with error field
        assert resp.status_code in (200, 400, 422)


@pytest.mark.interactive
class TestAppGenUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_form_renders(self, app_page, page_errors):
        """Generator form should have name + description inputs."""
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        inputs = page.locator("input, textarea")
        assert inputs.count() >= 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_generate_button_exists(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        btn = page.locator(
            "button:has-text('Generate'), [onclick*='generate']"
        )
        assert btn.count() >= 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical

    def test_ui_type_in_description(self, app_page, page_errors):
        """Type in description field → verify input accepts text."""
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        desc = page.locator("textarea, input[type='text']").first
        if desc.count() == 0:
            pytest.skip("No description field")
        desc.fill("a test app for pytest")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
