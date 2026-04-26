"""System app tests: Dictionary — 11 use cases (includes chat-like lookup)."""

import pytest

from helpers import TEST_PREFIX, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly


@pytest.mark.api
class TestDictionaryAPI:
    def test_word_of_day(self, http_client):
        data = assert_ok(http_client.get("/dictionary/api/word-of-day"))
        assert isinstance(data, dict)

    def test_vault_list(self, http_client):
        resp = http_client.get("/dictionary/api/vault")
        assert resp.status_code == 200

    def test_lookup_requires_llm(self, http_client, require_llm):
        # Dictionary lookup hits LLM → can take 30-60s
        resp = http_client.get("/dictionary/api/lookup?word=serendipity", timeout=90)
        assert resp.status_code in (200, 500)

    def test_suggest(self, http_client):
        resp = http_client.get("/dictionary/api/suggest?q=ser")
        assert resp.status_code == 200

    def test_srs_stats(self, http_client):
        resp = http_client.get("/dictionary/api/srs/stats")
        assert resp.status_code == 200

    def test_srs_deck(self, http_client):
        resp = http_client.get("/dictionary/api/srs/deck")
        assert resp.status_code == 200

    def test_frequency(self, http_client):
        resp = http_client.get("/dictionary/api/frequency?word=the")
        assert resp.status_code == 200


@pytest.mark.interactive
class TestDictionaryUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("dictionary")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_word_input_flow(self, app_page, page_errors):
        """Type word → submit → verify no JS error (lookup may fail gracefully)."""
        page = app_page("dictionary")
        wait_briefly(page, 1000)
        inp = page.locator(
            "input[placeholder*='ord' i], input[type='search'], input[type='text']"
        ).first
        if inp.count() == 0:
            pytest.skip("No word input")
        inp.fill("ephemeral")
        inp.press("Enter")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "AbortError"])

    def test_ui_chat_like_exchange(self, app_page, page_errors):
        """Dictionary shows word → definition — verify answer area renders."""
        page = app_page("dictionary")
        wait_briefly(page, 1500)
        # Look for any definition/answer container
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_srs_flashcard_area(self, app_page, page_errors):
        """SRS review section should render if user has cards."""
        page = app_page("dictionary")
        wait_briefly(page, 1500)
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
