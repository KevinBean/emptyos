"""Brain Dump — triggered (never ambient) thought-sorting pipeline.

A user fires a short session → record audio or type a pile of mixed thoughts →
transcribe → summarize → split into typed action items the user reviews via the
existing rooms review gate before anything touches state. The hard architectural
line that keeps this from becoming a surveillance-style life recorder:
**triggered, not ambient.** No background mic, no continuous listen, no
auto-start without an explicit user action.

Composes existing primitives — no new gate code, no new capability:
  * pipeline  : emptyos.sdk.pipeline (transcribe → summarize → extract_actions)
  * listen    : self.listen() chain (cloud-consent-gated, self-routing)
  * review    : self.propose_action / propose_kb_note → rooms pending queue
  * triggers  : button + hotkey:pressed + a voice intent (no ambient path)

Dark-default: gated behind the feature flag (default off). The flag reads the
runtime Settings service FIRST (so the ⚙ panel toggle flips it live, no
restart) and falls back to emptyos.toml [apps.braindump] feature.enabled (so a
repo/demo config still works). The app installs/loads regardless; the surface
stays dark until enabled.

Pipeline stages + prompts live in pipeline.py; this spine owns the HTTP surface,
the session lifecycle, the triggers, and the opt-in summary note.
"""
from __future__ import annotations

from datetime import date

from emptyos.sdk import BaseApp, on_event, slugify, web_route

from . import meeting as _meeting
from . import pipeline as _pipeline
from . import recall as _recall


def _slugify(text: str) -> str:
    return slugify(text, max_len=48, fallback="braindump")


_AUDIO_EXTS = {"webm", "wav", "mp3", "m4a", "ogg", "oga", "flac", "aac", "mp4"}


