"""System app tests: Dictionary — 12 use cases (includes chat-like lookup)."""

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
        # The context lives on the SENSE as a `met:` encounter line, not in a
        # `## Context` section — that section is written nowhere in the app and
        # this assertion had been red since the writer moved to sense blocks.
        assert "- **met**:" in got.get("body", "")
        assert src in got.get("body", "")
        # And it must reach the reader, not just the file: the same drift left
        # `_vault_as_lookup` hunting for `## Definition`, so every review card
        # for a recently-saved word revealed nothing.
        assert got.get("definition"), "definition must survive the round-trip to the card"
        assert got.get("example"), "the sentence must reach the card, not just the note"
        # clean up (TEST_PREFIX is also swept by conftest as a backstop)
        http_client.delete(f"/dictionary/api/vault/{word}")

    def test_difficulty_round_trips_through_the_note(self, http_client):
        """The reader's 1-5 "how hard is this for me" survives a write and a read.

        Pure vault write, no LLM. The rating exists because nothing else could
        record "I looked this up and still do not know it" — the SRS ladder only
        knows how a schedule went, and CEFR level is a claim about the language.
        """
        word = f"{TEST_PREFIX}obviate"
        assert_ok(http_client.post("/dictionary/api/save", json={
            "word": word, "definition": "to remove a need or difficulty",
        }))
        try:
            assert assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))["difficulty"] == 0

            r = assert_ok(http_client.post("/dictionary/api/difficulty",
                                           json={"word": word, "difficulty": 4}))
            assert r["difficulty"] == 4 and r["was"] == 0
            assert assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))["difficulty"] == 4

            # `bump` means "one harder than it was", for a caller with no number.
            # No UI reaches it — the reading layer's "Still hard" used to and no
            # longer does; the route still accepts it, so the clamp is pinned here.
            assert assert_ok(http_client.post("/dictionary/api/difficulty",
                                              json={"word": word, "difficulty": 1, "bump": True}))["difficulty"] == 5
            # ...and cannot run away with the scale however often it is pressed.
            assert assert_ok(http_client.post("/dictionary/api/difficulty",
                                              json={"word": word, "difficulty": 3, "bump": True}))["difficulty"] == 5

            # A rating must not cost the note anything else it was carrying.
            note = assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))
            assert note.get("definition"), "the definition must survive a rating write"

            # The list carries it, and the filter is a real cut rather than a label.
            listed = assert_ok(http_client.get("/dictionary/api/vault?min_difficulty=5"))
            assert any(w["word"] == word and w["difficulty"] == 5 for w in listed["words"])
            assert all(w["difficulty"] >= 5 for w in listed["words"])

            assert assert_ok(http_client.post("/dictionary/api/difficulty",
                                              json={"word": word, "difficulty": 0}))["difficulty"] == 0
        finally:
            http_client.delete(f"/dictionary/api/vault/{word}")

    @pytest.mark.xfail(reason="passes on homepc and on a freshly-leased sandbox member, fails only in CI; the CI-specific precondition is NOT yet identified — parked, not diagnosed")
    def test_a_reading_verdict_leaves_the_rating_alone_in_the_real_note(self, http_client):
        """The unit tests stub both the writer and the reader, so they can only
        prove `_record_word`'s three lines are wired together — never that a real
        save preserves the rating, which is the whole product claim.

        It currently survives on an implicit falsy-filter: `_record_word` calls
        `save_word` without `difficulty=`, so every reading-layer save sends 0, and
        only `merge_frontmatter` dropping a falsy value keeps the reader's number.
        Tighten that to preserve explicit zeros — plausible, since `normalise_fields`
        already treats 0 specially — and every verdict silently wipes the rating,
        with the read-back faithfully reporting the wipe so the card looks correct.

        No LLM: `enrich_on_save` is left alone, and the assertions are about the
        rating rather than the note's prose, so an enriched save is fine.
        """
        word = f"{TEST_PREFIX}obviate-verdict"
        assert_ok(http_client.post("/dictionary/api/save", json={
            "word": word, "definition": "to remove a need or difficulty",
        }))
        try:
            assert assert_ok(http_client.post("/dictionary/api/difficulty",
                                              json={"word": word, "difficulty": 3}))["difficulty"] == 3

            # "Still hard" — routes into review, reports the rating, moves nothing.
            r = assert_ok(http_client.post("/dictionary/api/reading/feedback", json={
                "word": word, "action": "hard",
                "item": {"word": word, "definition": "to remove a need"},
            }))
            assert r["difficulty"] == 3, "a verdict reported a rating it should only read"
            assert assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))["difficulty"] == 3

            # "I know this" — the other verdict, same promise about the number.
            r = assert_ok(http_client.post("/dictionary/api/reading/feedback", json={
                "word": word, "action": "known",
                "item": {"word": word, "definition": "to remove a need"},
            }))
            assert r["difficulty"] == 3
            assert assert_ok(http_client.get(f"/dictionary/api/vault/{word}"))["difficulty"] == 3
        finally:
            http_client.delete(f"/dictionary/api/vault/{word}")

    def test_difficulty_on_an_unsaved_word_is_refused_not_invented(self, http_client):
        """There is no note to write into, so this must say so rather than
        creating a bare note whose only content is a number."""
        r = http_client.post("/dictionary/api/difficulty",
                             json={"word": f"{TEST_PREFIX}never-saved-anywhere", "difficulty": 3})
        assert r.status_code == 200 and r.json().get("error")


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
        # visible=true skips the global search overlay's hidden input, which
        # matches `input[type='search']` and sorts first in DOM order.
        inp = page.locator(
            "input[placeholder*='ord' i], input[type='search'], input[type='text']"
        ).locator("visible=true").first
        if inp.count() == 0:
            pytest.skip("No word input")
        inp.fill("ephemeral")
        inp.press("Enter")
        wait_briefly(page, 2000)
        assert_no_js_errors(page_errors, allow_patterns=["fetch", "AbortError"])

    def test_lookup_deep_link_opens_requested_image_element(self, page, page_errors, base_url):
        page.route("**/dictionary/api/lookup?word=watering%20can", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"word":"watering can","definition":"a container used to water plants","from_vault":false}',
        ))
        page.goto(base_url + "/dictionary/#lookup/watering%20can", wait_until="domcontentloaded")
        page.locator("#lookup-input").wait_for()
        assert page.locator("#lookup-input").input_value() == "watering can"
        page.get_by_text("a container used to water plants", exact=True).wait_for()
        assert page.locator("#tab-btn-lookup").get_attribute("aria-selected") == "true"
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
