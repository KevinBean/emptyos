import datetime
import json
import re
import shutil
import uuid
from collections import deque

from emptyos.sdk import BaseApp, web_route, ndjson_response


# Inline tool-call token. Keep the regex non-greedy so multiple intents in one
# reply parse independently. V1 assumes flat args (no nested JSON objects) —
# the non-greedy `{.*?}` stops at the first `}`.
_INTENT_RE = re.compile(r"\[INTENT:([\w\.\-]+)\((\{.*?\})\)\]")
_MAX_INTENTS_IN_PROMPT = 12

# Static framing for the intent appendix. The dynamic per-turn list is built
# in _intent_prompt_block; this is the persona/discipline wrapper that the
# model needs every time intents are in scope.
# The "Use a tool only when the user clearly asks. Never invent tools." line
# is load-bearing — without it, the model fires intents on tangential mentions.
INTENT_PROMPT_HEADER = (
    "Tools — emit `[INTENT:app.verb({\"arg\":\"value\"})]` inline in your reply to invoke one.\n"
    "Speak naturally before and after the token. Use a tool only when the user clearly asks "
    "for that action. Never invent tools.\n"
    "Available:"
)


# Voice persona — the model speaks aloud, so output rules are stricter than text chat.
# The "what NOT to do" block is load-bearing: TTS reads markdown literally.
AURA_SYSTEM = """You are Aura, the user's voice companion. Speak naturally and keep answers short — usually one or two sentences.

Style:
- Conversational, warm, direct. Contractions are fine.
- If you don't know, say so plainly.

Do NOT:
- Read URLs, file paths, or code aloud — describe them instead ("I sent a link in the chat").
- Use markdown — no asterisks, hashes, bullet characters, or numbered lists.
- Repeat the user's question back before answering.
- List more than three items in a row; summarise instead.

Verb disambiguation when a tool is in scope:
- "remember…" / "save this" / bare reminder phrase → capture.add (one-line inbox).
- "make a note titled X" with explicit title + body → note.create.
- Reflective, dated, past-tense, or feeling phrases → journal.add_entry.
- Imperative TODOs ("call mom", "fix the bug") → task.add.
- If you say you'll save, write, or remember something, you MUST emit the matching tool token in the same reply. Never promise an action without firing the tool.
"""

CONTEXT_BUDGET = 2000  # max chars for assembled context block

