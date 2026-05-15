import datetime
import json
from collections import deque

from emptyos.sdk import BaseApp, ndjson_response, web_route

from .chat_pipeline import chat_response
from .intents import (
    MAX_INTENTS_IN_PROMPT,
    build_plan_dict,
    intent_embedding_text,
    render_intent_block,
    scope_intents,
    scope_intents_by_relevance,
    validate_args,
)
from .prompts import AURA_SYSTEM, COMPANION_CLASSIFY_SYSTEM, PLAN_SYSTEM_FOOT

CONTEXT_BUDGET = 2000  # max chars for assembled context block


class VoiceAssistantApp(BaseApp):
    async def setup(self):
        # Cache companion definitions from all contributing apps at startup.
        self._companions: dict[str, dict] = {}
        for entry, _ in await self.call_contributions("voice-assistant", "companion"):
            cid = entry.get("id")
            if cid:
                self._companions[cid] = entry

        # Cache voice intents (verbs apps expose for the LLM to invoke).
        # call_contributions also runs each contributor's `method`, but for
        # intents we only want the manifest entries — the methods are dispatch
        # targets, not data sources. So read the raw entries via app_loader.
        self._intents: dict[str, dict] = {}
        loader = getattr(self.kernel, "apps", None)
        if loader is not None:
            for entry in loader.get_contributions("voice-assistant", "intent"):
                verb = entry.get("verb")
                if verb:
                    self._intents[verb] = entry

        # Cache narrators — apps register `[[contributes.voice-assistant.narration]]`
        # to append a follow-up sentence after one of their intents fires.
        # Keep the raw list; matching (exact verb or `task.*` namespace prefix) is
        # done per-dispatch in _lookup_narrators.
        self._narrators: list[dict] = []
        if loader is not None:
            for entry in loader.get_contributions("voice-assistant", "narration"):
                if entry.get("intent") and entry.get("method"):
                    self._narrators.append(entry)

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

    def _scope_intents(self, companion_id: str | None) -> list[dict]:
        return scope_intents(self._intents, self._companions, self._recent_apps, companion_id)

    async def _scope_intents_relevant(
        self, user_text: str, companion_id: str | None,
        messages: list[dict] | None = None,
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
            return self._scope_intents(companion_id)
        if not getattr(self, "embeddings_available", False):
            return self._scope_intents(companion_id)
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
                self._intents, self._companions, companion_id, ranked_verbs
            )
        except Exception:
            return self._scope_intents(companion_id)

    def _intent_prompt_block(self, scoped: list[dict]) -> str:
        return render_intent_block(scoped)

    def _validate_args(self, schema: dict, args: dict) -> tuple[bool, str]:
        return validate_args(schema, args)

    # ── Logging + housekeeping ──────────────────────────────────────────────

    def _log_chat(self, user_text: str, reply_text: str, companion_id: str | None = None):
        log_file = self.data_dir / "chat_log.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.datetime.now().isoformat(),
            "companion": companion_id or "aura",
            "user": user_text,
            "assistant": reply_text,
        }
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

    # ── Companion routing ───────────────────────────────────────────────────

    async def _detect_companion_switch(
        self, user_text: str, current_companion: str | None
    ) -> str | None:
        """Two-stage detection: explicit phrases first, then LLM intent classification.

        Returns companion id to switch to, "__aura__" to return to Aura, or None for no switch.
        LLM stage only runs when no companion is active (avoids mis-routing mid-session).
        """
        low = user_text.lower()

        # Stage 1: explicit switch-back
        if any(p in low for p in ("back to aura", "switch back", "talk to aura", "aura again")):
            return "__aura__"

        # Stage 1: explicit trigger phrases declared in manifests
        for cid, entry in self._companions.items():
            if any(t.lower() in low for t in entry.get("triggers", [])):
                return cid

        # Stage 2: LLM intent classification — only when no companion is active
        if current_companion or not self._companions:
            return None

        companion_desc = "\n".join(
            f"- {cid}: {entry.get('name')} ({', '.join(str(t) for t in entry.get('triggers', [])[:3])})"
            for cid, entry in self._companions.items()
        )
        try:
            classification = await self.think(
                f'User said: "{user_text}"\n\nAvailable companions:\n{companion_desc}',
                system=COMPANION_CLASSIFY_SYSTEM,
                domain="reason",
                temperature=0.1,
            )
            result = (classification or "").strip().lower().split()[0]
            if result in self._companions:
                return result
        except Exception:
            pass
        return None

    async def _build_system_prompt(self, companion_id: str | None) -> str:
        """Return Aura's system prompt, or the active companion's persona."""
        if not companion_id or companion_id not in self._companions:
            return AURA_SYSTEM
        entry = self._companions[companion_id]
        try:
            prompt = await self.call_app(entry["_app_id"], entry["system_prompt_method"])
            if prompt:
                return prompt
        except Exception:
            pass
        return AURA_SYSTEM

    async def _build_context(self, companion_id: str | None) -> str:
        """Assemble the [System Context] block.

        Companion mode: fetches that companion's context only.
        Aura mode: layers contributions by declared priority into Critical / Situational / Background tiers.
        """
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [f"Current Date/Time: {now_str}"]

        # ── Companion mode ──────────────────────────────────────────────────
        if companion_id and companion_id in self._companions:
            entry = self._companions[companion_id]
            try:
                ctx = await self.call_app(entry["_app_id"], entry["context_method"])
                if ctx:
                    parts.append(str(ctx))
            except Exception:
                pass
            return "\n\n".join(parts)

        # ── Aura mode: tier-sorted context ──────────────────────────────────
        critical, situational, background = [], [], []

        for entry, result in await self.call_contributions("voice-assistant", "context"):
            if not result:
                continue
            if isinstance(result, dict):
                text = result.get("summary") or result.get("text") or str(result)
                priority = int(result.get("priority", entry.get("priority", 100)))
            else:
                text = str(result)
                priority = int(entry.get("priority", 100))

            if priority < 50:
                critical.append(text)
            elif priority < 150:
                situational.append(text)
            else:
                background.append(text)

        used = len("\n\n".join(parts))
        for label, items in (
            ("** Needs Attention **", critical),
            ("Today's State", situational),
            ("Background", background),
        ):
            if not items:
                continue
            block = f"{label}:\n" + "\n".join(items)
            if used + len(block) <= CONTEXT_BUDGET:
                parts.append(block)
                used += len(block) + 2  # account for \n\n separator
            elif label == "Background":
                trimmed = block[: CONTEXT_BUDGET - used - 3] + "..."
                if trimmed.strip():
                    parts.append(trimmed)

        return "\n\n".join(parts)

    # ── Chat API ────────────────────────────────────────────────────────────

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

        if not user_text or not user_text.strip():
            return ndjson_response(empty_stream("Could not understand audio"))

        return await chat_response(self, user_text, messages, companion_id)

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
        return await chat_response(self, user_text, messages, companion_id, echo_user_text=False)

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

    @web_route("GET", "/api/companions")
    async def api_companions(self, request):
        """List available companions for UI display."""
        return [
            {"id": cid, "name": entry.get("name", cid)} for cid, entry in self._companions.items()
        ]

    # ── Plan-then-execute (Phase 1) ───────────────────────────────────────
    # Voice keeps the inline streaming dispatcher in chat_pipeline.py
    # (single-intent, immediate-fire). Quick-action and any future caller that
    # wants "preview a plan, let the user confirm, then run" uses these
    # methods. The shared shape is intents.find_intents — both paths gain
    # nested-arg support once we route inline through it (later refactor;
    # keep V1 regex for streaming today to avoid touching the working voice loop).

    async def plan_actions(self, user_text: str, scope: str = "full", limit: int = 50) -> dict:
        """LLM-plan a sequence of intent calls for *user_text*. Does NOT execute.

        scope='full' surfaces the entire registry (cap *limit*, default 50).
        scope='voice' uses the same _scope_intents logic the voice path uses.
        """
        if not user_text or not user_text.strip():
            return {"raw_reply": "", "say": "", "calls": [], "error": "empty input"}

        if scope == "voice":
            scoped = self._scope_intents(None)
        else:
            scoped = list(self._intents.values())[: max(1, int(limit))]

        intent_block = self._intent_prompt_block(scoped)
        system = AURA_SYSTEM + intent_block + PLAN_SYSTEM_FOOT
        try:
            reply = await self.think(user_text, system=system, domain="text", temperature=0.2)
        except Exception as e:
            return {"raw_reply": "", "say": "", "calls": [], "error": f"think failed: {e}"}
        return build_plan_dict(reply or "", scoped)

    async def execute_plan(self, plan: dict, only_indices: list[int] | None = None) -> list[dict]:
        """Run plan calls serially. Returns one result dict per requested step.

        Each step is `{"index", "verb", ok|error|skipped, "result"?}`.
        Failed steps are isolated — execution continues. Recent-apps deque is
        updated on each successful call so subsequent voice turns inherit scope.
        """
        calls = plan.get("calls") or []
        indices = (
            list(range(len(calls)))
            if only_indices is None
            else [i for i in only_indices if 0 <= i < len(calls)]
        )
        idx_set = set(indices)
        results: list[dict] = []
        for i, call in enumerate(calls):
            if i not in idx_set:
                results.append({"index": i, "verb": call.get("verb"), "skipped": True})
                continue
            if call.get("error"):
                results.append({"index": i, "verb": call.get("verb"), "error": call["error"]})
                continue
            app_id = call.get("app")
            method = call.get("method")
            if not app_id or not method:
                results.append(
                    {"index": i, "verb": call.get("verb"), "error": "missing app/method"}
                )
                continue
            try:
                res = await self.call_app(app_id, method, **(call.get("args") or {}))
            except Exception as e:
                results.append({"index": i, "verb": call.get("verb"), "error": str(e)})
                continue
            if app_id in self._recent_apps:
                self._recent_apps.remove(app_id)
            self._recent_apps.append(app_id)
            self._persist_recent_apps()
            results.append(
                {
                    "index": i,
                    "verb": call.get("verb"),
                    "ok": True,
                    "result": res if isinstance(res, dict) else {"value": res},
                }
            )
        return results

    @web_route("POST", "/api/plan")
    async def api_plan(self, request):
        """Plan-only — LLM produces a list of intent calls, nothing executes."""
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text required"}
        scope = data.get("scope") or "full"
        limit = data.get("limit") or 50
        return await self.plan_actions(text, scope=scope, limit=limit)

    @web_route("POST", "/api/execute-plan")
    async def api_execute_plan(self, request):
        """Run a plan returned by /api/plan. Optional `only_indices` to skip steps."""
        data = await request.json()
        plan = data.get("plan")
        if not isinstance(plan, dict):
            return {"error": "plan required"}
        only = data.get("only_indices")
        if only is not None and not isinstance(only, list):
            return {"error": "only_indices must be a list"}
        results = await self.execute_plan(plan, only_indices=only)
        return {"results": results}

    @web_route("POST", "/api/dispatch")
    async def api_dispatch(self, request):
        """One-shot: plan + execute in a single call. The "do it for me" path
        once a caller is confident enough not to need the preview step."""
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text required"}
        scope = data.get("scope") or "full"
        limit = data.get("limit") or 50
        plan = await self.plan_actions(text, scope=scope, limit=limit)
        if plan.get("error") or not plan.get("calls"):
            return {"plan": plan, "results": []}
        results = await self.execute_plan(plan)
        return {"plan": plan, "results": results}

    @web_route("POST", "/api/confirm-intent")
    async def api_confirm_intent(self, request):
        """Run an intent that was previously gated by `confirm = true`.

        Frontend calls this after the user approves the confirm dialog. Re-validates
        args (defense-in-depth) and runs the same call_app + recent-apps logic the
        inline dispatcher uses. Returns the intent's `say` / `card` for the UI to
        speak / render.
        """
        data = await request.json()
        verb = (data.get("verb") or "").strip()
        args = data.get("args") or {}
        if not verb or verb not in self._intents:
            return {"error": f"unknown intent: {verb}"}
        entry = self._intents[verb]
        ok, msg = self._validate_args(entry.get("args") or {}, args)
        if not ok:
            return {"error": msg}
        app_id = entry.get("_app_id")
        method = entry.get("method")
        try:
            result = await self.call_app(app_id, method, **args)
        except Exception as e:
            return {"error": str(e)}
        if app_id:
            if app_id in self._recent_apps:
                self._recent_apps.remove(app_id)
            self._recent_apps.append(app_id)
            self._persist_recent_apps()
        out: dict = {"ok": True, "verb": verb}
        if isinstance(result, dict):
            if result.get("say"):
                out["say"] = result["say"]
            card = result.get("card")
            if isinstance(card, dict) and card.get("renderer"):
                out["card"] = {
                    "renderer": card["renderer"],
                    "data": card.get("data"),
                    "title": card.get("title"),
                }
        return out

    @web_route("GET", "/debug/intents")
    async def debug_intents(self, request):
        """Debug surface — full registry plus what's currently in scope.

        Mirrors /hub/debug/panels. Use ?companion=<id> to test the scope window
        for a specific companion.
        """
        companion_id = request.query_params.get("companion") or None
        if companion_id == "":
            companion_id = None
        scoped = self._scope_intents(companion_id)
        scoped_verbs = {e.get("verb") for e in scoped}
        return {
            "registry": [
                {
                    "verb": e.get("verb"),
                    "app": e.get("_app_id"),
                    "method": e.get("method"),
                    "example": e.get("example"),
                    "always": bool(e.get("always")),
                    "description": e.get("description"),
                    "args": e.get("args") or {},
                    "card": e.get("card"),
                }
                for e in self._intents.values()
            ],
            "scoped": sorted(scoped_verbs),
            "companion": companion_id,
            "recent_apps": list(self._recent_apps),
            "max_in_prompt": MAX_INTENTS_IN_PROMPT,
            "narrators": [
                {"intent": e.get("intent"), "app": e.get("_app_id"), "method": e.get("method")}
                for e in self._narrators
            ],
        }

    async def panel_assistant(self) -> dict:
        return {
            "label": "Aura",
            "text": "Ready to help",
            "url": "/voice-assistant/",
            "button_label": "Talk",
        }
