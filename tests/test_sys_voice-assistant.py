"""System app tests: voice-assistant (Aura) — 11 use cases.

Covers the API surface added this session (chat_text, confirm-intent, plan,
companions, history, debug intents, device_turn) plus the studio-console UI
smoke tests (brand bar, REC button, oscilloscope canvas, settings drawer, text
mode). `device_turn` is the headless-satellite endpoint — its LLM-free shape
(JSON, not NDJSON) is pinned here; the full round-trip is sandbox-verified.

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

    def test_warmup_reports_a_healthy_warm(self, http_client):
        """The page fires this on load so the first utterance skips the cold
        quality-context build (~23s cold vs ~3s warm). `failed` must be 0 —
        the gather swallows contributor errors, so a silently-cold cache would
        otherwise keep reporting "warmed" forever."""
        resp = http_client.post("/voice-assistant/api/warmup", json={})
        body = assert_dict_response(resp)
        assert body.get("ok") is True
        assert body.get("status") in ("warmed", "already-warming")
        if body.get("status") == "warmed":
            assert body.get("failed") == 0, f"warmup had failing builds: {body}"

    def test_warmup_is_idempotent_and_cheap_once_warm(self, http_client):
        """Second call must not re-pay the cold build — it either coalesces onto
        an in-flight warm or returns fast off the caches the first call filled."""
        http_client.post("/voice-assistant/api/warmup", json={})
        resp = http_client.post("/voice-assistant/api/warmup", json={})
        body = assert_dict_response(resp)
        assert body.get("ok") is True
        if body.get("status") == "warmed":
            assert body.get("ms", 99999) < 2000, f"warm call still slow: {body}"

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

    def test_device_turn_no_audio_returns_json_error(self, http_client):
        # The device endpoint returns ONE JSON object (not an NDJSON stream) so a
        # thin satellite never parses event streams. No audio → device-shaped error.
        resp = http_client.post("/voice-assistant/api/device_turn", data={"messages": "[]"})
        assert resp.status_code == 200
        body = resp.json()  # parsing as a single JSON object proves the non-NDJSON shape
        assert body.get("error") == "no audio"

    def test_device_turn_stream_no_audio_returns_ndjson_error(self, http_client):
        # The streaming fast-path returns an NDJSON event stream (not one JSON
        # object) so the puck plays sentence 1 while the rest still generates.
        # No audio → a single {"type":"error"} event line.
        import json as _json

        resp = http_client.post(
            "/voice-assistant/api/device_turn_stream", data={"messages": "[]", "fast": "1"}
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers.get("content-type", "")
        first = resp.text.strip().splitlines()[0]
        ev = _json.loads(first)
        assert ev.get("type") == "error"
        assert ev.get("error") == "no audio"

    def test_device_turn_bad_messages_tolerated(self, http_client):
        # Malformed messages JSON must not 500 — it falls back to [] and still
        # reaches the no-audio guard, returning the device-shaped error dict.
        resp = http_client.post(
            "/voice-assistant/api/device_turn", data={"messages": "not json"}
        )
        assert resp.status_code == 200
        assert resp.json().get("error") == "no audio"

    def test_device_turn_silent_audio_too_quiet_with_debug(self, http_client):
        # A near-silent WAV is rejected as "too quiet" BEFORE STT/LLM (no cost),
        # and ?debug=1 attaches the normalizer metrics so a satellite's level can
        # be diagnosed without reflashing the device.
        import io
        import struct
        import wave

        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<16000h", *([0] * 16000)))  # pure silence
        resp = http_client.post(
            "/voice-assistant/api/device_turn?debug=1",
            files={"audio": ("a.wav", b.getvalue(), "audio/wav")},
            data={"messages": "[]"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("error") == "too quiet"
        assert "norm_metrics" in body, "debug must attach normalizer metrics"


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
