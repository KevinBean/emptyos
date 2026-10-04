import asyncio
import datetime
import json
import time
from collections import deque

from emptyos.sdk import BaseApp, ndjson_response, web_route

from . import companions as _companions
from . import device as _device
from . import generic_verbs as _generic_verbs
from . import interiority as _interiority
from . import memory as _memory
from . import pending as _pending
from . import planning as _planning
from . import research as _research
from .chat_pipeline import chat_response
from .shared import is_stt_artifact
from .intents import (
    intent_embedding_text,
    render_intent_block,
    scope_intents,
    scope_intents_by_relevance,
    validate_args,
)

# Throwaway utterance for the warm-up's intent-relevance pass. Only the intent
# *corpus* embeddings it builds are cached and reused; this query's own
# embedding is discarded, so the wording is irrelevant — it just must be
# non-empty (an empty string short-circuits to the recency scope and warms
# nothing).
WARMUP_SEED = "hello"


class VoiceAssistantApp(BaseApp):
    async def setup(self):
        # Cache companion definitions from all contributing apps at startup.
        self._companions: dict[str, dict] = _companions.companions_from(
            getattr(self.kernel, "apps", None))

        # Cache voice intents (verbs apps expose for the LLM to invoke).
        # Source of truth is the unified verb registry ([[provides.verbs]],
        # surfaces ∋ "voice") for migrated apps; legacy
        # [[contributes.voice-assistant.intent]] for the rest. All-or-nothing per
        # (app, voice): an app with ANY registry voice verb has its legacy voice
        # intents + narrators skipped, so nothing double-registers. See
        # .claude/rules/verb-registry.md.
        self._intents: dict[str, dict] = {}
        self._narrators: list[dict] = []
        loader = getattr(self.kernel, "apps", None)
        migrated_voice_apps: set[str] = set()
        if loader is not None:
            # Shared merged view (registry voice surface + legacy intents,
            # all-or-nothing skip per migrated app) — same source the
            # telegram bridge derives its phone allowlist from.
            from emptyos.sdk.verb_registry import merged_voice_entries

            for rec in merged_voice_entries(loader):
                self._intents[rec["verb"]] = rec
                if rec.get("narrate"):
                    self._narrators.append(
                        {"intent": rec["verb"], "method": rec["narrate"],
                         "_app_id": rec.get("_app_id")}
                    )
            try:
                migrated_voice_apps = loader.get_verbs().apps_for_surface("voice")
            except Exception:
                migrated_voice_apps = set()
            # Legacy narrators — apps register `[[contributes.voice-assistant.narration]]`
            # to append a follow-up sentence after one of their intents fires.
            # Skip migrated apps (their narrate field is read above). Matching
            # (exact verb or `task.*` prefix) is done per-dispatch in _lookup_narrators.
            for entry in loader.get_contributions("voice-assistant", "narration"):
                if (
                    entry.get("intent")
                    and entry.get("method")
                    and entry.get("_app_id") not in migrated_voice_apps
                ):
                    self._narrators.append(entry)

        # Generic fallback verbs (aura.open/search/app_list) are declared in the
        # manifest but inert until feature.companion-generic-verbs.enabled. Drop
        # them from the scoped set when the flag is off (byte-identical when dark).
        if not self.app_config("feature.companion-generic-verbs.enabled", False):
            for _gv in _generic_verbs.GENERIC_VERBS:
                self._intents.pop(_gv, None)

        # Voice-confirm anchor: companion_id → last gated pending action id,
        # so a follow-up "yes" (aura.confirm) knows what it refers to.
        # In-memory only — after a restart _resolve_last_pending falls back
        # to the newest still-pending file, so nothing is lost.
        self._last_pending: dict[str, str] = {}

        # Cross-turn intent dedupe window: recent (invocation_key, monotonic_ts)
        # pairs. Guards mutation-shaped verbs against double-submit ("add a
        # task to call mom" twice in a minute → one task). See dispatch_intent.
        self._recent_invocations: deque[tuple[str, float]] = deque(maxlen=16)

        # Tracks which apps have fired an intent recently. Drives the scope
        # window — companion's app + last-2 invoked apps. Persisted across
        # daemon restarts so the user doesn't lose context after a reboot.
        self._recent_apps: deque[str] = deque(maxlen=2)
        try:
            persisted = (self.data_dir / "recent_apps.json").read_text(encoding="utf-8")
            for app_id in json.loads(persisted):
                if isinstance(app_id, str):
                    self._recent_apps.append(app_id)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # ── Narration + recency state ──────────────────────────────────────────

    def _lookup_narrators(self, verb: str) -> list[dict]:
        """Return narrators registered for *verb* (exact) or its namespace (`<app>.*`)."""
        if not self._narrators or not verb:
            return []
        ns_prefix = verb.split(".", 1)[0] + ".*" if "." in verb else None
        out = []
        for entry in self._narrators:
            target = entry.get("intent") or ""
            if target == verb or (ns_prefix and target == ns_prefix):
                out.append(entry)
        return out

    def _orient(self, user_text: str, scoped_intents: list[dict]) -> str | None:
        """Heuristic pre-turn preamble — "what I'm about to do" in <8 words.

        Deterministic keyword match against the user text + scoped intent verbs.
        Returns None for short / ack-shaped / unmatched inputs so the normal
        stream proceeds without a preamble. No LLM call — zero added latency,
        which is the whole point of a preamble.
        """
        text = (user_text or "").strip()
        words = text.split()
        if len(words) < 4:
            return None
        low = text.lower()
        for p in ("yes", "no ", "ok", "okay", "thanks", "thank", "yeah",
                  "cool", "nope", "sure", "alright", "got it"):
            if low.startswith(p):
                return None

        # Skip orient on memory-shaped utterances — the matching intent
        # (aura.remember / .forget / .recall) emits its own short say, and
        # a preamble like "checking your calendar" mis-fires on phrases
        # like "remember morning meetings".
        memory_triggers = ("remember", "forget", "what do you remember",
                           "what you know about me", "recall")
        if any(t in low for t in memory_triggers):
            return None

        verbs = {e.get("verb", "") for e in (scoped_intents or [])}
        has_ns = lambda ns: any(v.startswith(ns + ".") for v in verbs)

        if has_ns("task") and any(w in low for w in (
                "task", "todo", "to do", "tomorrow", "today", "overdue", "this week", "due")):
            return "Pulling up your tasks."
        if has_ns("journal") and any(w in low for w in (
                "journal", "diary", "yesterday", "reflect", "entry")):
            return "Checking your journal."
        if has_ns("kb") and any(w in low for w in (
                "knowledge", "note about", "kb ", "what do i know")):
            return "Searching your knowledge base."
        if any(w in low for w in (
                "research", "latest", "news on", "what's new", "what is new",
                "look up online", "google ", "find online")):
            return "Looking that up online."
        if any(w in low for w in ("search ", "find ", "lookup", "look up")):
            return "Searching for that."
        if any(w in low for w in (
                "calendar", "schedule", "appointment", "agenda")):
            return "Checking your calendar."
        if any(w in low for w in ("remind me", "reminder", "set a reminder")):
            return "Setting that reminder."
        return None

    async def _run_narrators(self, verb: str, args: dict, result: dict) -> str:
        """Call every narrator registered for *verb*. Fail-soft per narrator.

        Returns concatenated non-empty narration strings, joined by single space."""
        narrators = self._lookup_narrators(verb)
        if not narrators:
            return ""
        parts: list[str] = []
        for entry in narrators:
            app_id = entry.get("_app_id")
            method = entry.get("method")
            if not app_id or not method:
                continue
            try:
                out = await self.call_app(app_id, method, args=args, result=result)
            except Exception:
                continue
            if isinstance(out, str) and out.strip():
                parts.append(out.strip())
        return " ".join(parts)

    def _persist_recent_apps(self):
        try:
            (self.data_dir / "recent_apps.json").write_text(
                json.dumps(list(self._recent_apps)), encoding="utf-8"
            )
        except Exception:
            pass

    # ── Intent registry shims ───────────────────────────────────────────────
    # Thin instance methods that bind module-level helpers to current state,
    # so chat_pipeline + plan paths can keep calling self._scope_intents(...)
    # without knowing the helpers moved.

    def _scope_intents(self, companion_id: str | None, focus_app: str | None = None) -> list[dict]:
        return scope_intents(self._intents, self._companions, self._recent_apps, companion_id,
                             focus_app=focus_app)

    async def _scope_intents_relevant(
        self, user_text: str, companion_id: str | None,
        messages: list[dict] | None = None,
        focus_app: str | None = None,
    ) -> list[dict]:
        """Embedding-aware variant. Replaces the recency-window scope with a
        relevance-window scope: ranks every intent by cosine similarity of
        its (verb + description + example) to the user utterance, fills the
        prompt slots best-first after always-intents and companion-app
        intents.

        Multi-turn aware: when `messages` is passed, the embedding query
        includes recent prior turns so follow-ups ("the same for tomorrow")
        still surface the right intent.

        Falls back to the recency-based `_scope_intents` when embeddings
        aren't available (no OPENAI_API_KEY) or anything in the embed pass
        fails — so this is purely additive.
        """
        if not user_text or not user_text.strip():
            return self._scope_intents(companion_id, focus_app=focus_app)
        if not getattr(self, "embeddings_available", False):
            return self._scope_intents(companion_id, focus_app=focus_app)
        try:
            entries = list(self._intents.values())
            if not entries:
                return []
            texts = [intent_embedding_text(e) for e in entries]
            embs = await self.embed_texts(texts)
            from emptyos.sdk.embeddings import build_retrieval_query, cosine

            # Trim the trailing turn if it equals the current utterance.
            history = []
            for m in (messages or []):
                content = m.get("content") if isinstance(m, dict) else ""
                if content and content != user_text:
                    history.append({"role": m.get("role", ""), "content": content})
            retrieval_query = build_retrieval_query(history, user_text)
            q_emb = await self.embed_text(retrieval_query)

            scored = sorted(
                ((entries[i].get("verb"), cosine(q_emb, embs[i])) for i in range(len(entries))),
                key=lambda x: -x[1],
            )
            ranked_verbs = [v for v, _ in scored if v]
            return scope_intents_by_relevance(
                self._intents, self._companions, companion_id, ranked_verbs,
                recent_apps=self._recent_apps, focus_app=focus_app,
            )
        except Exception:
            return self._scope_intents(companion_id, focus_app=focus_app)

    def _intent_prompt_block(self, scoped: list[dict]) -> str:
        return render_intent_block(scoped)

    def _validate_args(self, schema: dict, args: dict) -> tuple[bool, str]:
        return validate_args(schema, args)

    # ── Logging + housekeeping ──────────────────────────────────────────────

    def _log_chat(
        self,
        user_text: str,
        reply_text: str,
        companion_id: str | None = None,
        *,
        surface: str = "",
        extras: dict | None = None,
    ):
        """Append one turn to chat_log.jsonl.

        `surface` names which face of the companion brain handled the turn
        (aura-voice / aura-text / rail / device); `extras` carries compact
        summaries of non-text output (cards / links / pending action ids) so
        a card-shaped answer is never invisible in the log or /api/history.
        Both are additive — old lines without them still parse everywhere.
        """
        log_file = self.data_dir / "chat_log.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.datetime.now().isoformat(),
            "companion": companion_id or "aura",
            "user": user_text,
            "assistant": reply_text,
        }
        if surface:
            entry["surface"] = surface
        for key in ("cards", "links", "pending"):
            vals = (extras or {}).get(key)
            if vals:
                entry[key] = vals
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _purge_audio(self):
        """Delete audio reply files older than 24 hours."""
        audio_dir = self.data_dir / "audio"
        if not audio_dir.exists():
            return
        cutoff = datetime.datetime.now().timestamp() - 86400
        for f in audio_dir.glob("reply_*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

    # ── Chat API ────────────────────────────────────────────────────────────

    @web_route("POST", "/api/warmup")
    async def api_warmup(self, request):
        """Pre-warm the quality-context caches so the session's first turn is fast.

        The heavy pre-stream build (`build_quality_context` in chat_pipeline)
        measured ~23s on cold caches vs ~3s warm — all of it in the ambient
        context fan-out (`_build_context` → call_contributions) and the intent
        embedding corpus (`_scope_intents_relevant`). Both cache after one run,
        so the page fires this on load (fire-and-forget) and the first real
        utterance lands on warm caches. Runs the same code a real turn runs —
        it cannot change any turn's output, only when the cost is paid.
        Concurrent calls coalesce onto one in-flight warm.
        """
        task = getattr(self, "_warmup_task", None)
        if task is not None and not task.done():
            return {"ok": True, "status": "already-warming"}

        async def _warm():
            t0 = time.monotonic()
            # A failing contributor must not fail the warm (the turn itself
            # tolerates them) — but it must not pass silently either, or a
            # permanently-cold cache reports "warmed" forever.
            results = await asyncio.gather(
                self._build_system_prompt(None),
                self._build_context(None),
                self._scope_intents_relevant(WARMUP_SEED, None),
                return_exceptions=True,
            )
            failed = [r for r in results if isinstance(r, BaseException)]
            for exc in failed:
                self.log_warn(f"warmup build failed: {exc!r}")
            return round((time.monotonic() - t0) * 1000), len(failed)

        self._warmup_task = asyncio.create_task(_warm())
        try:
            ms, failed = await self._warmup_task
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "status": "warmed", "ms": ms, "failed": failed}

    @web_route("POST", "/api/chat_stream")
    async def api_chat_stream(self, request):
        form = await request.form()
        audio_file = form.get("audio")
        messages_str = form.get("messages", "[]")
        companion_id = form.get("companion") or None  # echoed from frontend done payload

        try:
            messages = json.loads(messages_str)
        except Exception:
            messages = []

        async def empty_stream(err_msg):
            yield {"type": "error", "error": err_msg}

        if not audio_file:
            return ndjson_response(empty_stream("no audio"))

        audio_bytes = await audio_file.read()

        try:
            user_text = await self.listen(audio=audio_bytes)
        except Exception as e:
            return ndjson_response(empty_stream(f"Failed to listen: {e}"))

        if not user_text or not user_text.strip() or is_stt_artifact(user_text):
            # Caption-artifact filter: Whisper hallucinates YouTube boilerplate
            # ("Thanks for watching!") on silence/noise — treat as no speech.
            return ndjson_response(empty_stream("Could not understand audio"))

        return await chat_response(self, user_text, messages, companion_id, surface="aura-voice")

    @web_route("POST", "/api/chat_text")
    async def api_chat_text(self, request):
        """Text-mode chat — same NDJSON event stream as /api/chat_stream, no STT."""
        data = await self.safe_json(request)
        user_text = (data.get("text") or "").strip()
        messages = data.get("messages") or []
        if not isinstance(messages, list):
            messages = []
        companion_id = data.get("companion") or None

        async def empty_stream(err_msg):
            yield {"type": "error", "error": err_msg}

        if not user_text:
            return ndjson_response(empty_stream("text required"))

        # Text path — UI already rendered the user message optimistically,
        # so we suppress the user_text event to avoid duplication. Audio path
        # callers leave echo_user_text=True since the UI doesn't know what
        # was transcribed until the server says so.
        return await chat_response(self, user_text, messages, companion_id, echo_user_text=False,
                                   surface="aura-text")

    @web_route("GET", "/audio/{filename}")
    async def get_audio(self, request):
        filename = request.path_params["filename"]
        return self.serve_audio_file(filename, subdir="audio")

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        """Return recent chat log entries, newest first."""
        limit = int(request.query_params.get("limit", "50"))
        companion = request.query_params.get("companion")  # optional filter
        log_file = self.data_dir / "chat_log.jsonl"
        if not log_file.exists():
            return []
        entries = []
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if companion and entry.get("companion") != companion:
                    continue
                entries.append(entry)
            except Exception:
                pass
        return entries[-limit:][::-1]  # newest first

    async def panel_assistant(self) -> dict:
        return {
            "label": "Aura",
            "text": "Ready to help",
            "url": "/voice-assistant/",
            "button_label": "Talk",
        }

    # ── Companion routing + text rail (extracted to companions.py) ──
    _companion_enabled       = _companions._companion_enabled
    _detect_companion_switch = _companions._detect_companion_switch
    _build_system_prompt     = _companions._build_system_prompt
    _build_context           = _companions._build_context
    _append_focus_context    = _companions._append_focus_context
    api_companion_status      = _companions.api_companion_status
    api_companion_turn        = _companions.api_companion_turn
    api_companion_turn_stream = _companions.api_companion_turn_stream
    api_companion_voice_turn  = _companions.api_companion_voice_turn
    api_companions            = _companions.api_companions

    # ── Generic fallback verbs (extracted to generic_verbs.py) ──
    _resolve_app_query     = _generic_verbs._resolve_app_query
    voice_open_app         = _generic_verbs.voice_open_app
    voice_universal_search = _generic_verbs.voice_universal_search
    voice_app_list         = _generic_verbs.voice_app_list

    # ── Headless-device voice turns (extracted to device.py) ──
    _device_prep              = _device._device_prep
    api_device_turn           = _device.api_device_turn
    _device_turn_error        = _device._device_turn_error
    api_device_turn_stream    = _device.api_device_turn_stream
    _device_wav_urls          = _device._device_wav_urls
    _save_device_audio        = _device._save_device_audio
    _device_turn_audio_review = _device._device_turn_audio_review

    # ── Plan-then-execute + confirm gate + debug (extracted to planning.py) ──
    plan_actions       = _planning.plan_actions
    execute_plan       = _planning.execute_plan
    api_plan           = _planning.api_plan
    api_execute_plan   = _planning.api_execute_plan
    api_dispatch       = _planning.api_dispatch
    api_confirm_intent = _planning.api_confirm_intent
    debug_intents      = _planning.debug_intents

    # ── Pending-action gate (extracted to pending.py) ──
    _pending_dir         = _pending._pending_dir
    _pending_store       = _pending._pending_store
    _pending_path        = _pending._pending_path
    _actions_log_path    = _pending._actions_log_path
    _save_pending        = _pending._save_pending
    _load_pending        = _pending._load_pending
    _autopilot_data_root = _pending._autopilot_data_root
    _session_scope       = _pending._session_scope
    _is_grant_active     = _pending._is_grant_active
    save_pending_action  = _pending.save_pending_action
    list_pending         = _pending.list_pending
    find_pending_duplicate = _pending.find_pending_duplicate
    apply_pending        = _pending.apply_pending
    reject_pending       = _pending.reject_pending
    _resolve_last_pending = _pending._resolve_last_pending
    voice_confirm_pending = _pending.voice_confirm_pending
    voice_reject_pending  = _pending.voice_reject_pending
    api_pending_list     = _pending.api_pending_list
    api_pending_apply    = _pending.api_pending_apply
    api_pending_reject   = _pending.api_pending_reject
    api_undo             = _pending.api_undo
    api_autopilot_status = _pending.api_autopilot_status
    api_autopilot_grant  = _pending.api_autopilot_grant
    api_autopilot_revoke = _pending.api_autopilot_revoke

    # ── Web research (extracted to research.py) ──
    voice_research       = _research.voice_research

    # ── Memory layer (extracted to memory.py) ──
    _memory_dir            = _memory._memory_dir
    _memory_rel_dir        = _memory._memory_rel_dir
    _memory_slug_from      = staticmethod(_memory._memory_slug_from)
    _memory_cache_get      = _memory._memory_cache_get
    memory_save            = _memory.memory_save
    memory_load_all        = _memory.memory_load_all
    memory_delete          = _memory.memory_delete
    voice_remember         = _memory.voice_remember
    voice_forget           = _memory.voice_forget
    voice_recall           = _memory.voice_recall
    context_memory         = _memory.context_memory
    context_recent_journal = _memory.context_recent_journal
    api_memory_save        = _memory.api_memory_save
    api_memory_list        = _memory.api_memory_list
    api_memory_delete      = _memory.api_memory_delete
    api_debug_context      = _memory.api_debug_context

    # ── Machine interiority — Aura's private journal (extracted to interiority.py) ──
    _interiority_enabled   = _interiority._interiority_enabled
    _interiority_dir       = _interiority._interiority_dir
    reflect                = _interiority.reflect
    share_reflection       = _interiority.share_reflection
    list_shared            = _interiority.list_shared
    voice_reflect          = _interiority.voice_reflect
    api_interiority_shared = _interiority.api_interiority_shared
