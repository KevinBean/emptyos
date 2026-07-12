"""Compose — grounded professional / networking message drafts.

The shared draft-generator behind three consumers: the **writing-editor** app
(general + the agent-facing ``eos compose`` CLI), the **jobs** app (job-search
outreach, auto-grounded in a job note) and the **people** app (relationship
messages, auto-grounded in a person note).

Distinct from ``writing-editor``'s *revise/lint* verbs — those **transform text
the user already wrote**; compose **generates** a first draft from a *message
kind* (a persona-driven networking play) + a *channel* + grounding *context*.
The two compose: a generated draft can be handed straight to ``/api/revise``.

Drafting is reversible / internal, so per the north star it runs freely — there
is **no send and no consent gate in this module**. v1 is draft + copy-to-
clipboard; the caller decides what to do with the text.

Pure + think-injected: ``propose_message(..., think_fn=...)`` takes an async
closure ``(system, user) -> str`` so it unit-tests without a daemon (mirrors
``html_element_edit.py``). ``BaseApp.compose_message`` is the thin wrapper that
passes ``self.think``. The message-kind playbook + the channel/tone vocab +
the prompt builders are all module-level pure data/functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from emptyos.sdk.utils import parse_llm_json


# ── Channels — shape the register + length + whether there's a subject line ──

CHANNELS: dict[str, dict] = {
    "email": {
        "label": "Email", "subject": True,
        "length": "120–200 words",
        "register": "professional and complete, with a greeting and a sign-off",
    },
    "linkedin-dm": {
        "label": "LinkedIn message", "subject": False,
        "length": "40–80 words",
        "register": "warm and concise; no formal sign-off, no subject line",
    },
    "linkedin-inmail": {
        "label": "LinkedIn InMail", "subject": True,
        "length": "80–130 words",
        "register": "professional but personable; a short subject that earns the open",
    },
    "message": {
        "label": "Short message / SMS", "subject": False,
        "length": "25–60 words",
        "register": "relaxed and conversational, like a text to someone you know",
    },
}

# ── Tone — a small, compose-specific vocabulary ──

TONES: dict[str, str] = {
    "warm":   "friendly, personable, genuine — sound like a real person who means it",
    "direct": "lead with the point; minimal preamble; respect their time",
    "formal": "polished and professional; fuller courtesy; no slang",
    "casual": "relaxed and conversational; light, not stiff",
}


# ── The playbook — each kind is a persona-driven networking play ──

@dataclass(frozen=True)
class MessageKind:
    id: str
    label: str
    group: str            # "job" | "relationship"
    intent: str           # one-line shown in the picker
    guidance: str         # the play, injected into the system prompt
    default_channel: str = "email"


_KINDS: tuple[MessageKind, ...] = (
    # ── Job-search plays ──
    MessageKind(
        "cold-outreach", "Cold outreach", "job",
        "Reach a hiring manager or engineer at a target company you don't know yet",
        "A cold first contact with someone you have no relationship with. Open with a "
        "specific, genuine reason you're reaching out to THEM or their team — not a generic "
        "compliment. Draw one concrete line from your own background to their work. Make a "
        "SOFT ask only (a brief chat, a pointer, their read on the team) — never ask for a "
        "job or a referral in a first cold message. Keep it skimmable; they owe you nothing.",
        "linkedin-dm",
    ),
    MessageKind(
        "warm-intro-request", "Ask for an intro", "job",
        "Ask a mutual contact to introduce you to someone",
        "Ask a person you know to introduce you to someone they know. Make it FORWARDABLE: "
        "include a tight 2–3 line blurb they can paste straight on. Be precise about who and "
        "why. Make saying no effortless ('no worries at all if it's not a fit'). Acknowledge "
        "you're spending their social capital and that you appreciate it.",
        "email",
    ),
    MessageKind(
        "recruiter-reply", "Reply to a recruiter", "job",
        "Respond to an inbound recruiter message",
        "Respond to a recruiter who reached out. Be warm and signal genuine interest (or a "
        "polite, door-open redirection if it's off-target). Ask the 2–3 questions that "
        "actually matter before investing time: role scope/seniority, comp band, location/"
        "work model, timeline. Keep your options open; don't oversell or undersell.",
        "email",
    ),
    MessageKind(
        "follow-up-nudge", "Follow-up nudge", "job",
        "Gently nudge after no reply to an application or message",
        "A light nudge after silence. Reference the prior touch and roughly when it was. Add "
        "ONE new thing — a fresh reason you're a fit, a relevant update, or a small piece of "
        "value — so it isn't just 'checking in'. Keep it short and warm. No guilt, no "
        "passive-aggression; assume good faith and busyness.",
        "email",
    ),
    MessageKind(
        "thank-you-interview", "Post-interview thank-you", "job",
        "Thank an interviewer and reinforce fit",
        "A thank-you sent within a day of an interview. Reference ONE specific thing actually "
        "discussed (a problem, a project, a moment of rapport) so it's clearly not a template. "
        "Briefly reaffirm why the role fits. Warm, genuine, short. No new long pitch.",
        "email",
    ),
    MessageKind(
        "referral-request", "Ask for a referral", "job",
        "Ask a contact to refer you for a specific role",
        "Ask someone who knows your work to refer you internally for a specific role. This is "
        "a real ask — acknowledge it. Make it easy: name the role, and hand them a tailored "
        "3-line 'why me' they can lift into the referral form. Give them an easy out if the "
        "timing or fit is wrong for them.",
        "email",
    ),
    MessageKind(
        "offer-negotiation", "Negotiate an offer", "job",
        "Respond to an offer with a counter",
        "Respond to a job offer you want to improve. Lead with genuine enthusiasm for the role "
        "and team — make clear you want to make it work. Anchor the ask on market value and the "
        "scope you'll own, never on personal need. Be specific (a number or a concrete ask) and "
        "collaborative, not adversarial. Keep the relationship warm regardless of outcome.",
        "email",
    ),
    MessageKind(
        "withdraw-gracefully", "Withdraw gracefully", "job",
        "Bow out of a process while keeping the door open",
        "Withdraw from a hiring process cleanly. Be brief and appreciative of their time. Give "
        "a light, honest-enough reason without over-explaining or apologising excessively. "
        "Leave the door open for the future. End on warmth.",
        "email",
    ),
    # ── Relationship plays ──
    MessageKind(
        "reconnect", "Reconnect", "relationship",
        "Reach out to someone after a long silence",
        "Reconnect with someone you've lost touch with. Acknowledge the gap LIGHTLY — one line, "
        "no grovelling apology. Reference your shared history specifically (a project, a place, "
        "a moment) so it lands personal. GIVE before you ask: offer something relevant to them, "
        "or simply show genuine interest in how they are, before any ask. Propose a low-friction "
        "next step (a quick call, a coffee) and make it easy to say not now.",
        "email",
    ),
    MessageKind(
        "congratulations", "Congratulations", "relationship",
        "Congratulate someone on a new role, promotion, or milestone",
        "Congratulate someone on good news. Be specific about what they achieved and why it's "
        "earned. Genuine warmth, no faint praise. Crucially: NO ask in this message — it's a "
        "gift, not a setup. Short.",
        "linkedin-dm",
    ),
    MessageKind(
        "coffee-invite", "Invite for coffee / a call", "relationship",
        "Ask someone for a coffee or a short call",
        "Invite someone for a coffee or a short call. Say specifically why you'd value their "
        "time (their perspective on X, catching up on Y). Propose a concrete, low-commitment "
        "option (a 20-min call, coffee near them) and offer to work around them. Make declining "
        "graceful.",
        "message",
    ),
    MessageKind(
        "thank-you-favor", "Thank you for a favour", "relationship",
        "Thank someone who helped you",
        "Thank someone who did you a favour or gave real help. Be specific about what they did "
        "and the concrete difference it made — show it mattered and that you noticed the effort. "
        "Do NOT bundle a new ask into a thank-you; let it stand alone.",
        "message",
    ),
    MessageKind(
        "intro-two-people", "Introduce two people", "relationship",
        "Introduce two of your contacts to each other",
        "Introduce two people you know to each other. Give each a crisp 2-line bio and a clear "
        "reason the connection is worth their time. Be explicit that there's no obligation. "
        "Then hand off and get out of the way ('I'll let you two take it from here').",
        "email",
    ),
    MessageKind(
        "keep-warm", "Keep in touch", "relationship",
        "A light periodic check-in to keep a relationship alive",
        "A low-key check-in to keep a relationship warm. Share something genuinely relevant to "
        "THEM — an article, a thought, a memory, a small update — with no ask attached. Short, "
        "human, no agenda. The point is presence, not transaction.",
        "message",
    ),
)

MESSAGE_KINDS: dict[str, MessageKind] = {k.id: k for k in _KINDS}


# ── The networking know-how, appended to every system prompt ──

NETWORKING_PRINCIPLES = """Networking principles that govern every message:
- Reference specific, real shared context. Never generic flattery.
- Don't ask for a job or a big favour in a cold or first message — earn it. Calibrate the ask to the strength of the relationship.
- Give before you ask. Reciprocity beats extraction.
- Make the recipient's reply easy: one clear, low-friction next step, and an effortless way to say no.
- Match length to the channel and respect their time.
- Sound like a real, specific person — never a template. No corporate-speak (synergy, circle back, leverage, touch base), no AI tells (delve, tapestry, "I hope this email finds you well")."""

COMPOSE_SYSTEM_HEADER = """You draft professional and networking messages that sound like a real, thoughtful person — never a template.

