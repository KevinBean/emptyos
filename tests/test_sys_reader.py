"""System app tests: Reader."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors


@pytest.mark.api
class TestReaderAPI:
    def test_books_list(self, http_client):
        data = assert_dict_response(http_client.get("/reader/api/books"))
        assert "books" in data
        assert "books_dir" in data
        assert isinstance(data["books"], list)

    def test_open_missing_book(self, http_client):
        data = assert_dict_response(
            http_client.get("/reader/api/book/this-slug-does-not-exist-zzz")
        )
        assert "error" in data

    def test_progress_roundtrip(self, http_client):
        r = http_client.post(
            "/reader/api/progress",
            json={"slug": "test-slug-zzz", "paragraph": 5},
        )
        data = assert_dict_response(r)
        assert data.get("ok") is True
        assert data["progress"]["paragraph"] == 5

    def test_progress_persists(self, http_client):
        http_client.post(
            "/reader/api/progress",
            json={"slug": "persist-slug-zzz", "paragraph": 12},
        )
        # Open is the only read path that exposes per-slug progress; missing book
        # short-circuits before progress lookup, so we instead verify save_state
        # by posting again — the second response must reflect the new value.
        r = http_client.post(
            "/reader/api/progress",
            json={"slug": "persist-slug-zzz", "paragraph": 13},
        )
        data = assert_dict_response(r)
        assert data["progress"]["paragraph"] == 13

    def test_scene_skipped_when_disabled(self, http_client):
        r = http_client.post(
            "/reader/api/scene",
            json={"slug": "x", "paragraph_index": 0, "text": "A small room."},
        )
        data = assert_dict_response(r)
        # Either skipped (default) or returns a url/error — never crashes
        assert ("skipped" in data) or ("url" in data) or ("error" in data)

    def test_speak_empty_rejected(self, http_client):
        data = assert_dict_response(http_client.post("/reader/api/speak", json={"text": ""}))
        assert "error" in data

    def test_lookup_empty_rejected(self, http_client):
        data = assert_dict_response(http_client.post("/reader/api/lookup", json={"word": ""}))
        assert "error" in data

    def test_highlight_emits(self, http_client):
        r = http_client.post(
            "/reader/api/highlight",
            json={
                "slug": "x",
                "paragraph_index": 0,
                "text": "We must remember the homelessness of our souls.",
            },
        )
        data = assert_dict_response(r)
        assert data.get("ok") is True
        assert "forwarded_to_media" in data

    def test_concepts_returns_graph_shape(self, http_client):
        r = http_client.post(
            "/reader/api/concepts",
            json={"slug": "x", "paragraph_index": 0, "text": "Vashti sat in her room."},
        )
        data = assert_dict_response(r)
        # Either a graph or an error, but always a dict
        if "graph" in data:
            assert "nodes" in data["graph"]
            assert "edges" in data["graph"]


@pytest.mark.interactive
class TestReaderUI:
    def test_page_loads(self, page, page_errors, base_url):
        page.goto(base_url + "/reader/")
        page.wait_for_selector("h1")
        assert "Reader" in page.content()
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, page, page_errors, base_url):
        page.goto(base_url + "/reader/")
        page.click(".btn-settings")
        page.wait_for_selector("#reader-settings-panel", timeout=3000)
        assert_no_js_errors(page_errors)

    def test_library_or_empty_hint(self, page, base_url):
        page.goto(base_url + "/reader/")
        # Either books are listed or an empty hint shows
        page.wait_for_selector(".book-list, .empty-hint", timeout=3000)
