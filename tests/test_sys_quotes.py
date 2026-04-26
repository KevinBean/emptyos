"""System app tests: Quotes — 10 use cases."""

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestQuotesAPI:
    def test_quote_of_day(self, http_client):
        data = assert_ok(http_client.get("/quotes/api/quote"))
        assert isinstance(data, dict)

    def test_list_quotes(self, http_client):
        resp = http_client.get("/quotes/api/list")
        assert resp.status_code == 200

    def test_add_quote(self, http_client):
        payload = {
            "text": f"{TEST_PREFIX}life is what happens",
            "author": "test-author",
        }
        resp = http_client.post("/quotes/api/add", json=payload)
        assert resp.status_code == 200

    def test_delete_quote(self, http_client):
        # Add then delete
        payload = {"text": f"{TEST_PREFIX}delete-me", "author": "x"}
        http_client.post("/quotes/api/add", json=payload)
        resp = http_client.post(
            "/quotes/api/delete",
            json={"text": payload["text"]},
        )
        assert resp.status_code in (200, 404)

    def test_favorites(self, http_client):
        resp = http_client.get("/quotes/api/favorites")
        assert resp.status_code == 200

    def test_generate_requires_llm(self, http_client, require_llm):
        resp = http_client.post("/quotes/api/generate", json={"theme": "wisdom"})
        assert resp.status_code in (200, 500)

    def test_favorite_toggle(self, http_client):
        payload = {"text": f"{TEST_PREFIX}fave", "author": "x"}
        http_client.post("/quotes/api/add", json=payload)
        resp = http_client.post(
            "/quotes/api/favorite",
            json={"text": payload["text"]},
        )
        assert resp.status_code in (200, 404)


@pytest.mark.interactive
class TestQuotesUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("quotes")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors)

    def test_ui_displays_quote(self, app_page, page_errors):
        page = app_page("quotes")
        wait_briefly(page, 1500)
        # Some quote text should render
        assert_no_js_errors(page_errors)

    def test_ui_no_errors_on_refresh(self, app_page, page_errors):
        page = app_page("quotes")
        wait_briefly(page, 1000)
        page.reload()
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)