You write AS the user, in the first person. Use ONLY facts present in the context the user gives you. Never invent a shared history, a mutual contact, a job detail, or anything not provided — if you don't have a fact, write around it rather than fabricating it. Leave a [bracketed placeholder] only where the user clearly must fill a specific in (a date, a number)."""


def _voice_block(voice_rules: list[str] | None) -> str:
    """Optional 'Your voice' block from the user's own behavior-pattern records.

    Layers the user's personal voice on top of the generic NETWORKING_PRINCIPLES
    — it never replaces them. Empty / None ⇒ "" ⇒ byte-identical baseline prompt.
    Kept pure (no vault, no self): the calling app fetches the rules from
    ``BaseApp.behavior_patterns("self", applies_to="comms")`` and passes them in.
    """
    rules = [str(r).strip() for r in (voice_rules or []) if str(r).strip()]
    if not rules:
        return ""
    body = "\n".join(f"- {r}" for r in rules[:8])
    return (
        "\n\nYour voice (derived from the user's own patterns — apply on top of the "
        "principles above, never against them):\n" + body
    )


def build_system(kind: MessageKind, channel: str, tone: str,
                 voice_rules: list[str] | None = None) -> str:
    ch = CHANNELS.get(channel, CHANNELS["email"])
    return (
        f"{COMPOSE_SYSTEM_HEADER}\n\n"
        f"Message type — {kind.label}: {kind.guidance}\n\n"
        f"Channel — {ch['label']}: {ch['register']}. Target length: {ch['length']}.\n"
        f"Tone — {tone}: {TONES.get(tone, TONES['warm'])}.\n\n"
        f"{NETWORKING_PRINCIPLES}"
        f"{_voice_block(voice_rules)}\n\n"
        "Output STRICT JSON. No markdown, no preface:\n"
        "{\n"
        '  "subject": "<subject line, or empty string if this channel has no subject>",\n'
        '  "body": "<the full message body, ready to copy-paste>",\n'
        '  "why": "<one short sentence on why this approach lands>",\n'
        '  "variant": "<a shorter or alternative version, or empty string>"\n'
        "}\n\n"
        "Rules:\n"
        f"- {'Write a subject line.' if ch['subject'] else 'This channel has NO subject — set subject to an empty string.'}\n"
        "- Do NOT invent facts. Write around missing information.\n"
        "- Do NOT use corporate-speak or AI-essay tells.\n"
        "- Match the message to the user's language (the language of the context / instructions).\n"
        "- Output ONLY the JSON object."
    )