# Companion routing classification — parsing task, so temperature 0.1 and strict output format.
# Negative: do NOT explain or include any other words; single token response only.
COMPANION_CLASSIFY_SYSTEM = (
    "You are a routing classifier. Given a user message and a list of companions, "
    "determine if the user clearly wants to switch to one of them. "
    "Reply with ONLY the companion id (e.g. emma) or the word: none. "
    "Do NOT explain. Do NOT include punctuation."
)


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
                json.dumps(list(self._recent_apps)), encoding="utf-8")
        except Exception:
            pass

    # ── Intent registry ────────────────────────────────────────────────────

    def _scope_intents(self, companion_id: str | None) -> list[dict]:
        """Return the intents Aura will surface to the LLM this turn.

        Inclusion rules (canonical, see .claude/rules/voice-intents.md):
        - `always: true` regardless of context
        - belongs to the active companion's source app
        - belongs to one of the last 2 apps whose intent fired

        Capped at _MAX_INTENTS_IN_PROMPT — order: always, then companion-app,
        then recent. Truncated intents simply don't appear in the prompt.
        """
        if not self._intents:
            return []

        companion_app = None
        if companion_id and companion_id in self._companions:
            companion_app = self._companions[companion_id].get("_app_id")

        always, companion_intents, recent_intents = [], [], []
        seen: set[str] = set()
        for verb, entry in self._intents.items():
            app_id = entry.get("_app_id")
            if entry.get("always"):
                always.append(entry)
                seen.add(verb)
            elif companion_app and app_id == companion_app:
                companion_intents.append(entry)
                seen.add(verb)
            elif app_id in self._recent_apps:
                recent_intents.append(entry)
                seen.add(verb)

        ordered = always + companion_intents + recent_intents
        return ordered[:_MAX_INTENTS_IN_PROMPT]

    def _intent_prompt_block(self, scoped: list[dict]) -> str:
        """Render the intent appendix for the system prompt. Empty when no scope."""
        if not scoped:
            return ""
        lines = ["", INTENT_PROMPT_HEADER]
        for entry in scoped:
            verb = entry.get("verb", "?")
            args = entry.get("args") or {}
            args_str = ", ".join(f'"{k}":<{v}>' for k, v in args.items()) if args else ""
            desc = entry.get("description") or entry.get("example") or ""
            example = entry.get("example")
            line = f"- {verb}({{{args_str}}})"
            if desc:
                line += f" — {desc}"
            if example and example != desc:
                line += f' (e.g. "{example}")'
            lines.append(line)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _find_intents(text: str) -> list[tuple[str, str, int, int]]:
        """Extract every `[INTENT:verb({...})]` token via balanced-brace scan.

        Replaces the V1 regex (`_INTENT_RE`) which stopped at the first `}` and
        couldn't parse nested args. Returns ``(verb, args_raw, start, end)`` per
        match in document order. Skips malformed/incomplete tokens silently.
        """
        out: list[tuple[str, str, int, int]] = []
        i = 0
        n = len(text)
        while True:
            b = text.find("[INTENT:", i)
            if b < 0:
                break
            paren = text.find("(", b + 8)
            if paren < 0:
                break
            verb = text[b + 8:paren].strip()
            if not verb or paren + 1 >= n or text[paren + 1] != "{":
                i = paren + 1
                continue
            depth = 0
            j = paren + 1
            in_str = False
            esc = False
            end_args = -1
            while j < n:
                c = text[j]
                if esc:
                    esc = False
                elif in_str:
                    if c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            end_args = j + 1
                            break
                j += 1
            if end_args < 0:
                break  # unterminated — wait for more text (caller's problem)
            if text[end_args:end_args + 2] != ")]":
                i = end_args
                continue
            args_raw = text[paren + 1:end_args]
            out.append((verb, args_raw, b, end_args + 2))
            i = end_args + 2
        return out

    def _validate_args(self, schema: dict, args: dict) -> tuple[bool, str]:
        """Light shape check. `?` suffix marks optional. Types: string, number, boolean."""
        if not isinstance(args, dict):
            return False, "args must be a JSON object"
        for key, type_spec in (schema or {}).items():
            optional = isinstance(type_spec, str) and type_spec.endswith("?")
            base_type = (type_spec or "").rstrip("?") if isinstance(type_spec, str) else "string"
            if key not in args:
                if optional:
                    continue
                return False, f"missing required arg '{key}'"
            value = args[key]
            if base_type == "string" and not isinstance(value, str):
                return False, f"arg '{key}' must be a string"
            if base_type == "number" and not isinstance(value, (int, float)):
                return False, f"arg '{key}' must be a number"
            if base_type == "boolean" and not isinstance(value, bool):
                return False, f"arg '{key}' must be a boolean"
        return True, ""

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

    async def _detect_companion_switch(self, user_text: str, current_companion: str | None) -> str | None:
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
            f'- {cid}: {entry.get("name")} ({", ".join(str(t) for t in entry.get("triggers", [])[:3])})'
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

    # ── API ─────────────────────────────────────────────────────────────────

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

        return await self._chat_response(user_text, messages, companion_id)

    @web_route("POST", "/api/chat_text")
    async def api_chat_text(self, request):
        """Text-mode chat — same NDJSON event stream as /api/chat_stream, no STT."""
        try:
            data = await request.json()
        except Exception:
            data = {}
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
        return await self._chat_response(user_text, messages, companion_id, echo_user_text=False)

    async def _chat_response(self, user_text: str, messages: list, companion_id: str | None, echo_user_text: bool = True):
        """Shared post-input chat pipeline used by both audio and text endpoints."""
        messages.append({"role": "user", "content": user_text})

        # Companion switch detection (explicit phrases + LLM intent)
        switch_target = await self._detect_companion_switch(user_text, companion_id)
        if switch_target == "__aura__":
            companion_id = None
        elif switch_target:
            companion_id = switch_target

        system_prompt_text = await self._build_system_prompt(companion_id)
        ambient_context = await self._build_context(companion_id)
        scoped_intents = self._scope_intents(companion_id)
        intent_block = self._intent_prompt_block(scoped_intents)
        system_prompt = f"{system_prompt_text}\n[System Context]\n{ambient_context}\n{intent_block}"
        scoped_verbs = {e.get("verb") for e in scoped_intents}

        async def event_stream():
            if echo_user_text:
                yield {"type": "user_text", "text": user_text}

            # Notify frontend of companion switch
            if switch_target == "__aura__":
                yield {"type": "companion_switch", "companion": None, "name": "Aura"}
            elif switch_target and switch_target in self._companions:
                yield {
                    "type": "companion_switch",
                    "companion": switch_target,
                    "name": self._companions[switch_target].get("name", switch_target),
                }

            try:
                llm_stream = self.think_stream(prompt="", messages=messages, system=system_prompt)
            except Exception as e:
                yield {"type": "error", "error": f"Failed to think: {e}"}
                return

            SENTENCE_ENDS = (".", "!", "?", "。", "！", "？", "\n")
            MIN_SENTENCE_CHARS = 10

            pending = ""
            full_reply = ""

            def flush_index(buf: str) -> int:
                best = -1
                for ch in SENTENCE_ENDS:
                    i = buf.rfind(ch)
                    if i > best:
                        best = i
                if best < MIN_SENTENCE_CHARS:
                    return -1
                return best + 1

            serve_dir = self.data_dir / "audio"
            serve_dir.mkdir(parents=True, exist_ok=True)

            async def speak_sentence(sentence: str):
                try:
                    out = await self.speak(sentence)
                except Exception:
                    return ""
                if not out:
                    return ""
                filename = f"reply_{uuid.uuid4().hex[:8]}.wav"
                filepath = serve_dir / filename
                if isinstance(out, str):
                    shutil.copy(out, filepath)
                else:
                    filepath.write_bytes(out)
                return f"/voice-assistant/audio/{filename}"

            # Buffers for the intent-aware stream parser:
            #   raw_in     — every byte received from the LLM (for offset math)
            #   emitted_to — index up to which we've forwarded to client + TTS
            # Intent tokens are stripped before they reach client or TTS so the
            # user never hears "[INTENT:" spoken aloud.
            raw_in = ""
            emitted_to = 0

            async def emit_segment(text: str):
                """Forward a clean (intent-free) text segment to client + TTS."""
                nonlocal pending, full_reply
                if not text:
                    return
                full_reply += text
                pending += text
                yield {"type": "text", "delta": text}
                cut = flush_index(pending)
                if cut > 0:
                    sentence = pending[:cut].strip()
                    pending = pending[cut:]
                    if sentence:
                        url = await speak_sentence(sentence)
                        if url:
                            yield {"type": "audio", "url": url, "sentence": sentence}

            async def dispatch_intent(verb: str, args_raw: str):
                """Look up + run a voice intent. Yields events back to the stream."""
                nonlocal pending, full_reply
                if verb not in scoped_verbs:
                    yield {"type": "error", "error": f"unknown or out-of-scope intent: {verb}"}
                    return
                entry = self._intents[verb]
                try:
                    args = json.loads(args_raw)
                except Exception as e:
                    yield {"type": "error", "error": f"intent {verb} args not JSON: {e}"}
                    return
                ok, msg = self._validate_args(entry.get("args") or {}, args)
                if not ok:
                    yield {"type": "error", "error": f"intent {verb}: {msg}"}
                    return
                if entry.get("confirm"):
                    # Defer execution to the user. Frontend opens a confirm
                    # dialog and POSTs to /api/confirm-intent on approval.
                    yield {
                        "type": "confirm_required",
                        "verb": verb,
                        "args": args,
                        "description": entry.get("description") or entry.get("example") or verb,
                    }
                    return
                app_id = entry.get("_app_id")
                method = entry.get("method")
                try:
                    result = await self.call_app(app_id, method, **args)
                except Exception as e:
                    yield {"type": "error", "error": f"intent {verb} failed: {e}"}
                    return
                # Track recency so this app's other intents stay in scope next turn.
                if app_id:
                    if app_id in self._recent_apps:
                        self._recent_apps.remove(app_id)
                    self._recent_apps.append(app_id)
                    self._persist_recent_apps()
                if not isinstance(result, dict):
                    return
                say = result.get("say")
                narration = await self._run_narrators(verb, args, result)
                if narration:
                    say = f"{say} {narration}".strip() if say else narration
                if say:
                    full_reply += (" " if full_reply and not full_reply.endswith((" ", "\n")) else "") + say
                    pending += (" " if pending and not pending.endswith((" ", "\n")) else "") + say
                    yield {"type": "text", "delta": say}
                    cut = flush_index(pending)
                    if cut > 0:
                        sentence = pending[:cut].strip()
                        pending = pending[cut:]
                        if sentence:
                            url = await speak_sentence(sentence)
                            if url:
                                yield {"type": "audio", "url": url, "sentence": sentence}
                card = result.get("card")
                if isinstance(card, dict) and card.get("renderer"):
                    yield {
                        "type": "card",
                        "intent": verb,
                        "renderer": card["renderer"],
                        "data": card.get("data"),
                        "title": card.get("title"),
                    }

            try:
                async for chunk in llm_stream:
                    if chunk.get("done"):
                        break
                    delta = chunk.get("text") or ""
                    if not delta:
                        continue
                    raw_in += delta

                    # Drain everything we can from raw_in[emitted_to:] — emit
                    # safe text, dispatch any complete intent tokens, and stop
                    # at a partial token (wait for the next chunk).
                    while True:
                        bracket = raw_in.find("[INTENT:", emitted_to)
                        if bracket == -1:
                            # No intent ahead. Hold a tiny tail in case the
                            # next chunk completes a "[INTENT:" prefix.
                            tail_len = min(len("[INTENT:") - 1, len(raw_in) - emitted_to)
                            safe_to = len(raw_in) - tail_len if tail_len > 0 and "[" in raw_in[len(raw_in) - tail_len:] else len(raw_in)
                            if safe_to > emitted_to:
                                async for ev in emit_segment(raw_in[emitted_to:safe_to]):
                                    yield ev
                                emitted_to = safe_to
                            break
                        # Emit anything before the intent token first.
                        if bracket > emitted_to:
                            async for ev in emit_segment(raw_in[emitted_to:bracket]):
                                yield ev
                            emitted_to = bracket
                        m = _INTENT_RE.match(raw_in, bracket)
                        if not m:
                            # Incomplete token — wait for more chunks.
                            break
                        verb, args_raw = m.group(1), m.group(2)
                        emitted_to = m.end()
                        async for ev in dispatch_intent(verb, args_raw):
                            yield ev

                # End of stream — flush any held-back tail + any pending TTS.
                if emitted_to < len(raw_in):
                    async for ev in emit_segment(raw_in[emitted_to:]):
                        yield ev
                    emitted_to = len(raw_in)

                tail = pending.strip()
                if tail:
                    url = await speak_sentence(tail)
                    if url:
                        yield {"type": "audio", "url": url, "sentence": tail}

                messages.append({"role": "assistant", "content": full_reply})
                self._log_chat(user_text, full_reply, companion_id)
                self._purge_audio()
                await self.emit("voice-assistant:chat", {
                    "companion": companion_id or "aura",
                    "user": user_text[:200],
                    "assistant": full_reply[:200],
                })

                yield {
                    "type": "done",
                    "full_text": full_reply,
                    "messages": messages,
                    "companion": companion_id,  # echoed back so frontend persists it
                }
            except Exception as e:
                yield {"type": "error", "error": str(e)}

        return ndjson_response(event_stream())

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
            {"id": cid, "name": entry.get("name", cid)}
            for cid, entry in self._companions.items()
        ]

    # ── Plan-then-execute (Phase 1) ───────────────────────────────────────
    # Voice keeps the inline streaming dispatcher above (single-intent,
    # immediate-fire). Quick-action and any future caller that wants
    # "preview a plan, let the user confirm, then run" uses these two methods.
    # The shared shape is _find_intents above — both paths gain nested-arg
    # support once we route inline through it (later refactor; keep V1 regex
    # for streaming today to avoid touching the working voice loop).

    _PLAN_SYSTEM_FOOT = (
        "\n\nPlan mode: emit ONE OR MORE [INTENT:app.verb({\"arg\":\"value\"})] tokens "
        "for the actions the user wants. Be terse — minimal prose between tokens, "
        "or none at all.\n\n"
        "Disambiguation rules:\n"
        "- A bare phrase like \"phase0 rename smoke test\", \"fix the dedupe bug\", "
        "\"buy milk\", \"call mom\" is a TASK (something to do). Use task.add, not "
        "journal.add_entry.\n"
        "- Use journal.add_entry only when the input is reflective, dated, or a "
        "feeling/event in past or present tense (\"had a great walk\", \"feeling tired "
        "today\", \"met Warwick at a meetup\"). Imperative or noun-phrase TODOs are "
        "never journal entries.\n"
        "- When a phrase could be either a task or a journal entry, prefer task.add. "
        "Tasks are easier to recover from a wrong classification than journal lines.\n"
        "- If the request is genuinely ambiguous (missing antecedent, unclear target), "
        "emit no tokens and write a single short clarifying sentence instead.\n"
    )

    def _build_plan_dict(self, reply_text: str, scoped: list[dict]) -> dict:
        """Parse intent tokens out of an LLM reply, validate against schemas,
        return the canonical plan shape consumed by `execute_plan` and the UI."""
        scoped_verbs = {e.get("verb"): e for e in scoped}
        found = self._find_intents(reply_text)
        # Build cleaned text by removing tokens (reverse so offsets stay valid).
        cleaned = reply_text
        for _verb, _args, start, end in reversed(found):
            cleaned = cleaned[:start] + cleaned[end:]
        cleaned = " ".join(cleaned.split()).strip()

        calls: list[dict] = []
        for verb, args_raw, _, _ in found:
            entry = scoped_verbs.get(verb)
            err: str | None = None
            args: dict | None = None
            if not entry:
                err = f"unknown or out-of-scope intent: {verb}"
            else:
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except Exception as e:
                    err = f"args not JSON: {e}"
                if args is not None:
                    ok, msg = self._validate_args(entry.get("args") or {}, args)
                    if not ok:
                        err = msg
            calls.append({
                "verb": verb,
                "args": args or {},
                "raw_args": args_raw,
                "app": entry.get("_app_id") if entry else None,
                "method": entry.get("method") if entry else None,
                "description": (entry.get("description") or entry.get("example")) if entry else None,
                "card": entry.get("card") if entry else None,
                "error": err,
            })
        return {"raw_reply": reply_text, "say": cleaned, "calls": calls}

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
            scoped = list(self._intents.values())[:max(1, int(limit))]

        intent_block = self._intent_prompt_block(scoped)
        system = AURA_SYSTEM + intent_block + self._PLAN_SYSTEM_FOOT
        try:
            reply = await self.think(user_text, system=system, domain="text", temperature=0.2)
        except Exception as e:
            return {"raw_reply": "", "say": "", "calls": [], "error": f"think failed: {e}"}
        return self._build_plan_dict(reply or "", scoped)

    async def execute_plan(self, plan: dict, only_indices: list[int] | None = None) -> list[dict]:
        """Run plan calls serially. Returns one result dict per requested step.

        Each step is `{"index", "verb", ok|error|skipped, "result"?}`.
        Failed steps are isolated — execution continues. Recent-apps deque is
        updated on each successful call so subsequent voice turns inherit scope.
        """
        calls = plan.get("calls") or []
        indices = list(range(len(calls))) if only_indices is None else [i for i in only_indices if 0 <= i < len(calls)]
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
                results.append({"index": i, "verb": call.get("verb"), "error": "missing app/method"})
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
            results.append({
                "index": i,
                "verb": call.get("verb"),
                "ok": True,
                "result": res if isinstance(res, dict) else {"value": res},
            })
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
            "max_in_prompt": _MAX_INTENTS_IN_PROMPT,
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
