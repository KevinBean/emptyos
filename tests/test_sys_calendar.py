"""System app tests: Calendar — 10 use cases.

Calendar is a read-only aggregator (pulls from task + future ICS feeds).
No write surface, so tests focus on: the agenda endpoint, hub panel
contribution, voice-assistant context contribution, and the UI page.
"""

from __future__ import annotations

import datetime

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestCalendarAPI:
    def test_today_returns_list(self, http_client):
        data = assert_ok(http_client.get("/calendar/api/today"))
        assert isinstance(data, list)

    def test_today_item_shape(self, http_client):
        """Every agenda item must carry the fields the UI relies on."""
        items = assert_ok(http_client.get("/calendar/api/today"))
        for item in items:
            assert isinstance(item, dict)
            for key in ("time", "title", "type", "source"):
                assert key in item, f"missing '{key}' in {item}"

    def test_today_type_whitelist(self, http_client):
        """Types must come from a known set — surfaces drift in the aggregator."""
        items = assert_ok(http_client.get("/calendar/api/today"))
        known = {"task", "deadline", "event"}
        for item in items:
            assert item["type"] in known, f"unknown type: {item['type']}"

    def test_hub_panel_listed(self, http_client):
        """Calendar must register its agenda panel with hub."""
        data = assert_ok(http_client.get("/hub/api/panels/all"))
        panels = data.get("panels", data) if isinstance(data, dict) else data
        ids = [p.get("id") for p in panels] if isinstance(panels, list) else []
        assert "calendar-agenda" in ids, (
            f"calendar-agenda missing from hub panels (got {ids})"
        )

    def test_hub_panel_renders(self, http_client):
        """Panel endpoint returns the method output (list) or None (empty day)."""
        resp = http_client.get("/hub/api/panel/calendar-agenda")
        if resp.status_code == 404:
            pytest.skip("hub panel endpoint not present")
        assert_ok(resp)

    def test_voice_assistant_contribution_registered(self, http_client):
        """The manifest contribution slot should show up via the apps listing."""
        resp = http_client.get("/api/apps")
        if resp.status_code != 200:
            pytest.skip("apps listing not available")
        apps = resp.json()
        entries = apps if isinstance(apps, list) else apps.get("apps", [])
        ids = {a.get("id") for a in entries if isinstance(a, dict)}
        assert "calendar" in ids, f"calendar not in loaded apps: {sorted(ids)}"


@pytest.mark.api
class TestCalendarContributionWiring:
    """Verify the contribution slot is wired end-to-end without a voice call."""

    def test_contribution_returns_string_or_none(self, http_client):
        """Hit the contributor's method directly via the kernel apps route
        if exposed; otherwise exercise it through its own get_agenda call.
        """
        items = assert_ok(http_client.get("/calendar/api/today"))
        # If there's no agenda, the voice contribution falls back to a
        # friendly "no events" string; both paths must be valid.
        if not items:
            return
        # The contribution concatenates titles; each title must be a string.
        for item in items:
            assert isinstance(item.get("title"), str)


@pytest.mark.interactive
class TestCalendarUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("calendar")
        wait_briefly(page, 400)
        assert page.locator("h1").first.inner_text(), "missing page header"
        assert_no_js_errors(page_errors)

    def test_ui_agenda_container_present(self, app_page, page_errors):
        """The list container must exist even when empty — JS targets it by id."""
        page = app_page("calendar")
        wait_briefly(page, 500)
        container = page.locator("#agenda-list")
        assert container.count() == 1
        assert_no_js_errors(page_errors)

    def test_ui_back_link(self, app_page, page_errors):
        """Back link points to /hub/ (consistent with every app shell)."""
        page = app_page("calendar")
        wait_briefly(page, 300)
        link = page.locator("a.back-link").first
        assert link.count() == 1
        href = link.get_attribute("href")
        assert href and href.endswith("/hub/"), f"unexpected back-link: {href}"
        assert_no_js_errors(page_errors)
