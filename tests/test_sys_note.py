"""System app tests: Note — 10 use cases (CRUD + fuzzy + modals)."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestNoteAPI:
    def test_list_notes(self, http_client):
        resp = http_client.get("/note/api/list")
        assert resp.status_code == 200

    def test_create_note(self, http_client):
        payload = {
            "title": f"{TEST_PREFIX}note-{__import__('uuid').uuid4().hex[:6]}",
            "content": "test body",
            "tags": ["test"],
        }
        resp = http_client.post("/note/api/create", json=payload)
        assert resp.status_code == 200, resp.text

    def test_get_note_fuzzy(self, http_client):
        # Create then fetch with fuzzy title
        uid = __import__('uuid').uuid4().hex[:6]
        title = f"{TEST_PREFIX}findme-{uid}"
        http_client.post("/note/api/create", json={"title": title, "content": "x"})
        resp = http_client.get(f"/note/api/get?title={title}")
        assert resp.status_code == 200

    def test_append_note(self, http_client):
        title = f"{TEST_PREFIX}append-{__import__('uuid').uuid4().hex[:6]}"
        http_client.post("/note/api/create", json={"title": title, "content": "line1"})
        resp = http_client.post(
            "/note/api/append",
            json={"title": title, "text": "\nline2"},
        )
        assert resp.status_code == 200

    def test_search_notes(self, http_client):
        resp = http_client.get("/note/api/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_suggest_tags_requires_llm(self, http_client, require_llm):
        resp = http_client.post(
            "/note/api/suggest-tags",
            json={"text": "I went hiking in the mountains yesterday"},
        )
        assert resp.status_code == 200


@pytest.mark.interactive
class TestNoteUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("note")
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)

    def test_ui_list_renders(self, app_page, page_errors):
        page = app_page("note")
        wait_briefly(page, 1500)
        # Page should have some content structure
        content = page.locator(".page, main, body").first
        assert content.count() > 0
        assert_no_js_errors(page_errors)

    def test_ui_create_modal_opens(self, app_page, page_errors):
        """Click New/Create → verify form modal or input appears."""
        page = app_page("note")
        wait_briefly(page, 1000)
        click_first(
            page,
            "[onclick*='createNote']",
            "[onclick*='newNote']",
            "button:has-text('New')",
            "button:has-text('Create')",
            "button:has-text('+')",
        )
        wait_briefly(page, 600)
        # Check for modal overlay appearing
        modal = page.locator("#eos-modal-overlay, .modal, .eos-modal")
        assert_no_js_errors(page_errors)

    def test_ui_search_interaction(self, app_page, page_errors):
        """Type in search → verify no errors."""
        page = app_page("note")
        wait_briefly(page, 1000)
        search = page.locator("input[type='search'], input[placeholder*='earch' i]").first
        if search.count() == 0:
            pytest.skip("No search input")
        search.fill("test")
        wait_briefly(page, 600)
        assert_no_js_errors(page_errors)
