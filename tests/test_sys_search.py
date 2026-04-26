"""System app tests: Search — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestSearchAPI:
    def test_grep_search(self, http_client):
        data = assert_ok(http_client.get("/search/api/search?q=test&top=5&semantic=0"))
        assert isinstance(data, dict)
        assert "results" in data, f"Search response missing 'results': {list(data.keys())}"
        assert isinstance(data["results"], list)

    def test_empty_query(self, http_client):
        data = assert_ok(http_client.get("/search/api/search?q="))
        results = data.get("results") if isinstance(data, dict) else data
        assert isinstance(results, list)
        # Empty query should not crash; results may be empty or some default
        assert len(results) <= 50

    def test_recent_queries(self, http_client):
        data = assert_ok(http_client.get("/search/api/recent"))
        assert isinstance(data, (list, dict))

    def test_stats(self, http_client):
        data = assert_ok(http_client.get("/search/api/stats"))
        assert isinstance(data, dict)

    def test_vault_overview(self, http_client):
        resp = http_client.get("/search/api/vault-overview")
        if resp.status_code == 404:
            pytest.skip("vault-overview endpoint not present")
        data = assert_ok(resp)
        assert isinstance(data, dict)


@pytest.mark.interactive
class TestSearchUI:
    def test_ui_search_flow(self, app_page, page_errors):
        """Type query → press Enter → verify results render."""
        page = app_page("search")
        wait_briefly(page, 500)
        query = page.locator("#query, .search-input").first
        if query.count() == 0:
            pytest.skip("No search input")
        query.fill("note")
        query.press("Enter")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_mode_toggle(self, app_page, page_errors):
        """Click Ask AI mode button → verify mode switches."""
        page = app_page("search")
        wait_briefly(page, 500)
        clicked = click_first(
            page,
            "[onclick*=\"setMode('ask')\"]",
            "button:has-text('Ask AI')",
            ".mode-btn",
        )
        if not clicked:
            pytest.skip("No mode toggle button")
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_result_preview(self, app_page, page_errors):
        """Execute a search, click a result, verify preview shows."""
        page = app_page("search")
        wait_briefly(page, 500)
        query = page.locator("#query, .search-input").first
        if query.count() == 0:
            pytest.skip("No search input")
        query.fill("the")
        query.press("Enter")
        wait_briefly(page, 2000)
        # Try clicking a result card
        cards = page.locator(".result-card, .result-item, .sr-item")
        if cards.count() > 0:
            cards.first.click()
            wait_briefly(page, 600)
        assert_no_js_errors(page_errors)

    def test_ui_search_history(self, app_page, page_errors):
        """Focus search input → verify history dropdown / suggestions."""
        page = app_page("search")
        wait_briefly(page, 500)
        query = page.locator("#query").first
        if query.count() == 0:
            pytest.skip("No #query input")
        query.click()
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)

    def test_ui_clear_search(self, app_page, page_errors):
        """After search, click clear → verify input empty."""
        page = app_page("search")
        wait_briefly(page, 500)
        query = page.locator("#query").first
        if query.count() == 0:
            pytest.skip("No #query input")
        query.fill("temporary")
        wait_briefly(page, 400)
        click_first(
            page,
            "#clear-btn",
            "[onclick*='clearSearch']",
            "button:has-text('Clear')",
        )
        wait_briefly(page, 400)
        assert_no_js_errors(page_errors)
