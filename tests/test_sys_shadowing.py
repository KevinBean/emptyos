"""System app tests: Shadowing — passage mode (Step 4 read-aloud) + smoke.

Covers the Passages tab end-to-end: create from paste, list, get, start a
session, record reps via the text-only path (no audio + no real STT, so the
test is CI-safe and fast), complete, verify vault state. Plus a few smoke
tests for sentence mode so regressions show up here too.

The passage drill is the implementation of Step 4 of the "7 Step Method
for Changing Your Accent" — pin a 3-4 paragraph passage, drill 15-20 reps
with a slow→normal→fast speed ramp, accumulate slipped-phone marks on the
passage. See `apps/personal/shadowing/app.py::create_passage` and the
matching frontend in `pages/index.html`.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, switch_tab, wait_briefly


FIXTURE_PASSAGE = (
    "The river flows quietly through the valley. Three travellers stop to "
    "rest on the bank, their breath visible in the cold morning air.\n\n"
    "They light a small fire and share what's left of yesterday's bread. "
    "The eldest passes around a flask, and they speak of the road ahead.\n\n"
    "By dusk they will reach the next town. The path is rough, but they "
    "know it well."
)


def _create_paste(http_client, title_suffix: str = "the-river") -> dict:
    """Create a paste-source passage with a TEST_PREFIX title and return the
    server response. The cleanup_after_all fixture will reap it."""
    title = f"{TEST_PREFIX}{title_suffix}"
    resp = http_client.post(
        "/shadowing/api/passages",
        json={"source": "paste", "payload": {"title": title, "text": FIXTURE_PASSAGE}},
    )
    return assert_dict_response(resp, required_keys=["slug"])


@pytest.mark.api
class TestShadowingPassageAPI:

    def test_passages_list_shape(self, http_client):
        """GET /shadowing/api/passages returns {passages: [...]}."""
        data = assert_dict_response(http_client.get("/shadowing/api/passages"))
        assert "passages" in data, f"missing 'passages' key: {list(data.keys())}"
        assert isinstance(data["passages"], list)

    def test_create_paste_passage(self, http_client):
        """POST /shadowing/api/passages with source=paste creates a vault note."""
        created = _create_paste(http_client, "create-test")
        assert created["slug"], "no slug returned"
        assert TEST_PREFIX.lower() in created["slug"].lower(), \
            f"slug missing test prefix: {created['slug']}"

        # The new passage shows up in the list immediately
        listing = assert_dict_response(http_client.get("/shadowing/api/passages"))
        slugs = [p["slug"] for p in listing["passages"]]
        assert created["slug"] in slugs, f"new passage not in list: {slugs[:5]}"

    def test_get_passage_body(self, http_client):
        """GET /shadowing/api/passages/{slug} returns text + frontmatter fields."""
        created = _create_paste(http_client, "get-test")
        p = assert_dict_response(http_client.get(f"/shadowing/api/passages/{created['slug']}"))
        for key in ("slug", "title", "text", "rep_budget", "speed_ramp", "marked_words", "marked_phones"):
            assert key in p, f"passage missing {key}: {list(p.keys())}"
        assert FIXTURE_PASSAGE.split(".")[0] in p["text"], \
            f"text not preserved: {p['text'][:80]}"

    def test_get_missing_passage(self, http_client):
        """GET on a non-existent slug returns an error payload, not 500."""
        data = assert_ok(http_client.get("/shadowing/api/passages/does-not-exist-zzz"))
        assert data.get("error"), f"expected error for missing passage: {data}"

    def test_create_paste_requires_text(self, http_client):
        """Paste source without text errors out cleanly."""
        resp = http_client.post(
            "/shadowing/api/passages",
            json={"source": "paste", "payload": {"title": f"{TEST_PREFIX}empty"}},
        )
        data = assert_ok(resp)
        assert data.get("error"), f"expected error: {data}"

    def test_unknown_source_rejected(self, http_client):
        """Unknown source type errors out."""
        resp = http_client.post(
            "/shadowing/api/passages",
            json={"source": "weird-source", "payload": {"text": "x"}},
        )
        data = assert_ok(resp)
        assert data.get("error"), f"expected error: {data}"

    def test_session_start_and_speeds(self, http_client):
        """POST /api/passages/{slug}/session/start returns sid + speeds_by_rep."""
        created = _create_paste(http_client, "session-start")
        resp = http_client.post(
            f"/shadowing/api/passages/{created['slug']}/session/start",
            json={"quiet": True, "rep_budget": 6, "speed_ramp": [0.7, 1.0, 1.3]},
        )
        data = assert_dict_response(resp, required_keys=["sid", "rep_budget", "speed_ramp", "speeds_by_rep"])
        assert data["rep_budget"] == 6
        assert data["speed_ramp"] == [0.7, 1.0, 1.3]
        speeds = data["speeds_by_rep"]
        # Three-band ramp: 1-2 slow, 3-4 normal, 5-6 fast
        assert abs(speeds["1"] - 0.7) < 1e-6, f"rep 1 should be slow: {speeds}"
        assert abs(speeds["6"] - 1.3) < 1e-6, f"rep 6 should be fast: {speeds}"
        # Middle band — for budget=6, third=2 so reps 3,4 are normal
        assert abs(speeds["3"] - 1.0) < 1e-6, f"rep 3 should be normal: {speeds}"
        assert abs(speeds["4"] - 1.0) < 1e-6, f"rep 4 should be normal: {speeds}"

    def test_rep_text_only_path(self, http_client):
        """POST .../rep with attempt_text scores via LCS without needing audio."""
        created = _create_paste(http_client, "rep-text")
        sess = assert_ok(http_client.post(
            f"/shadowing/api/passages/{created['slug']}/session/start",
            json={"quiet": False, "rep_budget": 4},
        ))
        # Submit one rep with the exact passage text — should score ~1.0
        rep = assert_ok(http_client.post(
            f"/shadowing/api/passages/sessions/{sess['sid']}/rep",
            json={"attempt_text": FIXTURE_PASSAGE, "rep_index": 1},
        ))
        assert rep.get("i") == 1, f"unexpected rep index: {rep}"
        assert rep.get("score", 0) >= 0.9, f"exact-match should score high: {rep.get('score')}"
        assert rep.get("quiet") is False

    def test_quiet_mode_redacts_response(self, http_client):
        """Quiet sessions don't surface score/slip data in the per-rep response."""
        created = _create_paste(http_client, "rep-quiet")
        sess = assert_ok(http_client.post(
            f"/shadowing/api/passages/{created['slug']}/session/start",
            json={"quiet": True, "rep_budget": 3},
        ))
        rep = assert_ok(http_client.post(
            f"/shadowing/api/passages/sessions/{sess['sid']}/rep",
            json={"attempt_text": FIXTURE_PASSAGE, "rep_index": 1},
        ))
        # Quiet response: keep i + speed + rep_budget + quiet=True flag, no score
        assert rep.get("quiet") is True
        assert "score" not in rep, f"quiet rep should not surface score: {rep}"

    def test_complete_session_promotes_to_vault(self, http_client):
        """Completing a session bumps total_reps + total_sessions on the passage."""
        created = _create_paste(http_client, "complete")
        before = assert_ok(http_client.get(f"/shadowing/api/passages/{created['slug']}"))
        assert before.get("total_reps", 0) == 0

        sess = assert_ok(http_client.post(
            f"/shadowing/api/passages/{created['slug']}/session/start",
            json={"quiet": True, "rep_budget": 3},
        ))
        for i in range(1, 4):
            assert_ok(http_client.post(
                f"/shadowing/api/passages/sessions/{sess['sid']}/rep",
                json={"attempt_text": FIXTURE_PASSAGE, "rep_index": i},
            ))
        done = assert_ok(http_client.post(
            f"/shadowing/api/passages/sessions/{sess['sid']}/complete",
            json={"keep_audio": True},
        ))
        assert done.get("completed") is True, f"complete didn't flip completed flag: {done}"

        after = assert_ok(http_client.get(f"/shadowing/api/passages/{created['slug']}"))
        assert after["total_reps"] == 3, f"total_reps should be 3: {after['total_reps']}"
        assert after["total_sessions"] == 1, f"total_sessions should be 1: {after['total_sessions']}"
        assert after["last_drilled"], "last_drilled should be set"

    def test_delete_passage(self, http_client):
        """DELETE /shadowing/api/passages/{slug} removes the vault note."""
        created = _create_paste(http_client, "delete")
        slug = created["slug"]
        resp = http_client.request("DELETE", f"/shadowing/api/passages/{slug}")
        data = assert_ok(resp)
        assert data.get("ok") is True, f"delete failed: {data}"
        # Subsequent GET errors
        gone = assert_ok(http_client.get(f"/shadowing/api/passages/{slug}"))
        assert gone.get("error"), f"passage should be gone: {gone}"


