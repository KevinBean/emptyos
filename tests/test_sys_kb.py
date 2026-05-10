"""System app tests: Knowledge Base."""

import pytest

from helpers import assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestKBAPI:
    def test_domains(self, http_client):
        data = assert_ok(http_client.get("/kb/api/domains"))
        assert isinstance(data, dict)
        assert "domains" in data
        assert "total_notes" in data
        assert isinstance(data["domains"], list)

    def test_notes_list(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes"))
        assert isinstance(data, dict)
        assert "notes" in data
        assert "count" in data
        assert isinstance(data["notes"], list)

    def test_notes_filter_by_domain(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes?domain=power-systems"))
        for n in data["notes"]:
            assert n["domain"] == "power-systems"

    def test_notes_filter_by_kind(self, http_client):
        data = assert_ok(http_client.get("/kb/api/notes?kind=formula"))
        for n in data["notes"]:
            assert n["kind"] == "formula"

    def test_note_detail_missing(self, http_client):
        resp = http_client.get("/kb/api/notes/this-slug-does-not-exist-zzz")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_note_detail_real_when_seeded(self, http_client):
        # If the vault has seed notes, exercise the detail endpoint;
        # otherwise this test no-ops cleanly.
        notes = http_client.get("/kb/api/notes").json().get("notes", [])
        if not notes:
            pytest.skip("no kb notes in vault")
        slug = notes[0]["slug"]
        data = assert_ok(http_client.get(f"/kb/api/notes/{slug}"))
        assert data.get("slug") == slug
        assert "properties" in data
        assert "backlinks" in data
        assert "implemented_in_status" in data

    def test_health(self, http_client):
        data = assert_ok(http_client.get("/kb/api/health"))
        assert "broken_implemented_in" in data
        assert "formulas_missing_verification" in data
        assert "orphans" in data


@pytest.mark.interactive
class TestKBUI:
    def test_index_loads(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        assert "Knowledge Base" in page.content()
        assert_no_js_errors(page_errors)

    def test_filter_kind(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.select_option("#filter-kind", "formula")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_search_input(self, page, page_errors, base_url):
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.fill("#filter-q", "carson")
        wait_briefly(page)
        assert_no_js_errors(page_errors)

    def test_click_note_opens_detail(self, page, page_errors, base_url):
        """Clicking a note row navigates to detail without requiring a refresh."""
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        # First note row in the list
        first = page.locator(".note-row").first
        first.wait_for(state="visible", timeout=10000)
        slug = first.get_attribute("data-slug")
        assert slug, "no data-slug on first note row"
        first.click()
        wait_briefly(page)
        # Detail view shows up + URL hash reflects the slug
        assert page.locator("#detail-wrap.show").count() == 1, "detail view did not open"
        assert page.locator("#detail-head h1").is_visible(), "detail head missing"
        assert slug in page.url, f"hash not set to {slug}, url={page.url}"
        assert_no_js_errors(page_errors)

    def test_back_button_returns_to_list(self, page, page_errors, base_url):
        """The Back button clears the hash and re-shows the list view."""
        page.goto(f"{base_url}/kb/")
        wait_briefly(page)
        page.locator(".note-row").first.click()
        wait_briefly(page)
        page.locator("#detail-back").click()
        wait_briefly(page)
        assert page.locator("#detail-wrap.show").count() == 0, "detail view still showing"
        assert "#" not in page.url or page.url.endswith("#"), f"hash not cleared: {page.url}"
        assert_no_js_errors(page_errors)
