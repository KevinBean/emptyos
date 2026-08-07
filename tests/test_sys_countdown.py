"""System app tests: Countdown — days until/since events (Days Matter clone)."""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly

EVENT_TITLE = f"{TEST_PREFIX}Mock Launch"


def _cleanup(http_client, file: str):
    if file:
        http_client.delete(f"/countdown/api/events/{file}")


@pytest.mark.api
class TestCountdownAPI:
    def test_list_events_shape(self, http_client):
        data = assert_ok(http_client.get("/countdown/api/events"))
        assert isinstance(data.get("events"), list)

    def test_add_requires_title(self, http_client):
        resp = http_client.post("/countdown/api/events", json={"date": "2099-01-01"})
        data = resp.json()
        assert data.get("error")

    def test_add_requires_date(self, http_client):
        resp = http_client.post("/countdown/api/events", json={"title": f"{TEST_PREFIX}no-date"})
        data = resp.json()
        assert data.get("error")

    def test_add_rejects_bad_date(self, http_client):
        resp = http_client.post(
            "/countdown/api/events",
            json={"title": f"{TEST_PREFIX}bad-date", "date": "not-a-date"},
        )
        data = resp.json()
        assert data.get("error")

    def test_full_lifecycle(self, http_client):
        """Create → appears in list with a resolved day count → update →
        pin → archive → delete. One event, every state transition."""
        created = assert_ok(
            http_client.post(
                "/countdown/api/events",
                json={"title": EVENT_TITLE, "date": "2099-06-15", "category": "travel"},
            )
        )
        assert created.get("ok") is True
        file = created["file"]
        try:
            assert created["category"] == "travel"
            assert created["icon"]  # auto-derived from category

            detail = assert_ok(http_client.get(f"/countdown/api/events/{file}"))
            assert detail["title"] == EVENT_TITLE
            assert isinstance(detail["days"], int)
            assert detail["days"] > 0  # far-future one-off date

            listed = assert_ok(http_client.get("/countdown/api/events"))
            assert any(e["file"] == file for e in listed["events"])

            upd = assert_ok(
                http_client.put(f"/countdown/api/events/{file}", json={"pinned": True})
            )
            assert upd.get("ok") is True
            detail = assert_ok(http_client.get(f"/countdown/api/events/{file}"))
            assert detail["pinned"] is True

            upd = assert_ok(
                http_client.put(f"/countdown/api/events/{file}", json={"archived": True})
            )
            assert upd.get("ok") is True

            # Archived events drop out of the default list …
            listed = assert_ok(http_client.get("/countdown/api/events"))
            assert not any(e["file"] == file for e in listed["events"])
            # … but are visible with ?archived=1
            listed_all = assert_ok(http_client.get("/countdown/api/events?archived=1"))
            assert any(e["file"] == file for e in listed_all["events"])
        finally:
            _cleanup(http_client, file)

        # Deleted: no longer resolvable.
        gone = http_client.get(f"/countdown/api/events/{file}")
        assert gone.json().get("error")

    def test_yearly_repeat_resolves_next_occurrence(self, http_client):
        """A yearly-repeat event always has a non-negative day count — it
        never reports a birthday that already happened this year."""
        created = assert_ok(
            http_client.post(
                "/countdown/api/events",
                json={"title": f"{TEST_PREFIX}Annual Thing", "date": "2020-01-01", "repeat": "yearly"},
            )
        )
        file = created["file"]
        try:
            detail = assert_ok(http_client.get(f"/countdown/api/events/{file}"))
            assert detail["days"] >= 0
            assert detail["occurrence"] is not None and detail["occurrence"] > 0
        finally:
            _cleanup(http_client, file)

    def test_unknown_settable_field_rejected(self, http_client):
        created = assert_ok(
            http_client.post(
                "/countdown/api/events",
                json={"title": f"{TEST_PREFIX}field-guard", "date": "2099-01-01"},
            )
        )
        file = created["file"]
        try:
            resp = http_client.put(f"/countdown/api/events/{file}", json={"not_a_real_field": "x"})
            assert resp.json().get("error")
        finally:
            _cleanup(http_client, file)

    def test_path_traversal_guard(self, http_client):
        resp = http_client.get("/countdown/api/events/..%2F..%2Fetc%2Fpasswd")
        data = resp.json()
        assert data.get("error")

    def test_stats_shape(self, http_client):
        data = assert_ok(http_client.get("/countdown/api/stats"))
        for key in ("total", "upcoming", "pinned", "archived"):
            assert key in data


@pytest.mark.interactive
class TestCountdownUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("countdown")
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)

    def test_ui_add_flow(self, app_page, page_errors, http_client):
        page = app_page("countdown")
        wait_briefly(page, 500)
        page.click("text=+ Add")
        page.wait_for_selector(".eos-modal", timeout=3000)
        page.fill("#eos-form-title", f"{TEST_PREFIX}UI Event")
        page.fill("#eos-form-date", "2099-12-25")
        page.click(".eos-form-actions .eos-btn-primary")
        wait_briefly(page, 800)
        assert_no_js_errors(page_errors)
        # Clean up whatever the UI created.
        events = http_client.get("/countdown/api/events").json().get("events", [])
        for e in events:
            if e.get("title") == f"{TEST_PREFIX}UI Event":
                http_client.delete(f"/countdown/api/events/{e['file']}")
