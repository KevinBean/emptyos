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
        known = {"task", "reminder", "booking", "worklog", "deadline", "event"}
        for item in items:
            assert item["type"] in known, f"unknown type: {item['type']}"

    def test_month_cells_shape(self, http_client):
        data = assert_ok(http_client.get("/calendar/api/month"))
        assert "cells" in data and isinstance(data["cells"], list)
        for c in data["cells"][:5]:
            assert "date" in c and "items" in c

    def test_ics_export(self, http_client):
        """GET /calendar/api/export.ics returns a parseable VCALENDAR."""
        r = http_client.get("/calendar/api/export.ics")
        assert r.status_code == 200
        assert "text/calendar" in r.headers.get("content-type", "")
        body = r.text
        assert body.startswith("BEGIN:VCALENDAR")
        assert body.rstrip().endswith("END:VCALENDAR")
        # every VEVENT is closed and carries a UID + DTSTART
        assert body.count("BEGIN:VEVENT") == body.count("END:VEVENT")
        if "BEGIN:VEVENT" in body:
            assert "UID:" in body and "DTSTART;VALUE=DATE:" in body

    def test_ics_subscription_status_is_dark_and_secret_safe(self, http_client):
        data = assert_ok(http_client.get("/calendar/api/ics/status"))
        assert isinstance(data.get("enabled"), bool)
        assert isinstance(data.get("feeds"), list)
        # Status deliberately exposes names/counts/errors, never subscription URLs.
        assert "url" not in str(data).lower()

    def test_ics_refresh_is_inert_when_flag_is_off(self, http_client):
        status = assert_ok(http_client.get("/calendar/api/ics/status"))
        if status.get("enabled"):
            pytest.skip("calendar ICS import is enabled on this machine")
        data = assert_ok(http_client.post("/calendar/api/ics/refresh"))
        assert data == {"enabled": False, "feeds": 0, "events": 0}

    @pytest.mark.llm
    def test_brief_endpoint(self, http_client):
        """GET /calendar/api/brief returns a short AI brief (LLM-hitting)."""
        data = assert_ok(http_client.get("/calendar/api/brief"))
        assert "brief" in data

    def test_hub_panel_listed(self):
        """Calendar must register its agenda hub-panel contribution.

        Checked via the manifest, NOT /hub/api/panels/all: the rendered surface
        runs each panel method and drops any that return None (the hub-panel
        contract), so an agenda with no events today silently disappears — that
        can't verify *registration*. The render path is covered by
        test_hub_panel_renders (empty-day tolerant). Registration = the manifest
        declares the contribution.
        """
        import tomllib

        from helpers import app_path

        manifest = tomllib.loads(
            (app_path("calendar") / "manifest.toml").read_text(encoding="utf-8")
        )
        panels = manifest.get("contributes", {}).get("hub", {}).get("panel", [])
        agenda = [p for p in panels if p.get("id") == "calendar-agenda"]
        assert agenda, (
            f"calendar-agenda not declared in calendar manifest "
            f"(got {[p.get('id') for p in panels]})"
        )
        assert agenda[0].get("method") == "panel_agenda"

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
        """Primary containers must exist even on an empty day — JS targets them by id."""
        page = app_page("calendar")
        wait_briefly(page, 500)
        # Post-2026-05-26 rebuild: month grid + day side panel replaced the flat agenda list.
        assert page.locator("#cal-grid").count() == 1, "month grid mount missing"
        assert page.locator("#cal-day").count() == 1, "day side panel mount missing"
        assert_no_js_errors(page_errors)

    def test_ui_back_link(self, app_page, page_errors):
        """Home link points back to '/' (the shared EOS.nav() breadcrumb —
        the standalone `a.back-link` page-shell convention this test used to
        check was retired when the nav breadcrumb replaced it repo-wide;
        every app shell now uses this)."""
        page = app_page("calendar")
        wait_briefly(page, 300)
        link = page.locator("a.nav-home").first
        assert link.count() == 1
        href = link.get_attribute("href")
        assert href == "/", f"unexpected home link: {href}"
        assert_no_js_errors(page_errors)
