"""Tier 1: Smoke tests — every app page loads without JS errors."""

import pytest
from helpers import ALL_APP_PREFIXES


@pytest.mark.smoke
class TestPageLoads:
    """Every app page should load with HTTP 200 and no JS errors."""

    @pytest.mark.parametrize("path", ALL_APP_PREFIXES, ids=[p.strip("/") for p in ALL_APP_PREFIXES])
    def test_app_page_loads(self, page, page_errors, base_url, path):
        resp = page.goto(f"{base_url}{path}", wait_until="load", timeout=15000)
        assert resp is not None, f"No response for {path}"
        assert resp.status == 200, f"{path} returned {resp.status}"
        # Page should have some visible content (some apps render via JS after load)
        page.wait_for_timeout(500)
        # No JS errors
        assert len(page_errors) == 0, f"{path} JS errors: {page_errors}"

    def test_home_page(self, page, page_errors, base_url):
        resp = page.goto(f"{base_url}/", wait_until="load", timeout=15000)
        assert resp.status in (200, 302)  # may redirect to /hub/
        assert "EmptyOS" in page.title()
        assert len(page_errors) == 0, f"Home JS errors: {page_errors}"

    def test_topology_page(self, page, page_errors, base_url):
        resp = page.goto(f"{base_url}/topology", wait_until="load", timeout=15000)
        assert resp.status == 200
        assert len(page_errors) == 0, f"Topology JS errors: {page_errors}"

    def test_swagger_docs(self, page, base_url):
        resp = page.goto(f"{base_url}/docs", wait_until="load", timeout=15000)
        assert resp.status == 200
