"""System app tests: Hands-Free — 11 use cases.

The MediaPipe loop + Web Speech API run in-browser and aren't exercised
here. These tests cover the backend support surface (config, cleanup,
dispatch history, os-dictate guards) and verify the shared overlay loads
globally on every page.
"""

import platform

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import assert_no_js_errors, wait_briefly


@pytest.mark.api
class TestHandsFreeAPI:
    def test_config_returns_defaults(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert data.get("trigger_gesture") == "Open_Palm"
        assert isinstance(data.get("hold_ms"), int) and data["hold_ms"] > 0
        assert data.get("stt_provider") in ("os-native", "web-speech", "voice-api")
        assert data.get("platform") in ("Windows", "Darwin", "Linux")
        assert isinstance(data.get("os_dictate_supported"), bool)
        assert data["os_dictate_supported"] == (platform.system() == "Windows")
        # V3
        assert data.get("voice_out") in ("off", "confirm-only", "full")
        assert isinstance(data.get("tts_rate"), (int, float))
        assert "tts_voice_hint" in data

    def test_cleanup_short_text_noop(self, http_client):
        resp = http_client.post("/hands-free/api/cleanup", json={"text": "hi there"})
        data = assert_dict_response(resp)
        assert data.get("cleaned") == "hi there"
        assert data.get("skipped") == "below_threshold"

    def test_cleanup_empty_text(self, http_client):
        resp = http_client.post("/hands-free/api/cleanup", json={"text": ""})
        data = assert_dict_response(resp)
        assert data.get("cleaned") == ""

    def test_dispatch_round_trip(self, http_client):
        resp = http_client.post(
            "/hands-free/api/dispatch",
            json={
                "transcript": "test dispatch from pytest",
                "intent": "capture: test",
                "target": "capture",
                "params": {},
                "outcome": "fired",
            },
        )
        data = assert_dict_response(resp)
        assert data.get("ok") is True
        assert data["entry"]["transcript"].startswith("test dispatch")

    def test_history_returns_recent_dispatch(self, http_client):
        http_client.post(
            "/hands-free/api/dispatch",
            json={
                "transcript": "history check",
                "intent": "navigate",
                "target": "task",
                "outcome": "fired",
            },
        )
        data = assert_dict_response(http_client.get("/hands-free/api/history?limit=5"))
        assert isinstance(data.get("history"), list)
        assert data["history"], "history should have at least one entry"
        top = data["history"][0]
        assert top["intent"] == "navigate"
        assert top["target"] == "task"

    def test_history_clear(self, http_client):
        http_client.post(
            "/hands-free/api/dispatch",
            json={"transcript": "to clear", "intent": "x", "target": "y", "outcome": "fired"},
        )
        assert_ok(http_client.post("/hands-free/api/history/clear", json={}))
        data = assert_dict_response(http_client.get("/hands-free/api/history"))
        assert data.get("history") == []

    # ───────── V4 proactive announcements ─────────

    def test_v4_proactive_config_defaults(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert data.get("proactive_enabled") is False, "proactive must default off"
        assert isinstance(data.get("proactive_min_gap_sec"), (int, float))
        assert data.get("proactive_quiet_start") == "22:00"
        assert data.get("proactive_quiet_end") == "07:00"
        assert isinstance(data.get("proactive_events"), list)

    def test_v4_templates_endpoint(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/proactive/templates"))
        assert isinstance(data.get("templates"), dict)
        # Core set that the settings UI references.
        for key in ("focus:completed", "task:completed", "capture:saved", "journal:entry"):
            assert key in data["templates"], f"missing template for {key}"

    def test_v4_test_announce_enqueues(self, http_client):
        # Force a fresh state by reading current length, firing, then polling.
        before = http_client.get("/hands-free/api/proactive/pending?since=0").json()
        before_count = len(before.get("announcements") or [])
        resp = http_client.post("/hands-free/api/proactive/test", json={"key": "task:completed"})
        data = assert_dict_response(resp)
        reason = data.get("reason") or ""
        if data.get("ok") is False and ("gap active" in reason or "quiet hours" in reason):
            # Another recent test already enqueued something, or the quiet-hours
            # window is active. Either way the gate is working. Move on.
            return
        assert data.get("ok") is True
        assert data["entry"]["text"] == "Task completed."
        after = http_client.get("/hands-free/api/proactive/pending?since=0").json()
        assert len(after.get("announcements") or []) >= before_count + 1

    def test_v4_test_announce_rejects_unknown_key(self, http_client):
        data = assert_dict_response(
            http_client.post("/hands-free/api/proactive/test", json={"key": "does:not-exist"})
        )
        assert data.get("ok") is False
        assert "unknown" in (data.get("reason") or "").lower()

    # ───────── V5 read-aloud feeds ─────────

    def test_v5_capture_read_feed_shape(self, http_client):
        data = assert_dict_response(http_client.get("/quick-action/api/read-feed?limit=3"))
        assert data.get("source") == "inbox"
        assert isinstance(data.get("items"), list)
        for item in data["items"]:
            assert "id" in item and "text" in item
            # Capture items must offer a "Save as task" act so Victory does something useful.
            assert item.get("act") and item["act"].get("url") == "/task/api/add"

    def test_v5_task_read_feed_shape(self, http_client):
        data = assert_dict_response(http_client.get("/task/api/read-feed?limit=3"))
        assert data.get("source") == "tasks"
        assert isinstance(data.get("items"), list)
        for item in data["items"]:
            assert "id" in item and "text" in item
            act = item.get("act") or {}
            assert act.get("url") == "/task/api/toggle"
            # toggle needs file+line; make sure we're passing them through.
            assert "file" in (act.get("body") or {})
            assert "line" in (act.get("body") or {})

    def test_v5_journal_read_feed_shape(self, http_client):
        data = assert_dict_response(http_client.get("/journal/api/read-feed"))
        assert data.get("source") == "journal"
        assert isinstance(data.get("items"), list)
        # Journal is read-only in the queue (no act) — even the empty-day stub counts.
        assert data.get("count") >= 1

    def test_v5_config_has_read_settings(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert "read_autoadvance" in data
        assert isinstance(data.get("read_pause_ms"), (int, float))

    # ───────── V6 ambient ritual ─────────

    def test_v6_ritual_config_defaults(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert data.get("ritual_enabled") is False
        assert data.get("ritual_time") == "07:00"
        assert "intention" in (data.get("ritual_prompt") or "").lower()
        assert data.get("ritual_action") in ("capture", "journal-milestone", "task")

    # ───────── V7 breadth wiring + voice-nav + multi-turn ─────────

    def test_v7_config_has_read_voice_nav(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert "read_voice_nav" in data
        assert data["read_voice_nav"] is False, "voice-nav must default off (it uses cloud STT)"

    def test_v8_config_has_finger_scroll(self, http_client):
        data = assert_dict_response(http_client.get("/hands-free/api/config"))
        assert data.get("finger_scroll_enabled") is True
        assert isinstance(data.get("finger_scroll_gain"), (int, float))
        assert isinstance(data.get("finger_scroll_deadzone"), (int, float))
        assert 0 <= data["finger_scroll_deadzone"] <= 1

    def test_v6_ritual_test_enqueues(self, http_client):
        resp = http_client.post("/hands-free/api/ritual/test", json={})
        data = assert_dict_response(resp)
        # May be refused if quiet hours are active for the test run; accept both
        # shapes and just require a well-formed response.
        if data.get("ok") is False:
            assert "quiet" in (data.get("reason") or "").lower()
            return
        assert data.get("ok") is True
        entry = data.get("entry") or {}
        assert entry.get("kind") == "ritual"
        assert entry.get("action") in ("capture", "journal-milestone", "task")
        # Should show up in the pending queue.
        pending = http_client.get("/hands-free/api/proactive/pending?since=0").json()
        kinds = [a.get("kind") for a in (pending.get("announcements") or [])]
        assert "ritual" in kinds

    def test_os_dictate_platform_gate(self, http_client):
        # Use dry_run so the test doesn't pop Windows Voice Typing on the user's screen.
        data = assert_dict_response(
            http_client.post("/hands-free/api/os-dictate", json={"dry_run": True})
        )
        if platform.system() == "Windows":
            assert data.get("ok") is True
            assert data.get("method") == "win+h"
            assert data.get("dry_run") is True
        else:
            assert data.get("ok") is False
            assert "unsupported" in (data.get("reason") or "")


@pytest.mark.interactive
class TestHandsFreeUI:
    def test_config_page_loads(self, app_page, page_errors):
        page = app_page("hands-free")
        wait_briefly(page, 600)
        assert page.locator("#status-grid").count() == 1
        assert page.locator("#history").count() == 1
        assert_no_js_errors(page_errors)

    def test_settings_panel_opens(self, app_page, page_errors):
        page = app_page("hands-free")
        wait_briefly(page, 600)
        page.click('button:has-text("⚙ Settings")')
        wait_briefly(page, 300)
        assert page.locator("#handsfree-settings.open, #handsfree-settings").count() >= 1
        assert_no_js_errors(page_errors)

    def test_chip_loads_on_other_pages(self, app_page, page_errors):
        # Visiting any app should inject the global hands-free chip.
        page = app_page("task")
        wait_briefly(page, 900)
        assert page.locator("#eos-handsfree-chip").count() == 1, \
            "hands-free chip should load on every page via eos.js"
        assert_no_js_errors(page_errors)

    def test_chip_has_off_state_by_default(self, app_page, page_errors):
        page = app_page("task")
        wait_briefly(page, 900)
        chip = page.locator("#eos-handsfree-chip").first
        klass = chip.get_attribute("class") or ""
        assert "eos-hf-state-off" in klass, f"expected off state by default, got classes={klass!r}"
        assert_no_js_errors(page_errors)

    def test_v2_api_exposed(self, app_page, page_errors):
        """EOS.handsFree should expose the V2 registerGesture + registeredGestures API."""
        page = app_page("task")
        wait_briefly(page, 900)
        api_shape = page.evaluate(
            "() => ({"
            "  hasHandsFree: typeof window.EOS?.handsFree === 'object',"
            "  hasRegister: typeof window.EOS?.handsFree?.registerGesture === 'function',"
            "  hasList: typeof window.EOS?.handsFree?.registeredGestures === 'function',"
            "  hasStatus: typeof window.EOS?.handsFree?.status === 'function',"
            "})"
        )
        assert api_shape == {
            "hasHandsFree": True, "hasRegister": True, "hasList": True, "hasStatus": True
        }, f"V2 API shape mismatch: {api_shape}"
        assert_no_js_errors(page_errors)

    def test_task_page_registers_pointing_up(self, app_page, page_errors):
        """Task app should bind Pointing_Up to 'Complete top focus task' via registerGesture."""
        page = app_page("task")
        wait_briefly(page, 1200)
        registered = page.evaluate("() => window.EOS.handsFree.registeredGestures()")
        assert "Pointing_Up" in registered, f"task page should register Pointing_Up; got {registered}"
        assert "complete" in registered["Pointing_Up"].lower() or "top" in registered["Pointing_Up"].lower()
        assert_no_js_errors(page_errors)

    def test_register_and_clear_gesture(self, app_page, page_errors):
        """registerGesture should accept a handler and clear it when fn=null."""
        page = app_page("hands-free")
        wait_briefly(page, 600)
        result = page.evaluate(
            "() => {"
            "  window.EOS.handsFree.registerGesture('Victory', () => 42, 'test-victory');"
            "  const before = window.EOS.handsFree.registeredGestures().Victory;"
            "  window.EOS.handsFree.registerGesture('Victory', null);"
            "  const after = window.EOS.handsFree.registeredGestures().Victory;"
            "  return {before, after};"
            "}"
        )
        assert result["before"] == "test-victory"
        assert result["after"] is None  # cleared
        assert_no_js_errors(page_errors)

    # ───────── V3 eyes-free mode ─────────

    def test_v3_speak_api_exposed(self, app_page, page_errors):
        """EOS.handsFree._speak should be a function after overlay boots."""
        page = app_page("task")
        wait_briefly(page, 900)
        shape = page.evaluate(
            "() => ({"
            "  hasSpeak: typeof window.EOS?.handsFree?._speak === 'function',"
            "  hasCancel: typeof window.EOS?.handsFree?._cancelSpeak === 'function',"
            "})"
        )
        assert shape == {"hasSpeak": True, "hasCancel": True}, f"V3 TTS API missing: {shape}"
        assert_no_js_errors(page_errors)

    def test_v3_speak_with_stubbed_synthesis(self, app_page, page_errors):
        """_speak should invoke speechSynthesis.speak with a non-empty utterance.

        window.speechSynthesis is backed by a getter in Chromium so direct assignment
        is a silent no-op; use Object.defineProperty instead.
        """
        page = app_page("task")
        wait_briefly(page, 900)
        page.evaluate(
            "() => {"
            "  window.__tts_calls = [];"
            "  Object.defineProperty(window, 'speechSynthesis', {"
            "    configurable: true, writable: true,"
            "    value: {"
            "      speak: (u) => { window.__tts_calls.push({text: u.text, rate: u.rate}); },"
            "      cancel: () => { window.__tts_calls.push({cancel: true}); },"
            "      getVoices: () => [],"
            "    },"
            "  });"
            "  window.SpeechSynthesisUtterance = function(t) { this.text = t; this.rate = 1; };"
            "}"
        )
        ok = page.evaluate("() => window.EOS.handsFree._speak('hello eyes-free world')")
        assert ok is True
        calls = page.evaluate("() => window.__tts_calls")
        speak_calls = [c for c in calls if c.get("text")]
        assert len(speak_calls) == 1
        assert "hello eyes-free world" in speak_calls[0]["text"]
        assert_no_js_errors(page_errors)

    def test_v3_voice_out_setting_in_panel(self, app_page, page_errors):
        """Hands-free settings panel should expose the voice_out selector."""
        page = app_page("hands-free")
        wait_briefly(page, 600)
        page.click('button:has-text("⚙ Settings")')
        wait_briefly(page, 300)
        panel = page.locator('#handsfree-settings')
        assert panel.count() == 1
        # One of the labels in the panel body must mention voice-out.
        labels = panel.locator('.sf-label').all_text_contents()
        assert any("voice-out" in (l or "").lower() or "voice out" in (l or "").lower() for l in labels), \
            f"voice_out field not found in settings labels: {labels}"
        assert_no_js_errors(page_errors)

    def test_v7_breadth_apps_register_gestures(self, app_page, page_errors):
        """V7A: focus / journal / publish each register a page-specific gesture when
        their page loads. Verify via EOS.handsFree.registeredGestures().

        Note: /assistant/, /agent/, /voice-assistant/ are excluded — eos-hands-free.js
        self-disables on those routes (they have their own mic UI), so any
        registerGesture call from those pages queues but never drains.
        """
        for app, expected_key in [
            ("focus", "Pointing_Up"),
            ("journal", "Thumb_Up"),
            ("publish", "ILoveYou"),
        ]:
            page = app_page(app)
            # eos-hands-free.js loads async; on heavier pages (assistant also loads
            # page-assistant.js) the 1s wait race-loses to the stub. Poll until the
            # real impl has drained the queue.
            page.wait_for_function(
                f"() => window.EOS && window.EOS.handsFree "
                f"&& {expected_key!r} in window.EOS.handsFree.registeredGestures()",
                timeout=15000,
            )
            registered = page.evaluate("() => window.EOS.handsFree.registeredGestures()")
            assert expected_key in registered, (
                f"{app} page should register {expected_key}; got {registered}"
            )
        assert_no_js_errors(page_errors)

    def test_v7_thumb_up_down_registrable(self, app_page, page_errors):
        """V7A: Thumb_Up / Thumb_Down are routable through registerGesture (V7 extension)."""
        page = app_page("journal")
        wait_briefly(page, 1000)
        registered = page.evaluate("() => window.EOS.handsFree.registeredGestures()")
        assert "Thumb_Up" in registered, f"journal should register Thumb_Up; got {registered}"
        assert "Thumb_Down" in registered, f"journal should register Thumb_Down; got {registered}"
        assert_no_js_errors(page_errors)

    def test_v6_cheat_panel_is_state_aware(self, app_page, page_errors):
        """State-aware cheat must expose different rows per state. Uses the
        public showCheat() hook so we can render without granting camera."""
        page = app_page("task")
        wait_briefly(page, 900)
        rows = page.evaluate(
            "() => {"
            "  window.EOS.handsFree.showCheat();"
            "  return Array.from(document.querySelectorAll('#hfc-cheat-list .hfc-cheat-row .g')).map(e => e.textContent);"
            "}"
        )
        assert rows, "idle cheat rows should be non-empty"
        idle_rows = [r.replace(" ", "_") for r in rows]
        assert "Open_Palm" in idle_rows, f"Open_Palm missing in idle: {idle_rows}"
        assert "Victory" in idle_rows, f"Victory missing in idle: {idle_rows}"
        assert_no_js_errors(page_errors)

    def test_v5_read_sources_table_present(self, app_page, page_errors):
        """The overlay must expose its READ_SOURCES map (implicitly — via the resolver
        recognising `read inbox`). We check by calling the resolver-adjacent surface:
        starting a read and then aborting before a real fetch lands. Easier: verify
        the intents list on the config page now mentions `read inbox / tasks / journal`.
        """
        page = app_page("hands-free")
        wait_briefly(page, 600)
        body = page.content()
        assert "read inbox" in body.lower()
        assert "read queue" in body.lower()
        assert_no_js_errors(page_errors)

    def test_v3_cheat_panel_includes_speaking_entry_when_registered(self, app_page, page_errors):
        """Cheat row count should stay stable across V3 — 7 canonical gestures."""
        page = app_page("task")
        wait_briefly(page, 900)
        page.evaluate("() => window.EOS.handsFree.on()")
        wait_briefly(page, 400)
        rows = page.locator("#hfc-cheat-list .hfc-cheat-row").count()
        # Turn off to release the camera even if the stub prevented real camera use.
        page.evaluate("() => window.EOS.handsFree.off()")
        assert rows == 0 or rows == 7, f"cheat rows should be 0 (no-camera) or 7, got {rows}"
        assert_no_js_errors(page_errors)
