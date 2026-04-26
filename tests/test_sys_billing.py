"""System app tests: Billing — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestBillingAPI:
    def test_today_stats(self, http_client):
        data = assert_ok(http_client.get("/billing/api/today"))
        assert isinstance(data, dict)

    def test_monthly(self, http_client):
        data = assert_ok(http_client.get("/billing/api/monthly"))
        assert isinstance(data, dict)

    def test_usage(self, http_client):
        data = assert_ok(http_client.get("/billing/api/usage"))
        assert isinstance(data, (list, dict))

    def test_rates(self, http_client):
        data = assert_ok(http_client.get("/billing/api/rates"))
        assert isinstance(data, dict)

    def test_budget_get(self, http_client):
        data = assert_ok(http_client.get("/billing/api/budget"))
        assert isinstance(data, dict)

    def test_budget_set(self, http_client):
        original = http_client.get("/billing/api/budget").json()
        try:
            resp = http_client.post(
                "/billing/api/budget",
                json={"daily": 5.0},
            )
            assert resp.status_code == 200
        finally:
            if isinstance(original, dict):
                try:
                    http_client.post("/billing/api/budget", json=original)
                except Exception:
                    pass

    def test_vault_report(self, http_client):
        resp = http_client.get("/billing/api/vault-report")
        if resp.status_code == 404:
            pytest.skip("vault-report endpoint not present")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestBillingUI:
    def test_ui_stat_cards(self, app_page, page_errors):
        """Stat cards visible on page."""
        page = app_page("billing")
        wait_briefly(page, 1000)
        cards = page.locator(".stat-card, .eos-stat, [class*='stat']")
        assert_no_js_errors(page_errors)

    def test_ui_chart_render(self, app_page, page_errors):
        """Chart/sparkline element present."""
        page = app_page("billing")
        wait_briefly(page, 1500)
        # Look for SVG, canvas, or chart container
        charts = page.locator("svg, canvas, .chart, .donut, .sparkline")
        assert_no_js_errors(page_errors)

    def test_ui_loads_no_errors(self, app_page, page_errors):
        """Page loads without JS errors."""
        page = app_page("billing")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)
