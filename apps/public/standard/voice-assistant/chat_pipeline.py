# Streaming chat pipeline — the audio + text path that produces NDJSON events.
# Extracted from VoiceAssistantApp so the class file stays focused on lifecycle,
# state, and HTTP routing. The function takes the app instance and uses its
# capability methods (think_stream, speak, call_app, emit) directly.

import asyncio
import datetime
import json
import re
import shutil
import time
import uuid

from emptyos.sdk import ndjson_response

from .intents import CARD_RE, EMOTION_RE, INTENT_RE, VALID_EMOTIONS, parse_content_card
from .prompts import PROMPTS

# Cross-turn dedupe applies only to mutation-shaped verbs (add/create/save/…):
# an identical read repeated two minutes later is a legitimate re-ask, but an
# identical add is almost always a double-submit (voice re-send, model retry).
MUTATION_VERB_SUFFIXES = ("add", "create", "save", "capture", "log", "record", "remember")
DEDUPE_WINDOW_S = 120.0


RECENT_TURNS_CONTEXT_BUDGET = 900
RECENT_TURNS_MAX_MESSAGES = 6

# When the page-assistant rail forwards the current page's client-side actions
# (feature.companion-actions.enabled), teach the brain to offer them as one-click
# controls. We use the [BUTTON:label|action(param)] form — a proposed control the
# user clicks — NOT auto-exec [ACTION:], to keep the propose-not-autopilot posture
# the companion rail already renders (page-assistant.js _renderClientActionButtons).
PAGE_ACTIONS_HEADER = (
    "[Page Actions]\n"
    "You are shown in a rail beside the page the user is looking at. To operate a "
    "control ON THIS PAGE, emit a button token inline in your reply: "
    "[BUTTON:Human Label|action_name(param)] — clicking it runs the action on the "
    "page. Write a short natural sentence, then the button(s). Use ONLY the actions "
    "listed below; never invent one. Example: [BUTTON:Start 25-min timer|set_duration(25)]\n"
    "When a page action below can satisfy the request, ALWAYS offer it as a button — "
    "do not route the same operation through a backend intent the page will not show. "
    "Never say an action has already happened: buttons run only when the user clicks "
    "them, so speak in the offering voice (\"here you go\"), not the completed voice "
    "(\"started\").\n"
    "Available page actions:\n"
)


def _suppress_page_app_intents(
    scoped: list[dict], client_actions: list[dict] | None, focus_app: str | None,
) -> list[dict]:
    """Page-declared actions supersede the page's own app intents.

    When the rail carries registered page actions, intents belonging to the
    page's OWN app are dropped from scope: on-page operations must route
    through the declared handles (visible, ✓-marked on the page) rather than
    a backend intent whose effect the page may never show — the failure that
    produced "Focus session started" over a timer still reading 25:00.
    Cross-app intents stay in scope (asking for a task from the Focus page
    still reaches task.add). No actions / unknown page app → unchanged.
    """
    if not client_actions or not focus_app:
        return scoped
    return [e for e in scoped if e.get("_app_id") != focus_app]


def _page_actions_block(client_actions: list[dict] | None) -> str:
    """Render the [Page Actions] instruction from the rail's registered actions.

    Empty / malformed input → "" (byte-identical prompt). Each action is a
    ``{name, params, description}`` descriptor from EOS.registerActions.
    """
    if not client_actions or not isinstance(client_actions, list):
        return ""
    lines = []
    for a in client_actions:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        params = ", ".join(a.get("params") or [])
        desc = a.get("description") or ""
        lines.append(f"- {a['name']}({params}): {desc}".rstrip(": "))
    if not lines:
        return ""
    return "\n" + PAGE_ACTIONS_HEADER + "\n".join(lines) + "\n"