def _audio_ext(filename: str) -> str:
    """Safe audio extension from an uploaded filename (whisper sniffs content,
    but keep the real container hint). Defaults to webm (live recording)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return ext if ext in _AUDIO_EXTS else "webm"


class BrainDumpApp(BaseApp):

    # ── Pipeline drivers (extracted to pipeline.py) ──
    _run_pipeline = _pipeline._run_pipeline
    _resume_pipeline = _pipeline._resume_pipeline
    _run_status = _pipeline._run_status
    _extract_to_proposals = _pipeline._extract_to_proposals

    # ── Meeting capture (system-audio loopback + mic; extracted to meeting.py) ──
    _meeting_svc = _meeting._meeting_svc
    api_meeting_capabilities = _meeting.api_meeting_capabilities
    api_meeting_start = _meeting.api_meeting_start
    api_meeting_status = _meeting.api_meeting_status
    api_meeting_stop = _meeting.api_meeting_stop

    # ── Recall across the KEPT corpus (extracted to recall.py) ──
    # Reads only applied keep-summary notes under outputs/ — never a run dir,
    # so a discarded dump is unreachable by construction. Dark by default.
    _recall_enabled = _recall._recall_enabled
    _recall_corpus = _recall._recall_corpus
    _recall_search = _recall._recall_search
    api_recall_status = _recall.api_recall_status
    api_recall = _recall.api_recall

    # ── Dark-default flag gate ───────────────────────────────────────────
    def _enabled(self) -> bool:
        # Settings service first (⚙ panel toggle, live), then emptyos.toml.
        v = self.setting("braindump.feature.enabled", None)
        if v is not None:
            return bool(v)
        return bool(self.app_config("feature.enabled", False))

    @web_route("GET", "/api/config")
    async def api_config(self, request):
        """Dark-default flags the page reads on load. When ``enabled`` is false
        the page shows an 'enable in Settings' panel and fetches nothing.

        ``recall`` is the separate, darker flag for corpus recall — carried here
        so a daemon with it off costs the page no extra request."""
        return {"enabled": self._enabled(), "recall": self._recall_enabled()}

    # ── Start a session (the only trigger that records) ──────────────────
    @web_route("POST", "/api/start")
    async def api_start(self, request):
        """Begin a session. Multipart form: mode=audio|text, text?, and (audio
        mode) an `audio` blob. The transcribe → summarize work runs in the
        BACKGROUND (one LLM call can outlast an HTTP read timeout — see
        feedback_long_handler_in_http_request); this returns a run_id
        immediately and the client polls GET /api/runs/{id} until it pauses."""
        if not self._enabled():
            return {"error": "disabled"}
        form = await request.form()
        mode = (form.get("mode") or "text").strip()

        # Validate the input BEFORE minting a run so a rejected request leaves
        # no orphan run dir.
        audio_bytes = artifact = None
        if mode == "audio":
            # Same path for a live MediaRecorder blob OR an uploaded audio file
            # (phone voice memo etc.) — both arrive as the `audio` part.
            audio_file = form.get("audio")
            if not audio_file:
                return {"error": "no audio"}
            audio_bytes = await audio_file.read()
            if not audio_bytes:
                return {"error": "empty audio"}
            artifact = "raw." + _audio_ext(getattr(audio_file, "filename", "") or "")
            inputs = {"mode": "audio", "audio_artifact": artifact}
        else:
            text = (form.get("text") or "").strip()
            if not text:
                return {"error": "no text"}
            inputs = {"mode": "text", "text": text}

        handle = self.runs("runs").new()
        run_id = handle.run_id
        if audio_bytes is not None:
            handle.write_artifact(artifact, audio_bytes)

        import asyncio
        self.spawn_background(self.emit("braindump:started", {"mode": mode}))
        self.spawn_background(self._run_pipeline(inputs, run_id=run_id))
        return {"run_id": run_id, "status": "running"}

    @web_route("GET", "/api/runs/{run_id}")
    async def api_run_status(self, request):
        """Poll target for the client. Reads the persisted run state — works for
        both the start phase (running → paused, carries summary) and the continue
        phase (running → complete, carries proposed)."""
        run_id = request.path_params["run_id"]
        summary = self._run_status(run_id)
        if summary is None:
            return {"error": "unknown run"}
        return self._run_view(summary)

    def _run_view(self, summary: dict) -> dict:
        results = summary.get("results") or {}
        ex = results.get("extract_actions") or {}
        return {
            "run_id": summary.get("run_id"),
            "status": summary.get("status"),    # running | paused | complete | error
            "error": summary.get("error"),
            "summary": (results.get("summarize") or {}).get("summary", ""),
            "transcript": (results.get("transcribe") or {}).get("transcript", ""),
            "proposed": ex.get("proposed", []),
            "count": ex.get("count", 0),
        }

    # ── Continue (resume past the review pause) ──────────────────────────
    @web_route("POST", "/api/runs/{run_id}/continue")
    async def api_continue(self, request):
        """Resume a paused run through extract_actions in the BACKGROUND (another
        LLM call), applying the (possibly edited) summary. Returns immediately;
        the client polls GET /api/runs/{id} until it completes."""
        if not self._enabled():
            return {"error": "disabled"}
        run_id = request.path_params["run_id"]
        if self._run_status(run_id) is None:
            return {"error": "unknown run"}
        try:
            body = await request.json()
        except Exception:
            body = {}
        override = (body.get("summary_override") or "").strip()
        import asyncio
        self.spawn_background(self._resume_pipeline(run_id, summary_override=override))
        return {"run_id": run_id, "status": "running"}

    # ── Discard (privacy — leave no trace of an unwanted capture) ─────────
    @web_route("POST", "/api/runs/{run_id}/discard")
    async def api_discard(self, request):
        run_id = request.path_params["run_id"]
        handle = self.runs("runs").get(run_id)
        if handle is None:
            return {"error": "unknown run"}
        # Delete the raw audio (any container) + transcript so a discarded
        # capture leaves no trace.
        try:
            for p in handle.dir.glob("raw.*"):
                p.unlink(missing_ok=True)
            handle.artifact("transcript.txt").unlink(missing_ok=True)
        except Exception:
            pass
        state = handle.read_state() or {}
        state["status"] = "discarded"
        handle.write_state(state)
        return {"ok": True, "run_id": run_id}

    # ── Opt-in: propose keeping the summary as a vault note ───────────────
    @web_route("POST", "/api/runs/{run_id}/keep-summary")
    async def api_keep_summary(self, request):
        """Propose (through the review gate) saving the summary as a vault note.
        Even this write is reviewed — nothing touches the vault without Apply."""
        if not self._enabled():
            return {"error": "disabled"}
        run_id = request.path_params["run_id"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        summary = (body.get("summary") or "").strip()
        if not summary:
            handle = self.runs("runs").get(run_id)
            summary = (handle.read_artifact("summary.md") if handle else "") or ""
        if not summary.strip():
            return {"error": "no summary"}
        saved = await self.propose_action(
            app="braindump", method="save_summary_note",
            args={"run_id": run_id, "summary": summary},
        )
        return {"ok": True, "action_id": saved.get("id", "")}

    async def save_summary_note(self, run_id: str = "", summary: str = "") -> dict:
        """call_app target applied by the review gate. Writes the AI-authored
        summary under outputs/ with author: ai (authorship-boundary rule)."""
        summary = (summary or "").strip()
        if not summary:
            return {"error": "no summary"}
        today = date.today().isoformat()
        title = summary.split("\n", 1)[0][:60]
        slug = _slugify(title)
        root_rel = self.save_report_note(
            f"{today}-{slug}.md",
            title=title,
            body=summary,
            tags=["braindump"],
            as_of=today,
            extra={"run_id": run_id},
        )
        import asyncio
        self.spawn_background(self.emit("braindump:summary_kept",
                                      {"run_id": run_id, "path": root_rel}))
        return {"ok": True, "path": root_rel}

    # ── Triggers (all explicit — no ambient path) ────────────────────────
    async def voice_start_session(self) -> dict:
        """Voice intent: open/arm the Brain Dump surface. Does NOT turn Aura
        into the recorder — recording happens on the Brain Dump page."""
        if not self._enabled():
            return {"say": "Brain Dump is turned off. Enable it in Settings first."}
        import asyncio
        self.spawn_background(self.emit("braindump:started", {"mode": "voice-armed"}))
        return {
            "say": "Brain Dump ready — open it to record.",
            "link": {"text": "Open Brain Dump", "href": "/braindump/"},
        }

    @on_event("hotkey:pressed")
    async def _on_hotkey(self, event):
        """Opt-in hotkey trigger. Dormant unless the user sets
        braindump.capture_hotkey to the pressed key. Pure subscription —
        the global-hotkey plugin already emits hotkey:pressed."""
        if not self._enabled():
            return
        want = (self.setting("braindump.capture_hotkey", "") or "").strip().lower()
        if not want:
            return
        key = ((event.data or {}).get("key") or "").strip().lower()
        if key and key == want:
            await self.emit("braindump:started", {"mode": "hotkey-armed", "key": key})

    # ── Future wake-word hook (NOT wired in v1) ──────────────────────────
    # When plugins/wake-word/ ships it emits "wake-word:detected"; add:
    #   @on_event("wake-word:detected")
    #   async def _on_wake(self, event): ...  # same body as _on_hotkey
    # v1 deliberately has no always-on / ambient path.

    # ── Hub panel ────────────────────────────────────────────────────────
    async def panel_recent_captures(self) -> dict | None:
        """Glanceable count of recent brain-dump runs (last 7)."""
        try:
            runs = self.runs("runs").recent_states(7)
        except Exception:
            return None
        n = len(list(runs))
        if not n:
            return None
        return {"label": "Brain dumps (7)", "value": n, "href": "/braindump/"}
