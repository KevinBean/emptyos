"""Voice-assistant — companion routing + the page-assistant text/voice rail.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: companion-switch detection (trigger phrases + LLM classify),
the per-turn system prompt + tiered [System Context] assembly, and the
companion rail endpoints (the second face of the one turn_events brain —
text + voice turns collapsed to single JSON replies for page-assistant.js).
chat_pipeline.py reaches _build_system_prompt / _build_context /
_detect_companion_switch through ``app.<name>`` after re-binding.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``turn_events`` (chat_pipeline) for rail turns.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

import datetime
import json
import re
from typing import TYPE_CHECKING

from emptyos.sdk import ndjson_response, web_route

from .chat_pipeline import turn_events
from .shared import is_stt_artifact
from .prompts import PROMPTS

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only

CONTEXT_BUDGET = 2000  # max chars for assembled context block


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _companion_enabled        = _companions._companion_enabled
#   _detect_companion_switch  = _companions._detect_companion_switch
#   _build_system_prompt      = _companions._build_system_prompt
#   _build_context            = _companions._build_context
#   _append_focus_context     = _companions._append_focus_context
#   api_companion_status      = _companions.api_companion_status
#   api_companion_turn        = _companions.api_companion_turn
#   api_companion_turn_stream = _companions.api_companion_turn_stream
#   api_companion_voice_turn  = _companions.api_companion_voice_turn
#   api_companions            = _companions.api_companions
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


def companions_from(loader) -> dict[str, dict]:
    """Companion declarations by id, read straight from the app loader.

    A companion entry is a declaration, not a call: it names
    ``system_prompt_method`` / ``context_method`` and carries no ``method``
    key. ``call_contributions`` skips method-less entries, so loading through
    it returned none of them and every companion was silently unreachable.
    Module-level (not bound onto the app) so it is testable without a kernel.
    """
    if loader is None:
        return {}
    out: dict[str, dict] = {}
    for entry in loader.get_contributions("voice-assistant", "companion"):
        cid = entry.get("id")
        if cid:
            out[cid] = entry
    return out


_ADDRESS_LEAD = r"(?:hey|hi|hello|ok|okay)\s+"


def trigger_matches(trigger: str, text: str) -> bool:
    """Does ``text`` invoke a companion trigger?

    A phrase ("talk to Sage") matches on word boundaries. A bare name ("Sage",
    "Jing", "静") counts only when the user addresses the companion: the whole
    utterance, a leading "Jing, …", or "hey Jing". Plain substring matching
    switched persona on "message" (sage), "Beijing" (jing), "dilemma" (emma)
    and "relevance" (vance).
    """
    t = str(trigger or "").strip().lower()
    low = str(text or "").strip().lower()
    if not t or not low:
        return False
    word = re.escape(t)
    if re.search(r"\s", t):
        return re.search(rf"(?<!\w){word}(?!\w)", low) is not None
    return bool(
        re.fullmatch(rf"\W*{word}\W*", low)
        or re.match(rf"\W*(?:{_ADDRESS_LEAD})?{word}\s*[,，:：!！]", low)
        or re.match(rf"\W*{_ADDRESS_LEAD}{word}(?!\w)", low)
    )


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
        if any(trigger_matches(t, user_text) for t in entry.get("triggers", [])):
            return cid

    # Stage 2: LLM intent classification — only when no companion is active.
    # Dark: it costs a think call on every plain Aura turn and sends the user's
    # words to the chain's first provider. It never ran while companions failed
    # to load, so turning it on is a decision, not a side effect of that fix.
    if current_companion or not self._companions:
        return None
    if not self.app_config("feature.companion-classify.enabled", False):
        return None

    companion_desc = "\n".join(
        f"- {cid}: {entry.get('name')} ({', '.join(str(t) for t in entry.get('triggers', [])[:3])})"
        for cid, entry in self._companions.items()
    )
    try:
        classification = await self.think(
            f'User said: "{user_text}"\n\nAvailable companions:\n{companion_desc}',
            system=PROMPTS.companion_classify_system,
            domain="reason",
            temperature=0.1,
        )
        result = (classification or "").strip().lower().split()[0]
        if result in self._companions:
            return result
    except Exception:
        pass
    return None


async def _build_system_prompt(self, companion_id: str | None, focus_app: str | None = None) -> str:
    """Return Aura's system prompt, or the active companion's persona.

    When `focus_app` is set (the "📍 focus on this page" toggle) and no
    persona is active, append a one-line hint so the model concentrates on
    the app the user is looking at — its verbs are already prioritized in
    scope; this tells the model to prefer them."""
    if not companion_id or companion_id not in self._companions:
        if focus_app:
            return PROMPTS.aura_system + PROMPTS.focus_hint_foot.format(app=focus_app)
        return PROMPTS.aura_system
    entry = self._companions[companion_id]
    try:
        prompt = await self.call_app(entry["_app_id"], entry["system_prompt_method"])
        if prompt:
            return prompt
    except Exception:
        pass
    return PROMPTS.aura_system


async def _build_context(
    self, companion_id: str | None, focus_app: str | None = None,
    page_context: str | None = None,
) -> str:
    """Assemble the [System Context] block.

    Companion mode: fetches that companion's context only.
    Aura mode: layers contributions by declared priority into Critical / Situational / Background tiers.

    When ``feature.companion-focus-context.enabled`` and the 📍 focus toggle is
    on, two extra blocks are appended (both within CONTEXT_BUDGET): the focused
    app's live state via its generic ``companion_context`` hook, and the
    client-supplied visible page context. Dark by default → byte-identical.
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
        await self._append_focus_context(parts, focus_app, page_context)
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

    await self._append_focus_context(parts, focus_app, page_context)
    return "\n\n".join(parts)


