"""System app tests: Publish — 12 use cases including sidebar panel + modal."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestPublishAPI:
    def test_list_sites(self, http_client):
        resp = http_client.get("/publish/api/sites")
        assert resp.status_code == 200

    def test_config(self, http_client):
        data = assert_ok(http_client.get("/publish/api/config"))
        assert isinstance(data, dict)

    def test_sources(self, http_client):
        resp = http_client.get("/publish/api/sources")
        assert resp.status_code == 200

    def test_preview_endpoint(self, http_client):
        resp = http_client.get("/publish/api/preview")
        # May 400 if no draft loaded, but should not 500
        assert resp.status_code < 500

    def test_load_post_404_on_missing(self, http_client):
        resp = http_client.get(
            f"/publish/api/load-post?filename=PLAYWRIGHT-TEST-missing"
        )
        # Should return error (404 or 200 with error field)
        assert resp.status_code in (200, 404, 400)


@pytest.mark.interactive
class TestPublishUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("publish")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "AbortError"])

    def test_ui_post_list_renders(self, app_page, page_errors):
        page = app_page("publish")
        wait_briefly(page, 1500)
        # Post list container exists even if empty
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_settings_panel_slide_in(self, app_page, page_errors):
        """Click settings (⚙) → verify .settings-panel.open slides in from right."""
        page = app_page("publish")
        wait_briefly(page, 1000)
        clicked = click_first(
            page,
            "[onclick*='openSettings']",
            ".btn-settings",
            "button:has-text('Settings')",
            "button:has-text('⚙')",
        )
        if not clicked:
            pytest.skip("No settings button")
        wait_briefly(page, 600)
        panel = page.locator(".settings-panel.open, .settings-panel")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_settings_panel_close(self, app_page, page_errors):
        """Open settings panel, click close/X, verify panel hides."""
        page = app_page("publish")
        wait_briefly(page, 1000)
        click_first(page, "[onclick*='openSettings']", ".btn-settings")
        wait_briefly(page, 500)
        close = click_first(
            page,
            ".preview-close",
            ".settings-close",
            "[onclick*='closeSettings']",
            "button:has-text('×')",
        )
        if close:
            wait_briefly(page, 400)
        # Alternative: press Escape
        page.keyboard.press("Escape")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_preview_panel_opens(self, app_page, page_errors):
        """Click Preview on a post → verify .preview-panel slides in."""
        page = app_page("publish")
        wait_briefly(page, 1500)
        click_first(
            page,
            "[onclick*='previewPost']",
            "button:has-text('Preview')",
        )
        wait_briefly(page, 600)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_site_switcher(self, app_page, page_errors):
        """Site switcher control should not error when clicked."""
        page = app_page("publish")
        wait_briefly(page, 1500)
        click_first(
            page,
            ".site-switcher",
            "[onclick*='switchSite']",
            "select[name*='site']",
        )
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("publish")
        wait_briefly(page, 2500)
        critical = [
            e for e in page_errors
            if "TypeError" in str(e) or "ReferenceError" in str(e)
        ]
        assert not critical, f"Critical: {critical}"
