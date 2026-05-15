"""Hands-Free — gesture-as-push-to-talk + voice intents.

v1 is intent-only: voice fires short commands (navigation, pause/resume,
parameterized "capture X" / "task X"). Long-form authoring stays
keyboard-native because voice-editing word-by-word is a rabbit hole and
correction is brutal without a keyboard.

The frontend overlay (`emptyos/web/static/eos-hands-free.js`) does the
real work: MediaPipe gesture loop, command-palette resolution, confirm
loop. This backend is a thin support layer — config, LLM cleanup for
longer transcripts, and a dispatch history for debugging.

STT provider is hybrid and chosen per-runtime by the overlay:
- `os-native` — when daemon + browser share a machine, POST /api/os-dictate
  simulates Win+H so Windows Voice Typing handles transcription. Zero STT
  code for us, user's OS model and privacy settings apply.
- `web-speech` — fallback for remote access; Web Speech API in the browser.
- `voice-api` — deferred to v1.1.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import platform
import time
from datetime import UTC, datetime

from emptyos.sdk import BaseApp, cli_command, on_event, scheduled, web_route

CLEANUP_PROMPT = (
    "You are a transcription polisher. The text below is a raw speech-to-text "
    "transcript of a short command or capture. Your job is narrow:\n"
    "1. Add sensible punctuation and capitalisation.\n"
    "2. Fix OBVIOUS mis-hears caused by homophones (e.g. 'there' vs 'their') "
    "when context makes the correction unambiguous.\n"
    "3. Keep every content word the user said. Do NOT rephrase, shorten, or "
    "embellish.\n"
    "4. Return ONLY the cleaned text — no preamble, no explanation, no quotes."
)

DEFAULT_CONFIG = {
    "trigger_gesture": "Open_Palm",
    "hold_ms": 1000,
    "cleanup_threshold_words": 8,
    "auto_confirm_ms": 3000,
    "stt_provider": "os-native",
    "mic_language": "en-US",
    # V3 — eyes-free (TTS read-back). "confirm-only" announces intents but
    # never reads vault content; "full" reads Q&A answers + messages too.
    "voice_out": "confirm-only",
    "tts_rate": 1.1,
    "tts_voice_hint": "",
    # V4 — proactive voice. Default OFF so nothing speaks unprompted. Users
    # opt into events one at a time via proactive_events.
    "proactive_enabled": False,
    "proactive_min_gap_sec": 45,
    "proactive_quiet_start": "22:00",  # HH:MM, 24h local time
    "proactive_quiet_end": "07:00",
    "proactive_events": [],  # list of event keys, e.g. ["focus:completed"]
    # V5 — read-aloud queue defaults.
    "read_autoadvance": True,
    "read_pause_ms": 900,
    # V7 — voice navigation in reading mode. When on, opens a ~3s listen window
    # after each TTS finishes; recognises "next", "back", "stop", "act". Falls
    # through to auto-advance if the user says nothing. Requires web-speech STT
    # (Google cloud on Chrome/Edge) — off by default.
    "read_voice_nav": False,
    # V8B — finger-tip scroll. When Pointing_Up is held, the index-finger tip's
    # vertical position in the frame drives continuous page scroll. Retires the
    # V2 "jump one viewport per gesture" default. On by default but gated to
    # idle state + unregistered Pointing_Up so task-page "complete top task"
    # still wins.
    "finger_scroll_enabled": True,
    "finger_scroll_gain": 900,  # max px/sec at full displacement
    "finger_scroll_deadzone": 0.06,  # fraction of frame height (no scroll inside)
    # Advanced finger scroll tuning (exposed for power users)
    "finger_scroll_smooth_alpha": 0.25,  # EMA smoothing for fingertip Y (0.05–0.6)
    "finger_scroll_inertia_decay": 0.94,  # per-frame velocity decay (0.9–0.99)
    "finger_scroll_inertia_ms": 450,  # inertia window cap in ms (0–1500)
    # Cursor (in-browser) — fingertip mapped to page-local cursor; pinch/dwell to click.
    "cursor_enabled": False,
    "cursor_smooth_alpha": 0.25,
    "cursor_dwell_ms": 800,
    "cursor_pinch_on": 0.05,
    "cursor_pinch_off": 0.07,
    # V6 — ambient ritual (one daily check-in slot). Off by default; user sets time +
    # prompt + target action. Skips if quiet hours, if chip off (client-side), or if
    # it already fired today.
    "ritual_enabled": False,
    "ritual_time": "07:00",  # HH:MM local
    "ritual_prompt": "Good morning. What's today's intention?",
    "ritual_action": "capture",  # capture | journal-milestone | task
}

# Short, unobtrusive announcement templates. TTS interrupts — every second costs
# the user attention — so phrases stay under ~6 words.
ANNOUNCE_TEMPLATES = {
    "focus:completed": "Focus session complete.",
    "task:completed": "Task completed.",
    "task:added": "Task added.",
    "capture:saved": "Captured.",
    "journal:entry": "Journal entry saved.",
    "journal:milestone": "Milestone saved.",
}

PROACTIVE_QUEUE_LIMIT = 40

HISTORY_LIMIT = 500


class HandsFreeApp(BaseApp):
    def _history_path(self):
        return self.data_dir / "history.jsonl"

    def _config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        for k, default in DEFAULT_CONFIG.items():
            cfg[k] = self.app_config(k, default)
        # proactive_events is stored as a comma/newline-separated string via the
        # settings panel (the shared helper doesn't do lists), so coerce here.
        raw = cfg.get("proactive_events", [])
        if isinstance(raw, str):
            cfg["proactive_events"] = [
                p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()
            ]
        elif not isinstance(raw, list):
            cfg["proactive_events"] = []
        return cfg

    def _load_history(self, limit: int = 50) -> list[dict]:
        p = self._history_path()
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
        out: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _append_history(self, entry: dict) -> None:
        p = self._history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) > HISTORY_LIMIT:
                p.write_text("\n".join(lines[-HISTORY_LIMIT:]) + "\n", encoding="utf-8")
        except OSError:
            pass

    @cli_command("hands-free", help="Show hands-free status")
    async def cli_status(self):
        cfg = self._config()
        hist = self._load_history(20)
        lines = [
            f"Trigger: {cfg['trigger_gesture']} (hold {cfg['hold_ms']}ms)",
            f"STT: {cfg['stt_provider']}  (lang {cfg['mic_language']})",
            f"Voice-out: {cfg['voice_out']}  (rate {cfg['tts_rate']})",
            f"Platform: {platform.system()}",
            f"History: {len(hist)} recent dispatches",
        ]
        if hist:
            last = hist[-1]
            lines.append(f"Last: {last.get('transcript', '')!r} → {last.get('intent', '?')}")
        return "\n".join(lines)

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        cfg = self._config()
        cfg["platform"] = platform.system()
        cfg["os_dictate_supported"] = platform.system() == "Windows"
        return cfg

    @web_route("POST", "/api/cleanup")
    async def api_cleanup(self, request):
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"cleaned": ""}
        threshold = int(
            self.app_config("cleanup_threshold_words", DEFAULT_CONFIG["cleanup_threshold_words"])
        )
        if len(text.split()) <= threshold:
            return {"cleaned": text, "skipped": "below_threshold"}
        cleaned = await self.think(text, system=CLEANUP_PROMPT, domain="text", temperature=0.2)
        cleaned = (cleaned or "").strip()
        if not cleaned:
            cleaned = text
        if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) > 2:
            cleaned = cleaned[1:-1].strip()
        return {"cleaned": cleaned, "original": text}

    @web_route("POST", "/api/dispatch")
    async def api_dispatch(self, request):
        data = await request.json()
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "transcript": (data.get("transcript") or "").strip()[:400],
            "intent": (data.get("intent") or "").strip()[:80],
            "target": (data.get("target") or "").strip()[:80],
            "params": data.get("params") or {},
            "outcome": (data.get("outcome") or "fired").strip()[:40],
        }
        self._append_history(entry)
        await self.emit(
            "handsfree:dispatched",
            {
                "intent": entry["intent"],
                "target": entry["target"],
                "outcome": entry["outcome"],
            },
        )
        return {"ok": True, "entry": entry}

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        try:
            limit = max(1, min(HISTORY_LIMIT, int(request.query_params.get("limit") or "50")))
        except ValueError:
            limit = 50
        hist = self._load_history(limit)
        hist.reverse()
        return {"history": hist, "total": len(hist)}

    @web_route("POST", "/api/history/clear")
    async def api_history_clear(self, request):
        p = self._history_path()
        if p.exists():
            p.unlink()
        return {"ok": True}

    @web_route("POST", "/api/os-dictate")
    async def api_os_dictate(self, request):
        if platform.system() != "Windows":
            return {
                "ok": False,
                "reason": f"os-native dictation unsupported on {platform.system()}",
            }
        client_host = (request.client.host if request.client else "") or ""
        if client_host not in ("127.0.0.1", "::1", "localhost"):
            return {
                "ok": False,
                "reason": f"os-dictate refused for non-local origin: {client_host}",
            }
        body = await self.safe_json(request)
        if body and body.get("dry_run"):
            return {"ok": True, "method": "win+h", "dry_run": True}
        try:
            VK_LWIN = 0x5B
            VK_H = 0x48
            KEYEVENTF_KEYUP = 0x0002
            user32 = ctypes.windll.user32
            user32.keybd_event(VK_LWIN, 0, 0, 0)
            user32.keybd_event(VK_H, 0, 0, 0)
            await asyncio.sleep(0.02)
            user32.keybd_event(VK_H, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            return {"ok": True, "method": "win+h"}
        except Exception as e:
            return {"ok": False, "reason": f"keybd_event failed: {e.__class__.__name__}: {e}"}

    @on_event("gesture:detected")
    async def on_gesture(self, event):
        g = event.data.get("gesture") or ""
        if not g:
            return
        self._append_history(
            {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "transcript": "",
                "intent": f"gesture:{g}",
                "target": "debug",
                "params": {"confidence": event.data.get("confidence", 0)},
                "outcome": "logged",
            }
        )

    # ────────────────────────── V4 proactive announcements ──────────────────────────
    # Lazily-initialised instance state. BaseApp doesn't wire __init__ for us and we
    # need these to survive between @on_event invocations.
    def _proactive_state(self) -> dict:
        if not hasattr(self, "_pstate"):
            self._pstate = {"queue": [], "last_announce": 0.0, "seq": 0}
        return self._pstate

    def _in_quiet_hours(self, start: str, end: str) -> bool:
        """start/end are HH:MM strings, 24h local time. Returns True if now falls inside
        the quiet window. Handles ranges that cross midnight (22:00 → 07:00)."""
        try:
            sh, sm = [int(x) for x in str(start).split(":")]
            eh, em = [int(x) for x in str(end).split(":")]
        except ValueError:
            return False
        now = datetime.now().time()
        start_m = sh * 60 + sm
        end_m = eh * 60 + em
        now_m = now.hour * 60 + now.minute
        if start_m == end_m:
            return False
        if start_m < end_m:
            return start_m <= now_m < end_m
        # crosses midnight
        return now_m >= start_m or now_m < end_m

    async def _maybe_announce(self, key: str, event_data: dict | None = None):
        """Gate an announcement through enabled/allowlist/quiet-hours/gap checks and
        enqueue it. The frontend drains the queue on its polling cycle. Never blocks
        the caller — the bus handler returns immediately."""
        cfg = self._config()
        if not bool(cfg.get("proactive_enabled", False)):
            return
        allowed = cfg.get("proactive_events") or []
        if key not in allowed:
            return
        if self._in_quiet_hours(
            cfg.get("proactive_quiet_start", ""), cfg.get("proactive_quiet_end", "")
        ):
            return
        st = self._proactive_state()
        now = time.time()
        gap = float(cfg.get("proactive_min_gap_sec", 45))
        if now - st["last_announce"] < gap:
            return
        template = ANNOUNCE_TEMPLATES.get(key)
        if not template:
            return
        st["last_announce"] = now
        st["seq"] += 1
        entry = {
            "id": st["seq"],
            "key": key,
            "text": template,
            "ts": now,
            "iso": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        st["queue"].append(entry)
        if len(st["queue"]) > PROACTIVE_QUEUE_LIMIT:
            st["queue"] = st["queue"][-PROACTIVE_QUEUE_LIMIT:]

    @on_event("focus:completed")
    async def on_focus_completed(self, event):
        await self._maybe_announce("focus:completed", event.data)

    @on_event("task:completed")
    async def on_task_completed(self, event):
        await self._maybe_announce("task:completed", event.data)

    @on_event("task:added")
    async def on_task_added(self, event):
        await self._maybe_announce("task:added", event.data)

    @on_event("capture:saved")
    async def on_capture_saved(self, event):
        await self._maybe_announce("capture:saved", event.data)

    @on_event("journal:entry")
    async def on_journal_entry(self, event):
        await self._maybe_announce("journal:entry", event.data)

    @on_event("journal:milestone")
    async def on_journal_milestone(self, event):
        await self._maybe_announce("journal:milestone", event.data)

    @web_route("GET", "/api/proactive/pending")
    async def api_proactive_pending(self, request):
        """Return announcements newer than the client's last seen ts (seconds epoch).
        The overlay polls this on a short interval; fail-silent on the client side."""
        since_raw = request.query_params.get("since") or "0"
        try:
            since = float(since_raw)
        except ValueError:
            since = 0.0
        st = self._proactive_state()
        pending = [a for a in st["queue"] if a["ts"] > since]
        return {
            "announcements": pending,
            "now": time.time(),
            "enabled": bool(self._config().get("proactive_enabled")),
        }

    @web_route("GET", "/api/proactive/templates")
    async def api_proactive_templates(self, request):
        """Expose the event-key → template map + currently enabled list, so the
        settings UI can render a checklist without the frontend hardcoding the
        vocabulary."""
        cfg = self._config()
        return {
            "templates": ANNOUNCE_TEMPLATES,
            "enabled": cfg.get("proactive_events") or [],
        }

    # ───────────────────────────── V6 ambient ritual ─────────────────────────────
    def _ritual_state(self) -> dict:
        if not hasattr(self, "_rstate"):
            self._rstate = {"last_fire_date": None}
        return self._rstate

    def _enqueue_ritual(self, prompt: str, action: str) -> dict:
        pst = self._proactive_state()
        pst["seq"] += 1
        entry = {
            "id": pst["seq"],
            "kind": "ritual",
            "key": "ritual",
            "text": prompt,
            "action": action,
            "ts": time.time(),
            "iso": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        pst["queue"].append(entry)
        if len(pst["queue"]) > PROACTIVE_QUEUE_LIMIT:
            pst["queue"] = pst["queue"][-PROACTIVE_QUEUE_LIMIT:]
        return entry

    @scheduled("* * * * *", id="hands-free-ritual-tick")
    async def _ritual_tick(self):
        """Fires once per minute. Checks if the configured ritual time has just
        arrived and we haven't already fired today, then enqueues the ritual for
        the overlay to pick up on its next poll. Gated by enabled + quiet-hours."""
        cfg = self._config()
        if not bool(cfg.get("ritual_enabled", False)):
            return
        t = str(cfg.get("ritual_time", "")).strip()
        if not t or ":" not in t:
            return
        try:
            rh, rm = [int(x) for x in t.split(":")]
        except ValueError:
            return
        now = datetime.now()
        if now.hour != rh or now.minute != rm:
            return
        rst = self._ritual_state()
        today = now.date().isoformat()
        if rst.get("last_fire_date") == today:
            return
        if self._in_quiet_hours(
            cfg.get("proactive_quiet_start", ""), cfg.get("proactive_quiet_end", "")
        ):
            return
        rst["last_fire_date"] = today
        self._enqueue_ritual(
            str(cfg.get("ritual_prompt") or "Daily check-in."),
            str(cfg.get("ritual_action") or "capture"),
        )

    @web_route("POST", "/api/ritual/test")
    async def api_ritual_test(self, request):
        """Immediately enqueue the configured ritual so the user can verify it.
        Respects quiet hours but bypasses the once-a-day and schedule checks."""
        cfg = self._config()
        if self._in_quiet_hours(
            cfg.get("proactive_quiet_start", ""), cfg.get("proactive_quiet_end", "")
        ):
            return {"ok": False, "reason": "quiet hours"}
        entry = self._enqueue_ritual(
            str(cfg.get("ritual_prompt") or "Daily check-in."),
            str(cfg.get("ritual_action") or "capture"),
        )
        return {"ok": True, "entry": entry}

    @web_route("POST", "/api/proactive/test")
    async def api_proactive_test(self, request):
        """Synthesise an announcement without waiting for a real event, so users can
        verify their setup from the settings UI. Respects the gap + quiet-hours
        checks so the test ≈ what they'd hear in the wild."""
        data = await request.json()
        key = (data.get("key") or "capture:saved").strip()
        if key not in ANNOUNCE_TEMPLATES:
            return {"ok": False, "reason": f"unknown key: {key}"}
        # Force a one-shot announcement even if enabled=False, but keep gap/quiet checks.
        cfg = self._config()
        if self._in_quiet_hours(
            cfg.get("proactive_quiet_start", ""), cfg.get("proactive_quiet_end", "")
        ):
            return {"ok": False, "reason": "quiet hours"}
        st = self._proactive_state()
        now = time.time()
        gap = float(cfg.get("proactive_min_gap_sec", 45))
        if now - st["last_announce"] < gap:
            return {
                "ok": False,
                "reason": f"gap active, {gap - (now - st['last_announce']):.1f}s remaining",
            }
        st["last_announce"] = now
        st["seq"] += 1
        entry = {
            "id": st["seq"],
            "key": key,
            "text": ANNOUNCE_TEMPLATES[key],
            "ts": now,
            "iso": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        st["queue"].append(entry)
        if len(st["queue"]) > PROACTIVE_QUEUE_LIMIT:
            st["queue"] = st["queue"][-PROACTIVE_QUEUE_LIMIT:]
        return {"ok": True, "entry": entry}