async def _append_focus_context(self, parts, focus_app, page_context):
    """When focus + the focus-context flag are on, append the focused app's
    live state (its generic ``companion_context`` hook) and the client's
    visible page context. Mutates ``parts`` in place; caps each block so the
    two together can't blow the budget. Fail-soft — never raises."""
    if not focus_app and not page_context:
        return
    if not self.app_config("feature.companion-focus-context.enabled", False):
        return
    if focus_app:
        try:
            app_ctx = await self.call_app(focus_app, "companion_context")
            if app_ctx:
                parts.append(f"[Focused App: {focus_app}]\n{str(app_ctx)[:1200]}")
        except Exception:
            pass  # app didn't implement the hook, or it errored — skip
    if page_context:
        parts.append(f"[Focused Page Context]\n{str(page_context)[:1200]}")


# ── Companion text rail (second face of the one brain) ───────────────────
# The page-assistant rail (emptyos/web/static/page-assistant.js) is the text
# frontend over the SAME turn_events brain Aura's voice surface uses. It posts
# text here; we run turn_events(text_only=True) and collapse the NDJSON stream
# into one JSON reply (mirroring /api/device_turn). Same dispatch, scope,
# risk-gate, session grants, narration, and cards — only modality differs.
# Gated by [apps.voice-assistant] feature.companion-frame.enabled (default
# dark); when off the rail keeps routing to rooms (see page-assistant.js).


def _companion_enabled(self) -> bool:
    return bool(self.app_config("feature.companion-frame.enabled", False))


@web_route("GET", "/api/companion/status")
async def api_companion_status(self, request):
    """Cheap feature-detect for the rail: should it switch to companion mode,
    and which interactive sub-features are live?

    The rail reads this once on load and lights up streaming / form cards /
    proactive nudges accordingly. All sub-flags are meaningful only when
    ``enabled`` (the master companion-frame flag) is true; off ⇒ the rail
    routes to rooms and ignores them.
    """
    on = self._companion_enabled()
    return {
        "enabled": on,
        "stream": on and bool(self.app_config("feature.companion-stream.enabled", False)),
        "forms": on and bool(self.app_config("feature.companion-forms.enabled", False)),
        "proactive": on and bool(self.app_config("feature.companion-proactive.enabled", False)),
        "actions": on and bool(self.app_config("feature.companion-actions.enabled", False)),
    }


