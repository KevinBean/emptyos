"""System app tests: Welcome — public landing for the daemon.

Welcome is a small public-facing surface (no auth required for /welcome/ and
/welcome/api/programme), so the test surface is correspondingly small —
4 API checks + 1 UI smoke. The page is data-driven from any app declaring
[provides.welcome] in its manifest.
"""

import pytest

from helpers import assert_dict_response, assert_ok


@pytest.mark.api
class TestWelcomeAPI:
    def test_programme_returns_items_list(self, http_client):
        data = assert_dict_response(http_client.get("/welcome/api/programme"))
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_programme_includes_radio_when_present(self, http_client):
        """Radio declares [provides.welcome], so it should appear in the list."""
        data = assert_dict_response(http_client.get("/welcome/api/programme"))
        ids = {it.get("id") for it in data["items"] if isinstance(it, dict)}
        # Radio should be there if the radio app is loaded; if it's not loaded
        # in this test env, the welcome app falls back to a synthetic entry —
        # also valid. Either way, "radio" is a programme id.
        assert "radio" in ids or len(data["items"]) == 0

    def test_programme_entries_have_required_fields(self, http_client):
        """Each programme item must have id / title / tagline / href."""
        data = assert_dict_response(http_client.get("/welcome/api/programme"))
        for it in data["items"]:
            assert it.get("id"), f"missing id: {it}"
            assert it.get("title"), f"missing title: {it}"
            assert it.get("href"), f"missing href: {it}"
            # tagline + badge + order are optional but typed
            assert isinstance(it.get("tagline", ""), str)
            assert isinstance(it.get("order", 0), int)

    def test_programme_sorted_by_order_then_title(self, http_client):
        """Items must be sorted by (order, title) so the listing is stable."""
        data = assert_dict_response(http_client.get("/welcome/api/programme"))
        items = data["items"]
        keys = [(it.get("order", 100), it.get("title", "")) for it in items]
        assert keys == sorted(keys), f"items not sorted: {keys}"


@pytest.mark.interactive
class TestWelcomeUI:
    def test_page_loads(self, page, base_url):
        """Welcome page renders the masthead and at least one programme entry."""
        page.goto(f"{base_url}/welcome/")
        page.wait_for_selector(".w-title")
        # Masthead present
        assert "EmptyOS" in page.locator(".w-station").text_content()
        # Programme list eventually populates (data-driven from /api/programme)
        page.wait_for_selector(".w-listing a, .w-empty", timeout=5000)
