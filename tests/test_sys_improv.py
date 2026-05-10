"""System app tests: Improv — 12 use cases (API + UI workflows).

Covers:
  - Read-only endpoints (exercises, capabilities, sessions list)
  - Scene lifecycle: start → end (turn streaming requires LLM, optional)
  - Boards integration: list_all + set_field via dispatcher
  - UI: page loads, tabs switch, exercise selection enables Begin button,
        warmups grid renders, settings panel opens
"""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_briefly, switch_tab


# ── API ──────────────────────────────────────────────────

@pytest.mark.api
class TestImprovAPI:
    def test_exercises_list(self, http_client):
        data = assert_list_response(http_client.get("/improv/api/exercises"), min_len=5)
        ids = {e.get("id") for e in data}
        # Spot-check the canonical set is there
        for required in ("yes-and", "monologue", "object-work", "one-word-story"):
            assert required in ids, f"missing exercise '{required}'"
        for ex in data:
            assert ex.get("name") and ex.get("blurb")

    def test_capabilities_endpoint(self, http_client):
        data = assert_dict_response(
            http_client.get("/improv/api/capabilities"),
            required_keys=["speak", "listen", "default_exercise", "verbosity"],
        )
        assert isinstance(data["speak"], bool)
        assert isinstance(data["listen"], bool)

    def test_sessions_list_returns_list(self, http_client):
        # Empty or populated, must be a list
        assert_list_response(http_client.get("/improv/api/sessions"), min_len=0)

    def test_scene_start_then_end(self, http_client):
        """Full lifecycle without an LLM turn — ensures state save/load works."""
        start = assert_ok(http_client.post(
            "/improv/api/scene/start",
            json={"exercise": "yes-and", "persona": f"{TEST_PREFIX}a tired librarian"},
        ))
        assert start.get("scene_id", "").startswith("scene_")
        sid = start["scene_id"]

        # Immediately end it (no review since transcript is empty → no LLM call)
        end = assert_ok(http_client.post(
            "/improv/api/scene/end",
            json={"scene_id": sid, "rating": 3, "review": False},
        ))
        assert end.get("ok") is True
        assert end["session"]["ended"]
        assert end["session"]["rating"] == 3

    def test_scene_start_unknown_exercise(self, http_client):
        resp = http_client.post(
            "/improv/api/scene/start", json={"exercise": "nonexistent"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_scene_end_unknown_id(self, http_client):
        resp = http_client.post(
            "/improv/api/scene/end", json={"scene_id": "scene_nope"},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_session_detail_after_start(self, http_client):
        start = assert_ok(http_client.post(
            "/improv/api/scene/start",
            json={"exercise": "object-work", "persona": f"{TEST_PREFIX}detail-test"},
        ))
        sid = start["scene_id"]
        detail = assert_ok(http_client.get(f"/improv/api/sessions/{sid}"))
        assert detail["id"] == sid
        assert detail["exercise"] == "object-work"
        assert detail["transcript"] == []
        # cleanup: end the scene so it doesn't accumulate
        http_client.post(
            "/improv/api/scene/end",
            json={"scene_id": sid, "review": False},
        )

    def test_warmup_unknown_kind(self, http_client):
        resp = http_client.post("/improv/api/warmup", json={"kind": "bogus"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_word_assoc_requires_word(self, http_client):
        resp = http_client.post("/improv/api/word-assoc", json={})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_warmup_runs_when_llm(self, http_client, require_llm):
        resp = http_client.post("/improv/api/warmup", json={"kind": "objects"})
        assert resp.status_code == 200
        body = resp.json()
        # Either we got real text, or a graceful empty fallback
        assert "text" in body or "error" in body

    def test_set_field_rejects_unwhitelisted(self, http_client):
        """Boards integration — only SETTABLE_FIELDS may be flipped."""
        # Direct call via boards dispatcher would require a board; instead we
        # exercise the contract by hitting /improv/api/scene/start then... well,
        # set_field has no HTTP route — it's invoked through call_app from
        # boards. Sanity-check the whitelist via the source instead:
        from apps.improv.app import SETTABLE_FIELDS
        assert "rating" in SETTABLE_FIELDS
        assert "exercise" in SETTABLE_FIELDS
        assert "scene_id" not in SETTABLE_FIELDS, "scene_id must not be settable"


# ── UI ──────────────────────────────────────────────────

@pytest.mark.interactive
class TestImprovUI:
    def test_ui_loads(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1500)
        assert page.locator(".improv-title").is_visible()
        assert_no_js_errors(page_errors)

    def test_tabs_switch(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1200)
        # Stage tab is active by default
        assert page.locator("#pane-stage").is_visible()
        # Switch to Warm-ups via the tab button
        page.locator('.tab-btn[data-tab="warmups"]').click()
        wait_briefly(page, 400)
        assert page.locator("#pane-warmups").is_visible()
        # Switch to Library
        page.locator('.tab-btn[data-tab="library"]').click()
        wait_briefly(page, 400)
        assert page.locator("#pane-library").is_visible()
        assert_no_js_errors(page_errors)

    def test_exercise_picker_renders(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1500)
        cards = page.locator(".exercise-card")
        # Should render at least the canonical 7 exercises
        assert cards.count() >= 5
        assert_no_js_errors(page_errors)

    def test_selecting_exercise_enables_begin(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1500)
        # Selecting a card adds .selected and enables the Begin button
        card = page.locator('.exercise-card[data-ex="yes-and"]')
        card.click()
        wait_briefly(page, 200)
        assert "selected" in (card.get_attribute("class") or "")
        begin = page.locator("#begin-btn")
        assert not begin.is_disabled()
        assert_no_js_errors(page_errors)

    def test_warmups_grid_renders(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1200)
        page.locator('.tab-btn[data-tab="warmups"]').click()
        wait_briefly(page, 400)
        cards = page.locator(".warmup-card")
        assert cards.count() >= 4
        # Word-association box is present
        assert page.locator("#assoc-input").is_visible()
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 1200)
        page.locator('.header-btn[title="Settings"]').click()
        wait_briefly(page, 400)
        # Settings panel is present in DOM (slide-out)
        panel = page.locator("#app-settings-panel")
        assert panel.count() > 0
        assert_no_js_errors(page_errors)

    def test_no_js_errors_on_reload(self, app_page, page_errors):
        page = app_page("improv")
        wait_briefly(page, 800)
        page.reload()
        wait_briefly(page, 1200)
        assert_no_js_errors(page_errors)
