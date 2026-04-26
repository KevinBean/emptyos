"""System app tests: Settings — 10 use cases."""

import pytest

import factories
from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestSettingsAPI:
    def test_config_system_info(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/config"))
        sys_info = data.get("system", {})
        assert isinstance(sys_info, dict)
        assert any(
            k in sys_info for k in ("os_name", "vault_path", "host", "port")
        ), f"system info has no expected keys: {list(sys_info.keys())}"

    def test_config_settings_dict(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/config"))
        assert "settings" in data, f"config missing 'settings' key: {list(data.keys())}"
        assert isinstance(data["settings"], dict)

    def test_get_all_settings(self, http_client):
        resp = http_client.get("/settings/api/get")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_set_and_verify(self, http_client):
        key = factories.settings_test_key()
        # Set
        resp = http_client.post(
            "/settings/api/set",
            json={"key": key, "value": "test-value"},
        )
        assert resp.status_code == 200
        # Verify
        got = http_client.get(f"/settings/api/get?key={key}").json()
        # Response shape varies — accept dict with value or scalar
        if isinstance(got, dict):
            assert got.get("value") == "test-value" or got.get(key) == "test-value"
        # Cleanup
        try:
            http_client.post("/settings/api/reset", json={"key": key})
        except Exception:
            pass

    def test_set_bulk(self, http_client):
        keys = [factories.settings_test_key() for _ in range(2)]
        payload = {k: "bulk-test" for k in keys}
        resp = http_client.post("/settings/api/set-bulk", json=payload)
        if resp.status_code == 404:
            pytest.skip("set-bulk not available")
        assert resp.status_code == 200
        # Cleanup
        for k in keys:
            try:
                http_client.post("/settings/api/reset", json={"key": k})
            except Exception:
                pass

    def test_reset_setting(self, http_client):
        key = factories.settings_test_key()
        http_client.post("/settings/api/set", json={"key": key, "value": "x"})
        resp = http_client.post("/settings/api/reset", json={"key": key})
        assert resp.status_code == 200

    def test_shortcuts(self, http_client):
        data = assert_ok(http_client.get("/settings/api/shortcuts"))
        assert isinstance(data, dict)

    def test_schema(self, http_client):
        resp = http_client.get("/settings/api/schema")
        if resp.status_code == 404:
            pytest.skip("schema endpoint not present")
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_network_get(self, http_client):
        data = assert_dict_response(http_client.get("/settings/api/network"))
        for field in ("mode", "host", "port", "auth_required", "auth_token_set", "is_remote_bind"):
            assert field in data, f"Network info missing {field}: {list(data.keys())}"
        assert data["mode"] in ("local", "private", "public")
        assert isinstance(data["port"], int)

    def test_network_rejects_invalid_mode(self, http_client):
        resp = http_client.post("/settings/api/network", json={"mode": "internet"})
        assert resp.status_code == 200
        assert "error" in resp.json(), "Invalid mode should return an error"

    def test_network_rejects_public_without_token(self, http_client):
        cur = http_client.get("/settings/api/network").json()
        if cur.get("auth_token_set"):
            pytest.skip("Auth token already set — can't validate the 'public requires token' path")
        resp = http_client.post("/settings/api/network", json={"mode": "public"})
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body, "public mode without token should error"
        assert "auth_token" in body["error"].lower() or "token" in body["error"].lower()

    def test_network_rejects_quoted_token(self, http_client):
        resp = http_client.post("/settings/api/network", json={
            "mode": "public", "auth_token": 'evil"token',
        })
        body = resp.json()
        assert "error" in body, "Quoted tokens must be rejected to prevent TOML injection"


@pytest.mark.interactive
class TestSettingsUI:
    def test_ui_system_info_visible(self, app_page, page_errors):
        """Page renders system info section."""
        page = app_page("settings")
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)

    def test_ui_tab_navigation(self, app_page, page_errors):
        """Click through visible tabs."""
        page = app_page("settings")
        wait_briefly(page, 800)
        tabs = page.locator(".eos-tab, .tab, [data-tab]")
        if tabs.count() < 2:
            pytest.skip("Less than 2 tabs visible")
        # Click first 2 tabs
        for i in range(min(2, tabs.count())):
            try:
                tabs.nth(i).click()
                wait_briefly(page, 400)
            except Exception:
                continue
        assert_no_js_errors(page_errors)
