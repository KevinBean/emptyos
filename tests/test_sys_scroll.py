"""System app tests: Scroll — vertical short-clip feed driven by personas."""

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_list_response, assert_ok
from page_helpers import assert_no_js_errors, click_first, wait_for_toast


@pytest.mark.api
class TestScrollAPI:
    def test_personas_list_shape(self, http_client):
        data = assert_list_response(http_client.get("/scroll/api/personas"))
        if data:
            assert "id" in data[0]
            assert "name" in data[0]

    def test_feed_list_shape(self, http_client):
        data = assert_list_response(http_client.get("/scroll/api/feed"))
        for c in data:
            assert c.get("status", "published") == "published"

    def test_drafts_list_shape(self, http_client):
        data = assert_list_response(http_client.get("/scroll/api/drafts"))
        for c in data:
            assert c.get("status") == "draft"

    def test_add_persona_round_trip(self, http_client):
        name = TEST_PREFIX + "voice-1"
        r = http_client.post(
            "/scroll/api/personas",
            json={
                "name": name,
                "voice": "calm, mid-tempo",
                "topics": ["productivity", "wellbeing"],
                "cadence": "daily",
                "system_prompt": "Be warm and concise.",
            },
        )
        body = assert_dict_response(r)
        assert "id" in body
        # Confirm it now appears in the listing
        listed = assert_list_response(http_client.get("/scroll/api/personas"))
        assert any(p.get("id") == body["id"] for p in listed)

    def test_get_persona_includes_system_prompt(self, http_client):
        name = TEST_PREFIX + "voice-2"
        body = assert_dict_response(
            http_client.post("/scroll/api/personas", json={"name": name, "system_prompt": "X"})
        )
        got = assert_dict_response(http_client.get(f"/scroll/api/personas/{body['id']}"))
        assert "system_prompt" in got

    def test_get_missing_persona_returns_error(self, http_client):
        body = assert_dict_response(http_client.get("/scroll/api/personas/__nope__"))
        assert "error" in body

    def test_next_clip_handles_empty(self, http_client):
        r = http_client.get("/scroll/api/feed/next")
        assert_ok(r)

    def test_skip_clip_event(self, http_client):
        r = http_client.post("/scroll/api/clips/__nonexistent__/skip")
        body = assert_dict_response(r)
        assert body.get("ok") is True

    def test_like_clip_event(self, http_client):
        r = http_client.post("/scroll/api/clips/__nonexistent__/like")
        body = assert_dict_response(r)
        assert body.get("ok") is True

    def test_publish_missing_clip_returns_error(self, http_client):
        body = assert_dict_response(http_client.post("/scroll/api/clips/__nope__/publish"))
        assert "error" in body

    def test_relationship_default_is_stranger(self, http_client):
        body = assert_dict_response(http_client.get("/scroll/api/relationships/a/b"))
        assert body.get("status") == "stranger"

    def test_relationship_update_round_trip(self, http_client):
        a, b = TEST_PREFIX + "rel-a", TEST_PREFIX + "rel-b"
        r = http_client.post(f"/scroll/api/relationships/{a}/{b}", json={"affinity": 0.4, "familiarity": 0.5})
        body = assert_dict_response(r)
        assert body.get("affinity") == pytest.approx(0.4)
        assert body.get("status") == "friend"

    def test_generate_unknown_shape_rejects(self, http_client):
        body = assert_dict_response(
            http_client.post("/scroll/api/generate", json={"persona_id": "scroll-warwick-clarke", "shape": "wat"})
        )
        assert "error" in body

    def test_memories_endpoint_returns_envelope(self, http_client):
        body = assert_dict_response(http_client.get("/scroll/api/memories/__nobody__"))
        assert body.get("persona") == "__nobody__"
        assert isinstance(body.get("events"), list)


@pytest.mark.interactive
class TestScrollUI:
    def test_page_loads(self, page, page_errors, base_url):
        page.goto(base_url + "/scroll/")
        page.wait_for_selector(".scroll-tab.active")
        assert_no_js_errors(page_errors)

    def test_tabs_switch(self, page, page_errors, base_url):
        page.goto(base_url + "/scroll/")
        for tab in ("drafts", "personas", "feed"):
            page.click(f'.scroll-tab[data-tab="{tab}"]')
            page.wait_for_function(
                f'document.querySelector(\'.scroll-tab[data-tab="{tab}"]\').classList.contains("active")'
            )
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, page, page_errors, base_url):
        page.goto(base_url + "/scroll/")
        page.click(".btn-settings")
        page.wait_for_selector("#scroll-settings-panel.open, .eos-settings-panel.open")
        assert_no_js_errors(page_errors)

    def test_add_persona_flow(self, page, page_errors, base_url):
        page.goto(base_url + "/scroll/")
        page.click('.scroll-tab[data-tab="personas"]')
        click_first(page, "button:has-text('+ New Persona')")
        page.fill("input[name='name']", TEST_PREFIX + "ui-persona")
        page.fill("input[name='topics']", "ai, focus")
        click_first(page, "button:has-text('Save'), button[type='submit']")
        wait_for_toast(page, "Persona added")
        assert_no_js_errors(page_errors)
