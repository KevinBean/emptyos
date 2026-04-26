"""System app tests: voice-assistant (Aura) — 11 use cases.

Covers the API surface added this session (chat_text, confirm-intent, plan,
companions, history, debug intents) plus the studio-console UI smoke tests
(brand bar, REC button, oscilloscope canvas, settings drawer, text mode).

LLM-hitting endpoints (`/api/chat_text` with real input) are exercised only
to validate the streaming response shape — not the model output — so the
suite stays fast and provider-independent.
"""

import json

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestVoiceAssistantAPI:
    def test_companions_list(self, http_client):
        data = assert_ok(http_client.get("/voice-assistant/api/companions"))
        assert isinstance(data, list)

    def test_history_list(self, http_client):
        resp = http_client.get("/voice-assistant/api/history?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_debug_intents_shape(self, http_client):
        data = assert_dict_response(http_client.get("/voice-assistant/debug/intents"))
        assert "registry" in data and isinstance(data["registry"], list)
        assert "scoped" in data and isinstance(data["scoped"], list)
        assert "max_in_prompt" in data

    def test_capture_and_note_intents_registered(self, http_client):
        data = http_client.get("/voice-assistant/debug/intents").json()
        verbs = {entry["verb"] for entry in data["registry"]}
        assert "capture.add" in verbs, "capture.add intent should be registered"
        assert "note.create" in verbs, "note.create intent should be registered"

    def test_chat_text_empty_yields_error_event(self, http_client):
        resp = http_client.post("/voice-assistant/api/chat_text", json={"text": ""})
        assert resp.status_code == 200
        line = resp.text.splitlines()[0]
        evt = json.loads(line)
        assert evt.get("type") == "error"

    def test_confirm_intent_unknown_verb(self, http_client):
        resp = http_client.post(
            "/voice-assistant/api/confirm-intent",
            json={"verb": "definitely.not.a.real.verb", "args": {}},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_confirm_intent_bad_args(self, http_client):
        # capture.add requires text:string. Missing arg should fail validation.
        resp = http_client.post(
            "/voice-assistant/api/confirm-intent",
            json={"verb": "capture.add", "args": {}},
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_plan_empty_text(self, http_client):
        resp = http_client.post("/voice-assistant/api/plan", json={"text": ""})
        assert resp.status_code == 200
        assert "error" in resp.json()


@pytest.mark.interactive
class TestVoiceAssistantUI:
    def test_ui_loads_with_studio_console(self, app_page, page_errors):
        """Aura page renders brand bar, REC button, scope canvas, lamps."""
        page = app_page("voice-assistant")
        wait_briefly(page, 800)
        assert page.locator(".brand-name").count() > 0, "AURA brand mark missing"
        assert page.locator("#rec-btn").count() == 1, "REC button missing"
        assert page.locator("#scope-canvas").count() == 1, "Oscilloscope canvas missing"
        assert page.locator(".lamp-row.l-listen").count() == 1, "Listening lamp missing"
        assert_no_js_errors(page_errors)

    def test_ui_text_mode_toggle(self, app_page, page_errors):
        """Clicking ⌨ reveals the text input bar."""
        page = app_page("voice-assistant")
        wait_briefly(page, 600)
        text_input = page.locator("#text-input").first
        assert text_input.count() == 1
        # Hidden by default — display:none on .text-area until body.text-mode.
        assert not text_input.is_visible(), "Text input should be hidden before toggle"
        page.locator("#btn-text-toggle").click()
        wait_briefly(page, 200)
        assert text_input.is_visible(), "Text input should appear after toggle"
        assert_no_js_errors(page_errors)

    def test_capability_gap_banner_present(self, app_page, page_errors):
        """The cap-gap banner must exist in the DOM (visible only when a
        listen/think/speak provider is missing). Lighthouse for Rule-style
        graceful degradation across other apps."""
        page = app_page("voice-assistant")
        wait_briefly(page, 600)
        banner = page.locator("#cap-gap-banner")
        assert banner.count() == 1, "Capability gap banner element missing"
        fix_link = page.locator("#cap-gap-fix")
        assert fix_link.count() == 1
        href = fix_link.get_attribute("href") or ""
        assert href.startswith("/system"), f"Fix link should point at /system, got {href}"
        assert_no_js_errors(page_errors)

    def test_ui_settings_drawer_opens_with_history_tab(self, app_page, page_errors):
        """Clicking ⚙ opens the drawer with History tab active."""
        page = app_page("voice-assistant")
        wait_briefly(page, 600)
        page.locator("#btn-settings").click()
        wait_briefly(page, 300)
        drawer = page.locator("#settings-drawer.show")
        assert drawer.count() == 1, "Settings drawer did not open"
        active_tab = page.locator(".settings-tab.active").first
        assert active_tab.text_content().strip().lower() == "history"
        assert_no_js_errors(page_errors)
