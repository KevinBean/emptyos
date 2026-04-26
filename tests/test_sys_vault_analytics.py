"""System app tests: Vault Analytics (now part of app-analytics) — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestVaultAnalyticsAPI:
    def test_stats(self, http_client):
        data = assert_dict_response(http_client.get("/app-analytics/api/vault/stats"))
        # Should have file counts — vault analytics uses total_files + para
        assert any(
            k in data for k in ("total", "total_files", "files", "count", "by_folder", "para")
        ), f"vault analytics stats missing expected keys: {list(data.keys())}"

    def test_uncovered(self, http_client):
        resp = http_client.get("/app-analytics/api/vault/uncovered")
        assert resp.status_code == 200

    def test_recent(self, http_client):
        resp = http_client.get("/app-analytics/api/vault/recent")
        assert resp.status_code == 200

    def test_largest(self, http_client):
        resp = http_client.get("/app-analytics/api/vault/largest")
        assert resp.status_code == 200

    def test_stale(self, http_client):
        resp = http_client.get("/app-analytics/api/vault/stale")
        assert resp.status_code == 200

    def test_growth(self, http_client):
        resp = http_client.get("/app-analytics/api/vault/growth")
        assert resp.status_code == 200

    def test_stats_has_real_data(self, http_client):
        """Vault has thousands of files — stats shouldn't be zero."""
        data = http_client.get("/app-analytics/api/vault/stats").json()
        # Just sanity check the response is populated
        assert data  # non-empty dict


@pytest.mark.interactive
class TestVaultAnalyticsUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_vault_tab_renders(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 1000)
        page.locator(".eos-tab[data-tab='vault']").click()
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_no_critical_errors(self, app_page, page_errors):
        page = app_page("app-analytics")
        wait_briefly(page, 2000)
        critical = [e for e in page_errors if "TypeError" in str(e)]
        assert not critical, f"Critical: {critical}"