def _context_block(context: dict) -> str:
    lines = [f"- {k}: {v}" for k, v in (context or {}).items() if str(v or "").strip()]
    return "\n".join(lines) if lines else "(no structured context supplied)"


def build_user(context: dict, freeform: str = "", variants: bool = False) -> str:
    parts = ["Context for this message:", _context_block(context)]
    ff = (freeform or "").strip()
    if ff:
        parts.append(f"\nWhat the user specifically wants to say / additional instructions:\n{ff}")
    if variants:
        parts.append("\nAlso provide a shorter alternative in the `variant` field.")
    else:
        parts.append("\nLeave the `variant` field as an empty string.")
    return "\n".join(parts)


def kinds_for(group: str = "") -> list[dict]:
    """Picker metadata for a group ('job' | 'relationship'), or all if empty."""
    out = []
    for k in _KINDS:
        if group and k.group != group:
            continue
        out.append({
            "id": k.id, "label": k.label, "group": k.group,
            "intent": k.intent, "default_channel": k.default_channel,
        })
    return out


def channels_meta() -> list[dict]:
    return [{"id": cid, "label": c["label"], "subject": c["subject"]} for cid, c in CHANNELS.items()]


def tones_meta() -> list[dict]:
    return [{"id": tid, "desc": desc} for tid, desc in TONES.items()]


async def propose_message(
    kind: str,
    channel: str,
    context: dict,
    *,
    think_fn,
    tone: str = "warm",
    freeform: str = "",
    variants: bool = False,
    voice_rules: list[str] | None = None,
) -> dict:
    """Generate one draft. ``think_fn`` is an async ``(system, user) -> str``.

    ``voice_rules`` (optional) are the user's own voice directives, surfaced from
    their behavior-pattern records by the calling app — layered on top of the
    generic networking principles, never replacing them. Returns
    ``{ok, kind, channel, tone, subject, body, why, variant}`` on success or
    ``{ok: False, error}``. Never raises on a bad model reply — it reports.
    """
    mk = MESSAGE_KINDS.get(kind)
    if mk is None:
        return {"ok": False, "error": f"unknown message kind: {kind}"}
    if channel not in CHANNELS:
        channel = mk.default_channel
    if tone not in TONES:
        tone = "warm"

    system = build_system(mk, channel, tone, voice_rules=voice_rules)
    user = build_user(context, freeform=freeform, variants=variants)

    try:
        raw = await think_fn(system, user)
    except Exception as e:  # noqa: BLE001 — surface, don't crash the caller
        return {"ok": False, "error": f"think failed: {e}"}

    # fallback=False → a non-dict sentinel so a bad reply lands as ok:False
    # below (parse_llm_json raises when fallback is None).
    parsed = parse_llm_json(raw, fallback=False)
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "model returned non-object JSON", "raw": str(raw)[:400]}

    has_subject = CHANNELS[channel]["subject"]
    return {
        "ok": True,
        "kind": kind,
        "channel": channel,
        "tone": tone,
        "subject": (str(parsed.get("subject") or "").strip() if has_subject else ""),
        "body": str(parsed.get("body") or "").strip(),
        "why": str(parsed.get("why") or "").strip(),
        "variant": (str(parsed.get("variant") or "").strip() if variants else ""),
    }
