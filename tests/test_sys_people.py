"""System tests for the merged people app (roster + relationships)."""

import pytest

from helpers import TEST_PREFIX
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestPeopleAPI:
    def test_list(self, http_client):
        resp = http_client.get("/people/api/list")
        assert resp.status_code == 200

    def test_people_endpoint(self, http_client):
        resp = http_client.get("/people/api/people")
        assert resp.status_code == 200

    def test_search(self, http_client):
        resp = http_client.get("/people/api/search?q=test")
        assert resp.status_code == 200

    def test_frequency(self, http_client):
        resp = http_client.get("/people/api/frequency")
        assert resp.status_code == 200

    def test_due(self, http_client):
        resp = http_client.get("/people/api/due")
        assert resp.status_code == 200

    def test_stats(self, http_client):
        resp = http_client.get("/people/api/stats")
        assert resp.status_code == 200

    def test_notifications(self, http_client):
        resp = http_client.get("/people/api/notifications")
        assert resp.status_code == 200

    def test_birthdays(self, http_client):
        resp = http_client.get("/people/api/birthdays")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_match(self, http_client):
        resp = http_client.get("/people/api/match?skills=design")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_workload(self, http_client):
        resp = http_client.get("/people/api/workload")
        assert resp.status_code == 200

    def test_profile_missing_graceful(self, http_client):
        resp = http_client.get("/people/api/people/ZZZ-Nonexistent/profile")
        assert resp.status_code in (200, 404)

    def test_write_boundary_coercion(self, http_client):
        """Regression: type-drifted writes must normalize at the boundary via the
        Person(VaultModel) contract.

        Before VaultModel adoption, a boards cell / PATCH of ``active=0`` (falsy
        int) was written raw and the vault parser stored bools as the strings
        "True"/"False", so ``bool("False")`` read back as truthy — an archived
        person stayed "active". This pins the fixed behavior. Requires the daemon
        to be running the VaultModel-adopting people code (restart after the
        change for a live :9000 run).
        """
        # Unique id per run — people's DELETE only archives (no hard delete), so a
        # fixed slug would collide on re-run against the same daemon.
        import time

        uid = f"coerce-probe-{int(time.time() * 1000)}"
        name = f"{TEST_PREFIX}Coerce Probe"
        created = http_client.post(
            "/people/api/people", json={"id": uid, "name": name, "type": "internal"}
        )
        assert created.status_code == 200
        pid = created.json().get("id")
        assert pid

        try:
            # Fresh person with no `active` field → defaults active True.
            assert http_client.get(f"/people/api/people/{pid}").json().get("active") is True

            # Type-drifted PATCH: int 0 for a bool, string "30" for a float.
            patched = http_client.request(
                "PATCH",
                f"/people/api/people/{pid}",
                json={"active": 0, "capacity_hours_per_week": "30"},
            )
            assert patched.status_code == 200

            person = http_client.get(f"/people/api/people/{pid}").json()
            # Coerced on the way in: False (not truthy), 30.0 (not the "30" string).
            assert person.get("active") is False
            assert float(person.get("capacity_hours_per_week")) == 30.0

            # Inactive → excluded from the active-only roster.
            roster = http_client.get("/people/api/people?active_only=1").json()
            assert pid not in [p.get("id") for p in roster]

            # String "true" coerces back to a real bool True.
            http_client.request(
                "PATCH", f"/people/api/people/{pid}", json={"active": "true"}
            )
            assert http_client.get(f"/people/api/people/{pid}").json().get("active") is True
        finally:
            http_client.request("DELETE", f"/people/api/people/{pid}")


@pytest.mark.interactive
class TestPeopleUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("people")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_roster_renders(self, app_page, page_errors):
        page = app_page("people")
        wait_briefly(page, 2000)
        # Either a roster card or the empty state must render
        assert page.locator("#roster").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_tab_switch(self, app_page, page_errors):
        page = app_page("people")
        wait_briefly(page, 1500)
        page.locator('[data-tab="relationships"]').first.click()
        wait_briefly(page, 500)
        assert page.locator("#tab-relationships.active").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_detail_click(self, app_page, page_errors):
        page = app_page("people")
        wait_briefly(page, 2000)
        cards = page.locator("#roster .card")
        if cards.count() > 0:
            cards.first.click()
            # Condition-wait, not a fixed sleep: the detail pane opens only after
            # its workload fetch returns, and a concurrent /api/health?full=true
            # poll can stall page fetches for seconds under load.
            page.wait_for_selector("#detail.open", timeout=15000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
