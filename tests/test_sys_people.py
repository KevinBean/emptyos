"""System tests for the merged people app (roster + relationships)."""

import pytest

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
            wait_briefly(page, 800)
            assert page.locator("#detail.open").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