@web_route("POST", "/api/companion_turn")
async def api_companion_turn(self, request):
    """Text-companion turn — collapses turn_events(text_only=True) into one
    JSON reply the page-assistant rail renders (markdown say + cards + links +
    pending Apply/Reject). The text analogue of /api/device_turn.

    Request JSON: {text, messages?, companion?}
    Response JSON: {reply_text, cards[], links[], pending[], confirm[],
                    session:{messages, companion}}  — or {disabled:true}.
    """
    if not self._companion_enabled():
        return {"disabled": True}
    data = await self.safe_json(request)
    user_text = (data.get("text") or "").strip()
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    companion_id = data.get("companion") or None
    focus_app = (data.get("focus_app") or "").strip() or None
    page_context = (data.get("context") or "").strip() or None
    client_actions = data.get("client_actions") if isinstance(data.get("client_actions"), list) else None
    if not user_text:
        return {"error": "text required"}

    reply_text = ""
    cards: list[dict] = []
    links: list[dict] = []
    pending: list[dict] = []
    confirm: list[dict] = []
    session = {"messages": messages, "companion": companion_id}

    async for ev in turn_events(
        self, user_text, messages, companion_id,
        echo_user_text=False, text_only=True, focus_app=focus_app,
        page_context=page_context, client_actions=client_actions,
        surface="rail",
    ):
        t = ev.get("type")
        if t == "text":
            reply_text += ev.get("delta", "")
        elif t == "card":
            cards.append({"intent": ev.get("intent", ""), "renderer": ev.get("renderer"),
                          "data": ev.get("data"), "title": ev.get("title")})
        elif t == "link":
            links.append({"intent": ev.get("intent", ""), "text": ev.get("text") or "Open",
                          "href": ev.get("href")})
        elif t == "pending_action":
            pending.append({"action_id": ev.get("action_id"), "verb": ev.get("verb", ""),
                            "args": ev.get("args") or {}, "description": ev.get("description") or ""})
        elif t == "confirm_required":
            confirm.append({"verb": ev.get("verb", ""), "args": ev.get("args") or {},
                            "description": ev.get("description") or ""})
        elif t == "companion_switch":
            session["companion"] = ev.get("companion")
        elif t == "error":
            return {"error": ev.get("error", "turn failed"), "transcript": user_text}
        elif t == "done":
            session["messages"] = ev.get("messages", messages)
            session["companion"] = ev.get("companion")
            reply_text = ev.get("full_text") or reply_text

    return {
        "reply_text": reply_text.strip(),
        "cards": cards,
        "links": links,
        "pending": pending,
        "confirm": confirm,
        "session": session,
    }


@web_route("POST", "/api/companion_turn_stream")
async def api_companion_turn_stream(self, request):
    """Streaming twin of /api/companion_turn — NDJSON straight off the same
    turn_events brain so the rail paints tokens/cards/links as they arrive
    instead of waiting for the collapsed reply.

    Same request shape as /api/companion_turn. Emits the raw turn_events event
    dicts (type: text|card|link|pending_action|confirm_required|
    companion_switch|done|error). Gated by feature.companion-stream.enabled on
    top of companion-frame; when either is off the collapse endpoint is used
    instead (the rail feature-detects via /api/companion/status). Voice turns
    keep the collapse path (they need the audio_urls collected).
    """
    if not self._companion_enabled():
        return {"disabled": True}
    if not self.app_config("feature.companion-stream.enabled", False):
        return {"disabled": True, "reason": "stream off"}
    data = await self.safe_json(request)
    user_text = (data.get("text") or "").strip()
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    companion_id = data.get("companion") or None
    focus_app = (data.get("focus_app") or "").strip() or None
    page_context = (data.get("context") or "").strip() or None
    client_actions = data.get("client_actions") if isinstance(data.get("client_actions"), list) else None
    if not user_text:
        return {"error": "text required"}

    return ndjson_response(
        turn_events(
            self, user_text, messages, companion_id,
            echo_user_text=False, text_only=True, focus_app=focus_app,
            page_context=page_context, client_actions=client_actions,
            surface="rail",
        )
    )


