"""System app tests: Dictation.

The push-to-talk hotkey + Web Speech capture run in-browser and aren't driven
here (headless Chromium can't synthesize speech). These tests cover the backend
gate + the one piece of genuinely-new code: the focused-field insertion
primitive `EOS.dictate.insertInto`, exercised directly against real DOM fields.

Requires the daemon on :9000 with the `dictation` app loaded (a fresh app — run
after the user restarts). UI tests skip gracefully if the overlay isn't present.
"""

import pytest

from helpers import assert_dict_response
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestDictationAPI:
    def test_config_shape(self, http_client):
        data = assert_dict_response(http_client.get("/dictation/api/config"))
        # `enabled` is the dark flag — default False on a fresh install, but a dev
        # may have turned it on, so assert the type, not a live value.
        assert isinstance(data.get("enabled"), bool)
        assert isinstance(data.get("hotkey"), str) and data["hotkey"]
        assert isinstance(data.get("cleanup_threshold_words"), int)
        assert isinstance(data.get("mic_language"), str)
        # STT engine selector — one of the two known providers.
        assert data.get("stt_provider") in ("web-speech", "server")
        # Vocabulary must NOT be exposed to the client (server-side only).
        assert "vocabulary" not in data

    def test_transcribe_refuses_when_disabled(self, http_client):
        """Server-STT endpoint is gated by the same dark flag; refuses with no audio."""
        cfg = http_client.get("/dictation/api/config").json()
        # No multipart body — the handler should still respond as a dict, not 500.
        resp = http_client.post("/dictation/api/transcribe")
        data = assert_dict_response(resp)
        assert isinstance(data.get("text"), str)
        if not cfg.get("enabled"):
            assert data.get("error") == "dictation is disabled"

    def test_cleanup_refuses_when_disabled(self, http_client):
        """With the dark flag off, /api/cleanup is a refusing no-op."""
        cfg = http_client.get("/dictation/api/config").json()
        resp = http_client.post("/dictation/api/cleanup", json={"text": "some words here"})
        data = assert_dict_response(resp)
        if not cfg.get("enabled"):
            assert data.get("cleaned") == ""
            assert data.get("error") == "dictation is disabled"
        else:
            # If the user has enabled it, a short utterance returns verbatim.
            assert isinstance(data.get("cleaned"), str)

    def test_cleanup_empty_text(self, http_client):
        cfg = http_client.get("/dictation/api/config").json()
        if not cfg.get("enabled"):
            pytest.skip("dictation disabled — cleanup refuses; covered elsewhere")
        data = assert_dict_response(http_client.post("/dictation/api/cleanup", json={"text": ""}))
        assert data.get("cleaned") == ""


def _overlay_ready(page) -> bool:
    """Wait for the global overlay to expose its API; False if the app isn't loaded."""
    try:
        page.wait_for_function(
            "() => window.EOS && window.EOS.dictate "
            "&& typeof window.EOS.dictate.insertInto === 'function'",
            timeout=8000,
        )
        return True
    except Exception:
        return False


