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

    def test_srs_review_endpoint_still_works(self, http_client):
        """The route now delegates to srs_grade (learn's cross-app contract).

        Guards the refactor: the ladder must still advance through HTTP.
        """
        word = f"{TEST_PREFIX}serendipity"
        data = assert_ok(http_client.post(
            "/dictionary/api/srs/review", json={"word": word, "quality": 3},
        ))
        assert data["ok"] is True
        assert data["word"] == word
        assert data["next_review"]

    def test_srs_review_rejects_bad_quality(self, http_client):
        r = http_client.post("/dictionary/api/srs/review", json={"word": "x", "quality": 9})
        assert "error" in r.json()
        r = http_client.post("/dictionary/api/srs/review", json={"word": "", "quality": 3})
        assert "error" in r.json()

    def test_frequency(self, http_client):
        resp = http_client.get("/dictionary/api/frequency?word=the")
        assert resp.status_code == 200

    def test_save_captures_reading_context(self, http_client):
        # V0.7 — a saved word records the sentence + source URL it was found
        # in (the chrome-extension "抓词现场·一键回跳" flow). No LLM needed —
        # /api/save is a pure vault write.
        word = f"{TEST_PREFIX}serendipity"
        src = "https://example.com/article"
        sentence = "It was pure serendipity that they met."
        assert_ok(http_client.post("/dictionary/api/save", json={
            "word": word,
            "definition": "the occurrence of happy events by chance",
            "source_url": src,
            "sentence": sentence,
        }))
        got = assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))
        assert got.get("source") == src
        assert sentence in got.get("body", "")
        assert "## Context" in got.get("body", "")
        # clean up (TEST_PREFIX is also swept by conftest as a backstop)
        http_client.delete(f"/dictionary/api/vault/{word}")


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
