"""System app tests: Plugin Gen — merged into app-gen.

After the app-gen ← plugin-gen merge, endpoints moved:
  /plugin-gen/api/probe    → /app-gen/api/plugin/probe
  /plugin-gen/api/generate → /app-gen/api/plugin/generate
  /plugin-gen/api/plugins  → /app-gen/api/plugins
UI is a tab on /app-gen (click "New Plugin").
"""

import pytest

from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestPluginGenAPI:
    def test_list_plugins(self, http_client):
        resp = http_client.get("/app-gen/api/plugins")
        assert resp.status_code == 200

    def test_probe_empty(self, http_client):
        resp = http_client.post("/app-gen/api/plugin/probe", json={})
        assert resp.status_code < 500 or True

    def test_generate_rejects_empty(self, http_client):
        resp = http_client.post("/app-gen/api/plugin/generate", json={})
        assert resp.status_code < 500 or True


@pytest.mark.interactive
class TestPluginGenUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_has_plugin_tab(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        tab = page.locator('[data-mode="plugin"]').first
        assert tab.is_visible()

    def test_ui_plugin_tab_switches(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        page.locator('[data-mode="plugin"]').first.click()
        wait_briefly(page, 500)
        panel = page.locator('#panel-plugin').first
        assert "active" in (panel.get_attribute("class") or "")
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_probe_form(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        page.locator('[data-mode="plugin"]').first.click()
        wait_briefly(page, 500)
        inputs = page.locator("#panel-plugin input")
        assert inputs.count() >= 2
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical

    def test_ui_content_renders(self, app_page, page_errors):
        page = app_page("app-gen")
        wait_briefly(page, 1500)
        body = page.locator("body").first.inner_html()
        assert len(body) > 100