@pytest.mark.api
class TestShadowingSentenceModeSmoke:
    """Smoke tests for the existing sentence mode — keep passage-mode changes
    from regressing the original drill flow."""

    def test_stats_shape(self, http_client):
        data = assert_dict_response(http_client.get("/shadowing/api/stats"))
        for key in ("total", "perfect", "avg_score"):
            assert key in data, f"stats missing {key}: {list(data.keys())}"

    def test_history_returns_list(self, http_client):
        assert_list_response(http_client.get("/shadowing/api/history?limit=5"))

    def test_sentences_endpoint(self, http_client):
        """GET /api/sentences returns either dict (all) or filtered dict."""
        data = assert_ok(http_client.get("/shadowing/api/sentences"))
        assert isinstance(data, dict), f"sentences endpoint should return dict: {type(data)}"


@pytest.mark.interactive
class TestShadowingPassageUI:
    """Playwright tests for the Passages tab. These exercise the visual
    affordances added in `pages/index.html` — tab switch, list render,
    paste modal."""

    def test_passages_tab_switch(self, app_page, page_errors):
        """Click the Passages tab → empty state or list renders."""
        page = app_page("shadowing")
        wait_briefly(page, 600)
        switched = switch_tab(page, "passages")
        if not switched:
            pytest.skip("passages tab not found — UI may be mid-load")
        wait_briefly(page, 700)
        # Either the empty-state CTA row or a passage card list is visible
        has_cta = page.locator(".passage-empty-actions").count() > 0
        has_card = page.locator(".passage-card").count() > 0
        assert has_cta or has_card, "neither empty CTA nor passage cards visible"
        assert_no_js_errors(page_errors)

    def test_paste_modal_opens(self, app_page, page_errors):
        """Click '+ Paste text' → modal with title + textarea appears."""
        page = app_page("shadowing")
        wait_briefly(page, 600)
        switch_tab(page, "passages")
        wait_briefly(page, 500)
        # Click via the inline onclick — the button text is "+ Paste text"
        btn = page.locator("button:has-text('+ Paste text')").first
        if btn.count() == 0:
            pytest.skip("paste button not visible (already has passages?)")
        btn.click()
        wait_briefly(page, 400)
        assert page.locator("#pp-text").count() > 0, "paste-text textarea did not appear"
        assert_no_js_errors(page_errors)

    def test_settings_panel_has_passage_fields(self, app_page, page_errors):
        """Click ⚙ → settings panel shows the three passage-mode fields.
        Scoped to the panel element (`#shadowing-settings-panel`) — scraping
        `body.inner_text()` catches whatever else is open (smart-dot dial,
        assistant FAB, etc.) and produces false negatives. The panel id comes
        from the `EOS_UI.settingsPanel({id: 'shadowing-settings-panel'})` call
        at the bottom of `pages/index.html`."""
        page = app_page("shadowing")
        wait_briefly(page, 600)
        gear = page.locator(".eos-header button[title='Settings']").first
        if gear.count() == 0:
            pytest.skip("settings button not visible")
        gear.click()
        panel = page.locator("#shadowing-settings-panel")
        # Wait for the panel to mount (settingsPanel is lazy-rendered on first open)
        panel.wait_for(state="visible", timeout=4000)
        # Labels are CSS-transformed to uppercase in the rendered DOM, so
        # `inner_text` returns "PASSAGE REP BUDGET" not the source-case
        # "Passage Rep Budget". Compare case-insensitively.
        panel_text = panel.inner_text().lower()
        assert "passage rep budget" in panel_text, \
            f"rep-budget field missing — panel text: {panel_text[:300]}"
        assert "passage speed ramp" in panel_text, "speed-ramp field missing"
        assert "quiet mode" in panel_text, "quiet-mode field missing"
        assert_no_js_errors(page_errors)