def _recent_turns_context(messages: list[dict] | None) -> str:
    """Compact prior turns into an explicit short-term context block.

    Providers should already receive `messages=...`, but Aura is latency-first
    and uses multiple provider paths. This block makes follow-up references
    robust even when a path pays more attention to the system prompt than to
    earlier chat turns.
    """
    if not messages:
        return ""
    prior = []
    for m in messages[:-1]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        text = re.sub(r"\s+", " ", content).strip()
        if text:
            prior.append((role, text))
    if not prior:
        return ""

    lines = [
        "Earlier in this same conversation, the user already said:",
        "Use these details to resolve follow-ups. Do not ask for them again unless they conflict.",
    ]
    used = sum(len(line) + 1 for line in lines)
    for role, text in prior[-RECENT_TURNS_MAX_MESSAGES:]:
        label = "User" if role == "user" else "Aura"
        line_prefix = f"{label}: "
        room = RECENT_TURNS_CONTEXT_BUDGET - used - len(line_prefix) - 4
        if room < 40:
            break
        if len(text) > min(180, room):
            text = text[: min(177, room)].rstrip() + "..."
        line = line_prefix + text
        if used + len(line) + 1 > RECENT_TURNS_CONTEXT_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _card_summary(renderer: str | None, title: str | None, data) -> dict:
    """Compact log-safe summary of a card — renderer + title + first item
    texts, never full data/HTML. Feeds _log_chat extras so card-shaped
    answers stop vanishing from chat_log.jsonl / /api/history."""
    s: dict = {"renderer": renderer or "", "title": title or ""}
    if isinstance(data, list):
        s["n"] = len(data)
        items = []
        for it in data[:5]:
            if isinstance(it, dict):
                txt = it.get("text") or it.get("title") or it.get("label") or it.get("value") or ""
            else:
                txt = it
            txt = str(txt)[:80]
            if txt:
                items.append(txt)
        if items:
            s["items"] = items
    return s


async def _localize(app, text: str, user_text: str) -> str:
    """Match a fixed system string (gate say, blank-turn fallback) to the
    user's turn language. Dark-flagged (feature.localized-say.enabled) —
    off ⇒ byte-identical English. Script-hint only: Latin input stays
    English rather than guessing es/fr/…; translate() caches + never raises.
    """
    if not app.app_config("feature.localized-say.enabled", False):
        return text
    try:
        from emptyos.sdk.i18n import detect_lang_hint
        lang = detect_lang_hint(user_text)
        if lang and lang != "en":
            return await app.translate(text, to=lang)
    except Exception:
        pass
    return text