@web_route("POST", "/api/companion_voice_turn")
async def api_companion_voice_turn(self, request):
    """Voice turn for the page-assistant rail — record in place, hear the
    reply in place. The audio sibling of /api/companion_turn: STT the mic
    audio, run the SAME brain (with TTS on), collapse to one JSON the rail
    renders + plays. Lets voice live in the rail instead of a full-screen
    jump (see .claude/rules — the companion is one location, two modalities).

    Request (multipart/form-data): audio, messages?, companion?, focus_app?
    Response (JSON): {transcript, reply_text, audio_urls[], cards, links,
                      pending, confirm, session}  — or {disabled:true}.
    """
    if not self._companion_enabled():
        return {"disabled": True}
    form = await request.form()
    audio_file = form.get("audio")
    if not audio_file:
        return {"error": "no audio"}
    try:
        messages = json.loads(form.get("messages", "[]"))
    except Exception:
        messages = []
    if not isinstance(messages, list):
        messages = []
    companion_id = form.get("companion") or None
    focus_app = (form.get("focus_app") or "").strip() or None
    page_context = (form.get("context") or "").strip() or None

    try:
        audio_bytes = await audio_file.read()
        user_text = await self.listen(audio=audio_bytes)
    except Exception as e:
        return {"error": f"Failed to transcribe: {e}"}
    if not user_text or not user_text.strip() or is_stt_artifact(user_text):
        # Whisper caption-hallucination on silence/noise → treat as no speech.
        return {"error": "Could not understand audio"}
    user_text = user_text.strip()

    reply_text = ""
    audio_urls: list[str] = []
    cards: list[dict] = []
    links: list[dict] = []
    pending: list[dict] = []
    confirm: list[dict] = []
    session = {"messages": messages, "companion": companion_id}

    # text_only=False → speak the reply; collapse audio events into audio_urls
    # the rail plays in sequence (mirrors /api/device_turn's collapse loop).
    async for ev in turn_events(
        self, user_text, messages, companion_id,
        echo_user_text=False, text_only=False, focus_app=focus_app,
        page_context=page_context, surface="rail-voice",
    ):
        t = ev.get("type")
        if t == "text":
            reply_text += ev.get("delta", "")
        elif t == "audio":
            if ev.get("url"):
                audio_urls.append(ev["url"])
        elif t == "card":
            cards.append({"intent": ev.get("intent", ""), "renderer": ev.get("renderer"),
                          "data": ev.get("data"), "title": ev.get("title")})
        elif t == "link":
            links.append({"intent": ev.get("intent", ""), "text": ev.get("text") or "Open",
                          "href": ev.get("href")})
        elif t == "pending_action":
            pending.append({"action_id": ev.get("action_id"), "verb": ev.get("verb", ""),
                            "args": ev.get("args") or {}, "description": ev.get("description") or ""})
        elif t == "confirm_required":
            confirm.append({"verb": ev.get("verb", ""), "args": ev.get("args") or {},
                            "description": ev.get("description") or ""})
        elif t == "companion_switch":
            session["companion"] = ev.get("companion")
        elif t == "error":
            return {"error": ev.get("error", "turn failed"), "transcript": user_text}
        elif t == "done":
            session["messages"] = ev.get("messages", messages)
            session["companion"] = ev.get("companion")
            reply_text = ev.get("full_text") or reply_text

    return {
        "transcript": user_text,
        "reply_text": reply_text.strip(),
        "audio_urls": audio_urls,
        "cards": cards,
        "links": links,
        "pending": pending,
        "confirm": confirm,
        "session": session,
    }


@web_route("GET", "/api/companions")
async def api_companions(self, request):
    """List available companions for UI display."""
    return [
        {"id": cid, "name": entry.get("name", cid)} for cid, entry in self._companions.items()
    ]
