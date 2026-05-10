# Streaming chat pipeline — the audio + text path that produces NDJSON events.
# Extracted from VoiceAssistantApp so the class file stays focused on lifecycle,
# state, and HTTP routing. The function takes the app instance and uses its
# capability methods (think_stream, speak, call_app, emit) directly.

import json
import shutil
import uuid

from emptyos.sdk import ndjson_response

from .intents import INTENT_RE


async def chat_response(
    app, user_text: str, messages: list, companion_id: str | None, echo_user_text: bool = True
):
    """Shared post-input chat pipeline used by both audio and text endpoints."""
    messages.append({"role": "user", "content": user_text})

    # Companion switch detection (explicit phrases + LLM intent)
    switch_target = await app._detect_companion_switch(user_text, companion_id)
    if switch_target == "__aura__":
        companion_id = None
    elif switch_target:
        companion_id = switch_target

    system_prompt_text = await app._build_system_prompt(companion_id)
    ambient_context = await app._build_context(companion_id)
    scoped_intents = await app._scope_intents_relevant(user_text, companion_id, messages=messages)
    intent_block = app._intent_prompt_block(scoped_intents)
    system_prompt = f"{system_prompt_text}\n[System Context]\n{ambient_context}\n{intent_block}"
    scoped_verbs = {e.get("verb") for e in scoped_intents}

    async def event_stream():
        if echo_user_text:
            yield {"type": "user_text", "text": user_text}

        # Notify frontend of companion switch
        if switch_target == "__aura__":
            yield {"type": "companion_switch", "companion": None, "name": "Aura"}
        elif switch_target and switch_target in app._companions:
            yield {
                "type": "companion_switch",
                "companion": switch_target,
                "name": app._companions[switch_target].get("name", switch_target),
            }

        try:
            llm_stream = app.think_stream(prompt="", messages=messages, system=system_prompt)
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

        serve_dir = app.data_dir / "audio"
        serve_dir.mkdir(parents=True, exist_ok=True)

        async def speak_sentence(sentence: str):
            try:
                out = await app.speak(sentence)
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
            entry = app._intents[verb]
            try:
                args = json.loads(args_raw)
            except Exception as e:
                yield {"type": "error", "error": f"intent {verb} args not JSON: {e}"}
                return
            ok, msg = app._validate_args(entry.get("args") or {}, args)
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
                result = await app.call_app(app_id, method, **args)
            except Exception as e:
                yield {"type": "error", "error": f"intent {verb} failed: {e}"}
                return
            # Track recency so this app's other intents stay in scope next turn.
            if app_id:
                if app_id in app._recent_apps:
                    app._recent_apps.remove(app_id)
                app._recent_apps.append(app_id)
                app._persist_recent_apps()
            if not isinstance(result, dict):
                return
            say = result.get("say")
            narration = await app._run_narrators(verb, args, result)
            if narration:
                say = f"{say} {narration}".strip() if say else narration
            if say:
                full_reply += (
                    " " if full_reply and not full_reply.endswith((" ", "\n")) else ""
                ) + say
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
                        safe_to = (
                            len(raw_in) - tail_len
                            if tail_len > 0 and "[" in raw_in[len(raw_in) - tail_len :]
                            else len(raw_in)
                        )
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
                    m = INTENT_RE.match(raw_in, bracket)
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
            app._log_chat(user_text, full_reply, companion_id)
            app._purge_audio()
            await app.emit(
                "voice-assistant:chat",
                {
                    "companion": companion_id or "aura",
                    "user": user_text[:200],
                    "assistant": full_reply[:200],
                },
            )

            yield {
                "type": "done",
                "full_text": full_reply,
                "messages": messages,
                "companion": companion_id,  # echoed back so frontend persists it
            }
        except Exception as e:
            yield {"type": "error", "error": str(e)}

    return ndjson_response(event_stream())
