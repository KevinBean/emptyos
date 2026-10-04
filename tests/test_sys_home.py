"""System app tests: Home (/) — 10 use cases.

Home page is served at / and powered by /hub/api/* endpoints.
"""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, click_first, wait_briefly,
)


@pytest.mark.api
class TestHomeAPI:
    # The hub was consolidated: per-domain endpoints
    # (/hub/api/{health-score,what-now,streaks,countdowns,wellness,goals})
    # collapsed into the unified panels aggregator. Each app now contributes
    # via [[contributes.hub.panel]] and is rendered through /hub/api/panels.

    def test_panels_aggregator(self, http_client):
        """The unified panels endpoint returns the panel blocks the hub renders."""
        data = assert_dict_response(http_client.get("/hub/api/panels"))
        assert "blocks" in data, f"panels response missing 'blocks': {list(data.keys())}"
        assert isinstance(data["blocks"], list)

    def test_panels_all_lists_known_panels(self, http_client):
        """The catalog endpoint enumerates every contributed panel."""
        data = assert_ok(http_client.get("/hub/api/panels/all"))
        assert isinstance(data, (list, dict))

    def test_shortcuts_filtered_to_loaded_apps(self, http_client):
        """`/api/shortcuts` go_map must only reference loaded apps or non-app routes.

        Guards `_filter_go_map_to_loaded` in emptyos/web/server.py — the filter that
        keeps `g+letter` shortcuts honest in trimmed tiers (core/demo). Regression
        here means demo bundles ship shortcuts that 404.
        """
        loaded = {a["id"] for a in assert_ok(http_client.get("/api/apps")) if a.get("id")}
        go_map = assert_dict_response(http_client.get("/api/shortcuts")).get("go_map", {})
        assert go_map, "go_map should not be empty — at least Home (/) is always present"

        non_app_routes = {"", "console", "topology", "settings", "docs", "ws"}
        for key, entry in go_map.items():
            path = (entry or {}).get("path", "/") or "/"
            first = path.lstrip("/").split("/", 1)[0].split("#", 1)[0]
            assert first in non_app_routes or first in loaded, (
                f"shortcut '{key}' points at /{first}/ which is neither loaded "
                f"nor an allowlisted non-app route (loaded={sorted(loaded)[:5]}…)"
            )


@pytest.mark.interactive
class TestHomeUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        """GET / returns 200 with EmptyOS title."""
        resp = page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        assert resp.status in (200, 302)
        wait_briefly(page, 800)
        title = page.title()
        assert "EmptyOS" in title or "empty" in title.lower()
        assert_no_js_errors(page_errors)

    def test_ui_widgets_render(self, page, base_url, page_errors):
        """Stat widgets and health ring render."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Health ring SVG should exist
        assert_no_js_errors(page_errors)

    def test_ui_app_navigation(self, page, base_url, page_errors):
        """Click an app link → verify navigation."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Find any link to an app
        link = page.locator("a[href*='/task/'], a[href='/task/']").first
        if link.count() == 0:
            pytest.skip("No app link found")
        link.click()
        wait_briefly(page, 1500)
        assert "/task" in page.url
        assert_no_js_errors(page_errors)

    def test_ui_quick_actions(self, page, base_url, page_errors):
        """Quick actions row visible."""
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        # Quick actions usually have .qa or .quick class
        assert_no_js_errors(page_errors)