async def turn_events(
    app, user_text: str, messages: list, companion_id: str | None, echo_user_text: bool = True,
    speak_prefer: list[str] | None = None, fast: bool = False, text_only: bool = False,
    focus_app: str | None = None, page_context: str | None = None,
    client_actions: list[dict] | None = None, surface: str = "aura",
):
    """Core voice/text turn as an async generator of NDJSON event dicts.

    Yields the same event sequence the browser stream consumes (user_text, text
    deltas, audio, card/link, pending_action, emotion, done/error). Both the
    browser endpoints (via ``chat_response``, wrapped in ``ndjson_response``) and
    the headless device endpoint (``/api/device_turn``, which consumes these
    events server-side and collapses them into one JSON reply) drive this — so a
    device turn reuses the exact think→intent→speak pipeline without forking it.

    ``text_only`` is the text-companion frontend's lever (the page-assistant
    rail, second face of the one companion brain): it suppresses TTS entirely
    (no ``speak_sentence``, so no ``audio`` events) and skips the two-speed fast
    lane (a perceived-audio-latency optimization that means nothing in text).
    Every dispatch/scope/gate/narration/card path stays identical — so the rail
    and Aura run the same brain, differing only by modality. Default False keeps
    Aura (voice + its ⌨ text mode) byte-identical.
    """
    messages.append({"role": "user", "content": user_text})
    recent_context = _recent_turns_context(messages)

    # Stash this turn's retrieval query so the memory context contributor
    # (context_memory) can rank by relevance when ranked-recall is on. Uses
    # the multi-turn query builder so a bare follow-up keeps prior topic.
    from emptyos.sdk.embeddings import build_retrieval_query
    app._recall_query = build_retrieval_query(messages[:-1], user_text)
    # Stash the active companion so memory writes/reads can be namespaced
    # to it when companion-memory-scope is on.
    app._active_companion = companion_id or ""

    # `companion_id` can change on a companion switch (detected inside the heavy
    # build below). Keep the live value in a one-key dict so the inner closures
    # (finalize_turn, dispatch_intent, the `done` event) always read the
    # post-switch companion rather than the stale argument.
    state = {"companion_id": companion_id}

    # ── Heavy pre-stream build (deferred) ───────────────────────────────────
    # Companion-switch detection + the three prompt builds (persona, ambient
    # context fan-out, intent-relevance embed) are the slow part — up to several
    # seconds when caches are cold. Only the escalate / flag-off / verify paths
    # need them, so they are NOT run up front (that would gate even the fast
    # lane behind them). The event stream calls this *after* the fast lane has
    # already spoken. Switch detection (sometimes an LLM round-trip) overlaps a
    # speculative build for the current companion; only an actual switch rebuilds.
    async def build_quality_context():
        cid = companion_id

        async def _build_for(c):
            return await asyncio.gather(
                app._build_system_prompt(c, focus_app=focus_app),
                app._build_context(c, focus_app=focus_app, page_context=page_context),
                app._scope_intents_relevant(user_text, c, messages=messages, focus_app=focus_app),
            )

        switch_task = asyncio.create_task(app._detect_companion_switch(user_text, cid))
        spec_task = asyncio.create_task(_build_for(cid))
        switch_target = await switch_task
        target = cid
        if switch_target == "__aura__":
            target = None
        elif switch_target:
            target = switch_target

        if target != cid:
            spec_task.cancel()
            try:
                await spec_task
            except BaseException:
                pass
            cid = target
            app._active_companion = cid or ""
            sp_text, ctx, scoped = await _build_for(cid)
        else:
            sp_text, ctx, scoped = await spec_task

        state["companion_id"] = cid
        # Page actions supersede the page app's own intents (see helper doc).
        page_actions_on = bool(client_actions) and app.app_config(
            "feature.companion-actions.enabled", False
        )
        if page_actions_on:
            scoped = _suppress_page_app_intents(scoped, client_actions, focus_app)
        now_str = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
        recent_block = (
            f"[Short-term Conversation Context]\n{recent_context}\n"
            if recent_context else ""
        )
        system_prompt = (
            f"{sp_text}\nCurrent date/time: {now_str}\n"
            f"{recent_block}"
            f"[System Context]\n{ctx}\n"
            f"{app._intent_prompt_block(scoped)}"
        )
        # Page-action controls (feature.companion-actions.enabled) — teach the
        # brain to offer the current page's client-side actions as clickable
        # [BUTTON:] tokens. Flag-gated + empty-input-safe, so off ⇒ byte-identical.
        if page_actions_on:
            system_prompt += _page_actions_block(client_actions)
        return {
            "switch_target": switch_target,
            "system_prompt": system_prompt,
            "scoped_intents": scoped,
            "scoped_verbs": {e.get("verb") for e in scoped},
        }

    async def event_stream():
        if echo_user_text:
            yield {"type": "user_text", "text": user_text}

        # ── Shared state + helpers (used by the fast lane AND the quality stream) ──
        SENTENCE_ENDS = (".", "!", "?", "。", "！", "？", "\n")
        MIN_SENTENCE_CHARS = 10

        pending = ""
        full_reply = ""
        produced_card = False  # any non-text visible surface (card/link/gate)
        # Compact summaries of the non-text output this turn, for the chat log
        # (mutated in place by dispatch_intent + the content-card branch).
        visible_extras: dict = {"cards": [], "links": [], "pending": []}
        # Per-turn intent dedupe — a stuttered duplicate [INTENT:] token in one
        # stream dispatches once.
        seen_invocations: set[str] = set()

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
            if text_only:
                return ""  # text-companion rail: no TTS, no audio events
            try:
                # Device turns prefer a WAV provider (kokoro) so a thin client can
                # play the reply without an MP3 decoder; browser turns pass nothing.
                out = await app.speak(sentence, **({"prefer_provider": speak_prefer} if speak_prefer else {}))
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

        async def finalize_turn(reply_text: str):
            """Shared end-of-turn bookkeeping: history, chat log, audio purge, event."""
            cid = state["companion_id"]
            messages.append({"role": "assistant", "content": reply_text})
            app._log_chat(user_text, reply_text, cid, surface=surface, extras=visible_extras)
            app._purge_audio()
            await app.emit(
                "voice-assistant:chat",
                {
                    "companion": cid or "aura",
                    "user": user_text[:200],
                    "assistant": reply_text[:200],
                },
            )

        # ── Two-speed voice (dark-flagged) ──────────────────────────────────
        # SUPERSEDED (2026-07-12) — off by default, and there is now little reason
        # to turn it on. This existed to mask claude-cli's ~12-15s one-shot spawn
        # (which is process startup, NOT the "can't token-stream" claim this
        # comment used to make — see claude_cli.py). Voice now pins openai-mini
        # (think.app.voice-assistant), so the plain quality stream reaches first
        # audio in ~3s on its own. With two-speed ON, the claude-cli verify lane
        # then runs AFTER the answer is spoken and delays the `done` event by ~7s
        # — and the frontend gates the mic re-arm on `done`, so it buys a fast
        # first word at the cost of 7s of deafness before the user can reply.
        # Left in place (flagged, unwired by default) rather than deleted, in case
        # a future slow-but-strong provider needs the same masking.
        #
        # A fast model (openai-mini, ~1.5s) answers simple turns outright; anything
        # needing depth, context, or an action it acknowledges and escalates to the
        # quality model (claude-cli Sonnet). The fast lane
        # runs on a LIGHT prompt (persona + time only) BEFORE the heavy context
        # build, so it answers in ~1.5s even when caches are cold — the heavy build
        # only runs on the escalate path that actually needs it. The fast answer is
        # spoken immediately, then the quality model verifies it: silent CONFIRM, or
        # a trailing spoken correction for a genuine error. Audio is an append-only
        # client queue, so there is deliberately NO mid-utterance takeover (the
        # short fast answer finishes before the verdict returns).
        # Limitations (v1): the fast lane never fires intents (action turns rely on
        # triage emitting [ESCALATE]); two-speed is skipped when a companion is
        # active (companion personas go straight to the quality stream).
        # Live toggle (no restart): settings key `voice-assistant.two-speed`.
        # Static fallback: [apps.voice-assistant] feature.two-speed.enabled.
        _ts = app.setting("voice-assistant.two-speed", None)
        two_speed = (
            bool(_ts) if _ts is not None
            else bool(app.app_config("feature.two-speed.enabled", False))
        )
        bridged = False  # fast lane spoke a non-committal bridge → quality answers below

        if two_speed and not companion_id and not text_only:
            now_str = datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M")
            light_system = (
                f"{PROMPTS.aura_system}\nCurrent date/time: {now_str}\n{PROMPTS.two_speed_triage_foot}"
            )
            if recent_context:
                light_system = (
                    f"{PROMPTS.aura_system}\nCurrent date/time: {now_str}\n"
                    f"[Short-term Conversation Context]\n{recent_context}\n{PROMPTS.two_speed_triage_foot}"
                )
            try:
                fast_raw = await app.think_pinned(
                    "openai-mini", "", "text",
                    messages=messages, system=light_system, temperature=0.3,
                )
            except Exception:
                fast_raw = None
            fast_raw = (fast_raw or "").strip()
            escalate = (not fast_raw) or ("[ESCALATE]" in fast_raw.upper())
            # Strip control tokens defensively — the fast model shouldn't emit
            # intents, but the user must never hear "[INTENT:" / "[EMOTION:".
            clean = re.sub(r"\[ESCALATE\]", "", fast_raw, flags=re.IGNORECASE)
            clean = INTENT_RE.sub("", EMOTION_RE.sub("", clean))
            # Strip any stray bare marker like [JOY] / [NEUTRAL] the fast model
            # may emit despite the prompt — they must never be spoken aloud.
            clean = re.sub(r"\[[A-Za-z]+\]", "", clean).strip()
            if not clean:
                escalate = True  # nothing usable — let the quality stream answer

            if not escalate:
                # Fast model answered — this IS the reply (a fuller second
                # opinion may append below). Only true actions escalate.
                full_reply = clean
                yield {"type": "text", "delta": clean}
                url = await speak_sentence(clean)
                if url:
                    yield {"type": "audio", "url": url, "sentence": clean}

                # Settle the turn's bubble now — the answer stands on its own, so
                # the user shouldn't see a "typing" cursor while the (silent in
                # the common case) second opinion runs behind it. A later
                # correction still appends to this same bubble.
                yield {"type": "settle"}

                # Verify lane — quality model checks the already-spoken draft, on
                # the same light context the draft was made with. Silent CONFIRM,
                # else a stand-alone trailing correction. Runs behind the spoken
                # answer, so its latency never hits perceived reaction time.
                verify_system = (
                    f"{PROMPTS.aura_system}\nCurrent date/time: {now_str}\n{PROMPTS.two_speed_verify_foot}"
                )
                if recent_context:
                    verify_system = (
                        f"{PROMPTS.aura_system}\nCurrent date/time: {now_str}\n"
                        f"[Short-term Conversation Context]\n{recent_context}\n"
                        f"{PROMPTS.two_speed_verify_foot}"
                    )
                try:
                    verdict = await app.think_pinned(
                        "claude-cli",
                        f'User said: "{user_text}"\nFirst-draft reply: "{clean}"',
                        "reason",
                        system=verify_system, model="sonnet", temperature=0.2,
                    )
                except Exception:
                    verdict = None
                verdict = (verdict or "").strip()
                norm = verdict.upper().strip(" .!\"'")
                if verdict and norm != "CONFIRM" and not norm.startswith("CONFIRM"):
                    correction = INTENT_RE.sub("", EMOTION_RE.sub("", verdict)).strip()
                    if correction:
                        full_reply = f"{full_reply} {correction}".strip()
                        yield {"type": "text", "delta": " " + correction}
                        url = await speak_sentence(correction)
                        if url:
                            yield {"type": "audio", "url": url, "sentence": correction}

                await finalize_turn(full_reply)
                yield {
                    "type": "done",
                    "full_text": full_reply,
                    "messages": messages,
                    "companion": state["companion_id"],
                }
                return

            # Escalate — speak the bridge, then fall through to the quality stream.
            if clean:
                yield {"type": "orient", "text": clean}
                url = await speak_sentence(clean)
                if url:
                    yield {"type": "audio", "url": url, "sentence": clean}
            bridged = True

        # ── Quality stream (flag off, companion active, OR two-speed escalate) ──
        # Heavy build happens now — after the fast lane already spoke (if any).
        qc = await build_quality_context()
        switch_target = qc["switch_target"]
        system_prompt = qc["system_prompt"]
        if fast:
            system_prompt += PROMPTS.voice_brief_foot      # curt spoken answers in fast mode
        if text_only:
            system_prompt += PROMPTS.text_rail_foot        # relax markdown/length + allow content cards
        scoped_intents = qc["scoped_intents"]
        scoped_verbs = qc["scoped_verbs"]

        # Notify frontend of companion switch
        if switch_target == "__aura__":
            yield {"type": "companion_switch", "companion": None, "name": "Aura"}
        elif switch_target and switch_target in app._companions:
            yield {
                "type": "companion_switch",
                "companion": switch_target,
                "name": app._companions[switch_target].get("name", switch_target),
            }

        # Pre-turn orient — short "what I'm about to do" preamble. Skipped when
        # the two-speed bridge already spoke its own preamble.
        if not bridged:
            orient_text = app._orient(user_text, scoped_intents)
            if orient_text:
                yield {"type": "orient", "text": orient_text}

        try:
            # Voice runs on Sonnet via claude-cli by default. The claude CLI's
            # own default is Opus, which roughly triples reaction latency for no
            # quality gain on conversational turns (claude-cli -p can't
            # token-stream, so the whole reply is gated on full generation — a
            # lighter model is the only lever). The model= kwarg is read only by
            # the claude provider; openai/ollama ignore it, so chain fallback is
            # unaffected. Override per-machine with [apps.voice-assistant]
            # think_model = "...".
            voice_model = app.app_config("think_model", "sonnet")
            if fast:
                # Fast mode: keep the LLM (don't abandon thinking) but answer on a
                # quick provider instead of the slow quality model (sonnet/claude).
                fast_provider = app.app_config("fast_think_provider", "openai-mini")
                llm_stream = app.think_stream(
                    prompt="", messages=messages, system=system_prompt, provider=fast_provider
                )
            else:
                llm_stream = app.think_stream(
                    prompt="", messages=messages, system=system_prompt, model=voice_model
                )
        except Exception as e:
            yield {"type": "error", "error": f"Failed to think: {e}"}
            return

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
            nonlocal pending, full_reply, produced_card
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
            # Per-turn dedupe: the model sometimes stutters the same [INTENT:]
            # token twice in one stream — dispatch once, drop the rest silently.
            inv_key = f"{verb}|{json.dumps(args, sort_keys=True)}"
            if inv_key in seen_invocations:
                return
            seen_invocations.add(inv_key)
            if entry.get("confirm"):
                # Defer execution to the user. Frontend opens a confirm
                # dialog and POSTs to /api/confirm-intent on approval.
                produced_card = True
                yield {
                    "type": "confirm_required",
                    "verb": verb,
                    "args": args,
                    "description": entry.get("description") or entry.get("example") or verb,
                }
                return
            app_id = entry.get("_app_id")
            method = entry.get("method")
            # Risk-gate: intents marked risk=high go through the pending
            # queue (Apply/Reject card) unless an active session grant
            # covers this verb. Free-form-content verbs (note.create,
            # staff.dispatch) carry risk=high in their manifest.
            risk = (entry.get("risk") or "low").lower()
            if risk == "high" and not app._is_grant_active(verb, companion_id=state["companion_id"]):
                # Repeat request for something already awaiting review? Reuse
                # the existing pending action instead of filing a duplicate —
                # the 2026-06-29 log shows the same gated recipe-add asked 3×
                # producing 3 orphan pending files and the same dead-end say.
                existing = app.find_pending_duplicate(verb, args)
                if existing:
                    saved = existing
                    gate_say = "Still waiting on your confirmation — say yes to apply it, or no to drop it."
                else:
                    saved = await app.save_pending_action(
                        verb=verb,
                        app=app_id,
                        method=method,
                        args=args,
                        description=entry.get("description") or entry.get("example") or verb,
                        say=entry.get("example") or "",
                        companion_id=state["companion_id"],
                    )
                    gate_say = "Want me to apply that? Say yes to confirm."
                # Anchor the follow-up "yes" (aura.confirm) to this action.
                app._last_pending[state["companion_id"] or ""] = saved["id"]
                gate_say = await _localize(app, gate_say, user_text)
                yield {
                    "type": "text",
                    "delta": gate_say,
                }
                full_reply += (" " if full_reply else "") + gate_say
                pending += (" " if pending and not pending.endswith((" ", "\n")) else "") + gate_say
                visible_extras["pending"].append(saved["id"])
                yield {
                    "type": "pending_action",
                    "action_id": saved["id"],
                    "verb": verb,
                    "args": args,
                    "description": saved.get("description") or "",
                }
                return
            # Cross-turn dedupe — MUTATION verbs only (reads repeated across
            # turns are legitimate re-asks): an identical add/create/save within
            # 120s is a double-submit (the log shows "add a task to call mom"
            # twice a minute apart → two tasks). Checked here on the execute
            # path only — gated verbs have their own duplicate handling — and
            # recorded only AFTER a successful call, so a failed attempt never
            # blocks a retry.
            verb_tail = verb.rsplit(".", 1)[-1].lower()
            is_mutation = args and any(verb_tail.startswith(sfx) for sfx in MUTATION_VERB_SUFFIXES)
            recent = getattr(app, "_recent_invocations", None) if is_mutation else None
            if recent is not None:
                now_mono = time.monotonic()
                if any(k == inv_key and now_mono - ts < DEDUPE_WINDOW_S for k, ts in recent):
                    dup_say = "That looks like a repeat of what I just did, so I skipped it — rephrase if you want it again."
                    full_reply += (" " if full_reply else "") + dup_say
                    pending += (" " if pending and not pending.endswith((" ", "\n")) else "") + dup_say
                    yield {"type": "text", "delta": dup_say}
                    return
            try:
                result = await app.call_app(app_id, method, **args)
            except Exception as e:
                yield {"type": "error", "error": f"intent {verb} failed: {e}"}
                return
            if recent is not None:
                recent.append((inv_key, time.monotonic()))
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
                produced_card = True
                visible_extras["cards"].append(_card_summary(card["renderer"], card.get("title"), card.get("data")))
                yield {
                    "type": "card",
                    "intent": verb,
                    "renderer": card["renderer"],
                    "data": card.get("data"),
                    "title": card.get("title"),
                }
            link = result.get("link")
            if isinstance(link, dict) and link.get("href"):
                produced_card = True
                visible_extras["links"].append({"text": link.get("text") or "Open", "href": link["href"]})
                yield {
                    "type": "link",
                    "intent": verb,
                    "text": link.get("text") or "Open",
                    "href": link["href"],
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
                # safe text, dispatch any complete control tokens ([INTENT:…]
                # or the silent [EMOTION:…] affect marker), and stop at a
                # partial token (wait for the next chunk).
                while True:
                    bi = raw_in.find("[INTENT:", emitted_to)
                    be = raw_in.find("[EMOTION:", emitted_to)
                    bc = raw_in.find("[CARD:", emitted_to) if text_only else -1
                    # earliest of the present tokens
                    cands = [p for p in (bi, be, bc) if p != -1]
                    bracket = min(cands) if cands else -1
                    if bracket == -1:
                        # No token ahead. Hold a tail long enough to complete
                        # the longest token prefix split across chunks.
                        hold = len("[EMOTION:") - 1
                        tail_len = min(hold, len(raw_in) - emitted_to)
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
                    # Emit anything before the token first.
                    if bracket > emitted_to:
                        async for ev in emit_segment(raw_in[emitted_to:bracket]):
                            yield ev
                        emitted_to = bracket
                    if bracket == bc:
                        # Content card (text-rail only) — strip the whole
                        # [CARD:...]...[/CARD] block from the prose and surface it
                        # as a card event. Held back until [/CARD] arrives so the
                        # raw marker never flashes mid-stream.
                        m = CARD_RE.match(raw_in, bracket)
                        if not m:
                            break  # block not closed yet — wait for more chunks
                        emitted_to = m.end()
                        card = parse_content_card(m.group(1), m.group(2) or "", m.group(3) or "")
                        if card:
                            produced_card = True
                            visible_extras["cards"].append(
                                _card_summary(card.get("renderer"), card.get("title"), card.get("data"))
                            )
                            yield card
                        continue
                    if bracket == be:
                        # Silent affect marker — strip from text + TTS, surface
                        # as an `emotion` event the frontend lands on the body.
                        m = EMOTION_RE.match(raw_in, bracket)
                        if not m:
                            break  # incomplete token — wait for more chunks
                        emitted_to = m.end()
                        label = m.group(1).lower()
                        if label in VALID_EMOTIONS:
                            yield {"type": "emotion", "emotion": label}
                        continue
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
                tail_text = raw_in[emitted_to:]
                # Drop a dangling, unclosed [CARD: block so a malformed card
                # never leaks as raw prose (a closed one was handled in-loop).
                if text_only:
                    cut = tail_text.find("[CARD:")
                    if cut != -1 and "[/CARD]" not in tail_text[cut:]:
                        tail_text = tail_text[:cut].rstrip()
                async for ev in emit_segment(tail_text):
                    yield ev
                emitted_to = len(raw_in)

            tail = pending.strip()
            if tail:
                url = await speak_sentence(tail)
                if url:
                    yield {"type": "audio", "url": url, "sentence": tail}

            # Never end a turn with a silently-blank reply. full_reply is empty
            # when the model emitted only stripped control tokens (e.g. a lone
            # [EMOTION:joy] marker with no words) or the provider returned an
            # empty stream (cold start). If nothing visible was surfaced at all
            # (no text, no card/link/gate), give the user an honest fallback so
            # the Aura bubble is never blank.
            if not full_reply.strip() and not produced_card:
                fallback = await _localize(
                    app, "Sorry — I blanked for a second there. Could you say that again?", user_text,
                )
                full_reply = fallback
                yield {"type": "text", "delta": fallback}
                url = await speak_sentence(fallback)
                if url:
                    yield {"type": "audio", "url": url, "sentence": fallback}

            await finalize_turn(full_reply)
            yield {
                "type": "done",
                "full_text": full_reply,
                "messages": messages,
                "companion": state["companion_id"],  # echoed back so frontend persists it
            }
        except Exception as e:
            yield {"type": "error", "error": str(e)}

    async for ev in event_stream():
        yield ev


async def chat_response(
    app, user_text: str, messages: list, companion_id: str | None, echo_user_text: bool = True,
    surface: str = "aura",
):
    """Shared post-input chat pipeline for the browser audio + text endpoints.

    Thin wrapper that streams ``turn_events`` as NDJSON. Kept so the existing
    ``/api/chat_stream`` and ``/api/chat_text`` call sites stay byte-identical.
    """
    return ndjson_response(
        turn_events(app, user_text, messages, companion_id, echo_user_text=echo_user_text,
                    surface=surface)
    )