@pytest.mark.interactive
class TestDictationUI:
    def test_status_page_loads(self, app_page, page_errors):
        page = app_page("dictation")
        wait_briefly(page, 600)
        # Either the gate (off) or the active surface renders — both are valid.
        assert page.locator("#gate, #app").count() >= 1
        assert_no_js_errors(page_errors)

    def test_overlay_exposes_api(self, app_page, page_errors):
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded (app may be disabled in store)")
        shape = page.evaluate(
            "() => ({"
            "  hasInsert: typeof window.EOS.dictate.insertInto === 'function',"
            "  hasReload: typeof window.EOS.dictate.reload === 'function',"
            "  hasStatus: typeof window.EOS.dictate.status === 'function',"
            "})"
        )
        assert shape == {"hasInsert": True, "hasReload": True, "hasStatus": True}
        assert_no_js_errors(page_errors)

    def test_insert_into_textarea(self, app_page, page_errors):
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        result = page.evaluate(
            "() => {"
            "  const ta = document.createElement('textarea');"
            "  document.body.appendChild(ta); ta.focus();"
            "  let fired = false; ta.addEventListener('input', () => { fired = true; });"
            "  const ok = window.EOS.dictate.insertInto(ta, 'hello world');"
            "  const v = ta.value; ta.remove();"
            "  return { ok, v, fired };"
            "}"
        )
        assert result == {"ok": True, "v": "hello world", "fired": True}
        assert_no_js_errors(page_errors)

    def test_insert_at_caret_position(self, app_page, page_errors):
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        # Caret between "ab" and "cd"; insertion lands at the caret, not the end.
        v = page.evaluate(
            "() => {"
            "  const ta = document.createElement('textarea');"
            "  document.body.appendChild(ta); ta.value = 'abcd'; ta.focus();"
            "  ta.setSelectionRange(2, 2);"
            "  window.EOS.dictate.insertInto(ta, 'XY');"
            "  const v = ta.value; ta.remove(); return v;"
            "}"
        )
        assert v == "abXYcd"
        assert_no_js_errors(page_errors)

    def test_insert_into_contenteditable(self, app_page, page_errors):
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        result = page.evaluate(
            "() => {"
            "  const d = document.createElement('div'); d.contentEditable = 'true';"
            "  document.body.appendChild(d); d.focus();"
            "  let fired = false; d.addEventListener('input', () => { fired = true; });"
            "  const ok = window.EOS.dictate.insertInto(d, 'rich text');"
            "  const t = d.textContent; d.remove();"
            "  return { ok, t, fired };"
            "}"
        )
        assert result["ok"] is True
        assert result["t"] == "rich text"
        assert_no_js_errors(page_errors)

    def test_insert_guards_non_editable(self, app_page, page_errors):
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        ok = page.evaluate(
            "() => {"
            "  const div = document.createElement('div');"
            "  document.body.appendChild(div);"
            "  const ok = window.EOS.dictate.insertInto(div, 'nope');"
            "  div.remove(); return ok;"
            "}"
        )
        assert ok is False
        assert_no_js_errors(page_errors)

    def test_hotkey_unbound_when_disabled(self, app_page, page_errors):
        """The no-op contract: with the dark flag off, status().enabled is false."""
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        st = page.evaluate("() => window.EOS.dictate.status()")
        cfg_enabled = page.evaluate(
            "() => fetch('/dictation/api/config').then(r => r.json()).then(c => !!c.enabled)"
        )
        # status().enabled mirrors the server flag.
        assert st.get("enabled") == cfg_enabled
        assert_no_js_errors(page_errors)

    def test_mic_button_appears_on_focus(self, app_page, page_errors):
        """When enabled, focusing a text field mounts the floating 🎤 button
        anchored to that field; blurring retires it."""
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        if not page.evaluate("() => window.EOS.dictate.status().enabled"):
            pytest.skip("dictation disabled — mic button only mounts when enabled")
        # Focus a fresh textarea → the mic button gets class 'on' (focusin handler).
        shown = page.evaluate(
            "() => new Promise(res => {"
            "  const ta = document.createElement('textarea');"
            "  ta.id = 'eos-mic-probe'; document.body.appendChild(ta); ta.focus();"
            "  setTimeout(() => {"
            "    const mic = document.getElementById('eos-dictate-mic');"
            "    res(!!(mic && mic.classList.contains('on')));"
            "  }, 200);"
            "})"
        )
        assert shown is True
        # Blur (focus elsewhere) → the button retires after its hide delay.
        hidden = page.evaluate(
            "() => new Promise(res => {"
            "  const ta = document.getElementById('eos-mic-probe');"
            "  if (ta) ta.blur();"
            "  document.body.focus();"
            "  setTimeout(() => {"
            "    const mic = document.getElementById('eos-dictate-mic');"
            "    const off = !(mic && mic.classList.contains('on'));"
            "    if (ta) ta.remove();"
            "    res(off);"
            "  }, 350);"
            "})"
        )
        assert hidden is True
        assert_no_js_errors(page_errors)

    def test_stream_region_writes_into_field(self, app_page, page_errors):
        """The live-streaming region (used during dictation) writes + revises text
        in place. insertInto is the same underlying caret write, exercised here as
        the closest headless proxy for the streaming path (SpeechRecognition can't
        run headlessly)."""
        page = app_page("dictation")
        if not _overlay_ready(page):
            pytest.skip("dictation overlay not loaded")
        # Simulate the interim→final revision: two writes into the same caret region
        # must replace, not append (the streaming contract).
        v = page.evaluate(
            "() => {"
            "  const ta = document.createElement('textarea');"
            "  document.body.appendChild(ta); ta.value = 'note: '; ta.focus();"
            "  ta.setSelectionRange(6, 6);"
            "  window.EOS.dictate.insertInto(ta, 'helo wrld');"   # interim-ish
            "  const mid = ta.value;"
            "  ta.setSelectionRange(6, ta.value.length);"          # re-select the written span
            "  window.EOS.dictate.insertInto(ta, 'hello world');"  # revised
            "  const fin = ta.value; ta.remove();"
            "  return { mid, fin };"
            "}"
        )
        assert v["mid"] == "note: helo wrld"
        assert v["fin"] == "note: hello world"
        assert_no_js_errors(page_errors)
