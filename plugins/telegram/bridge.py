"""Telegram bridge — pure helpers for the two-way rooms bridge.

Zero kernel imports by design: everything here is unit-testable without a
daemon (tests/test_unit_telegram_bridge.py loads this file via importlib).
The plugin (`plugin.py`) owns the aiohttp session, the poll loop, and all
rooms `call_app` dispatch; this module owns parsing, rendering, chunking,
and offset-state persistence.

Security note: `parse_update` is the bridge's entire auth model — a
single-user chat_id allowlist. Every update whose chat id (and, for button
taps, sender id) doesn't string-equal the configured chat_id is dropped
before any processing. See the plan's security model + the room review
gate (.claude/rules/room-review-gate.md) for the action-layer floor.
"""

from __future__ import annotations

import hmac
import html
import json
import re
from pathlib import Path

from emptyos.frontmatter import strip_frontmatter

# Persona for the bridge's rooms agent (CLAUDE.md rule 12 — named constant).
# The agent runs with gate_mode="gate": the gate decides each [DO:] — a
# pending Apply/Reject card, or (stable verb with an inverse, while
# autopilot.auto_stable_default is on) an auto-run with Undo. The persona must
# teach token emission the same way the CLI system prompt does — "never
# describe an action without emitting the token".
#
# The seed is user-owned after first create (rooms `keep_existing_prompt`), so
# a reworded seed only reaches a live bot whose prompt still equals an earlier
# seed: every retired wording stays in SUPERSEDED_PERSONAS, verbatim.
TELEGRAM_BRIDGE_PERSONA_V1 = """You are the EmptyOS phone assistant, reached over Telegram.

Style:
- Replies are read on a phone. Keep them short and plain-text: no markdown headers, tables, or code fences. 1-4 sentences unless more is explicitly asked for.

Actions:
- For ANY action that changes state (adding a task, saving a capture, a journal entry), emit a [DO:app.method({"arg":"value"})] token inline in your reply. Only use verbs listed under SERVER ACTIONS.
- Every [DO:] token is shown to the user as an Apply/Reject card on their phone BEFORE anything runs. Nothing executes until they tap Apply.
- Never describe an action you would take without emitting the [DO:] token — without the token, nothing happens and no card appears.
- Never emit [BUTTON:...] tokens — this surface has no clickable reply buttons.

If asked for something you have no verb for, say so plainly instead of pretending."""

# v2 (2026-09-30, tg-life-surface T2): adds the routing rules the 58-turn phone
# corpus showed the model getting wrong — a chore phrased around a report or a
# month is a task, not a spending summary; a word question is a dictionary
# lookup, not a vault search; "半小时后" goes into `due` as written; a no-arg
# verb still gets `({})`.
TELEGRAM_BRIDGE_PERSONA_V2 = """You are the EmptyOS phone assistant, reached over Telegram. The user writes or speaks in 中文 or English; answer in the language they used.

Style:
- Replies are read on a phone. Keep them short and plain-text: no markdown headers, tables, or code fences. 1-3 sentences unless more is explicitly asked for.

Actions:
- Anything that records, schedules, looks something up in EmptyOS, or reports from it is done with a [DO:app.method({"arg":"value"})] token inline in your reply. Only use verbs listed under SERVER ACTIONS.
- A verb with no parameters is still written with empty braces: [DO:app.method({})].
- Every [DO:] token is shown to the user as an Apply/Reject card on their phone BEFORE anything runs. Say in one short sentence what the card will do; never say it is done.
- Never describe an action you would take without emitting the [DO:] token — without the token, nothing happens and no card appears.
- Never emit [BUTTON:...] tokens — this surface has no clickable reply buttons.

Picking the verb:
- A number after food, transport, or a shop ("午饭 45", "coffee 5.80") is an expense: amount = the number, description = the rest.
- Something to do later — 记得 / 需要 / todo / 整理 / 交 / 复核 / "need to" / "by Friday" — is a task to add, even when it names a report, invoices, or a month. Only summarise spending when asked what was spent.
- 提醒我 / "remind me" is a reminder: due = the day ("today", "tomorrow", or YYYY-MM-DD), time = the clock time ("9am", "20:00"). "半小时后" / "in 30 minutes" goes into due exactly as the user said it.
- Dates: 明天 = tomorrow, 后天 = the day after, 明早七点 = due tomorrow, time 07:00. A weekday or 下周三 becomes the YYYY-MM-DD it falls on.
- "X 是什么意思", "what does X mean", "define X", "查一下 X" for an English word is a dictionary lookup of that one word — not a vault search.
- "今天有什么安排" / "what's on today" is the calendar agenda for today plus today's tasks.
- A line about the user's day ("今天跑了5公里", "feeling tired") is a journal entry. A loose thought with no verb is a capture.
- Plain chat or a question about the world: just answer, no token.

If asked for something you have no verb for, say so plainly instead of pretending."""

# v3 (2026-10-03, tg-life-surface T9 + T11): since T3 some verbs run at once
# and the phone shows "⚡ done" with an Undo, so v2's "nothing runs before
# Apply" was false. Which ones depends on the gate and the deployment (the
# 2026-10-03 eval: only task adds auto-ran; expenses and reminders stayed
# cards), so v3 names both outcomes and promises neither. v3 also makes the bot the same manager as the
# computer session: it follows the manager profile that arrives as live context
# each turn, and records decisions as [LOG: ...] lines for the shared log.
TELEGRAM_BRIDGE_PERSONA = """You are the user's manager on their phone, reached over Telegram — the same manager as their computer session. Each turn's live context carries the manager profile (persona, voice, standing rules) and the newest lines of the shared manager log; follow the profile, and use the log to know what was already decided or done. The user writes or speaks in 中文 or English; answer in the language they used.

Style:
- Replies are read on a phone. Keep them short and plain-text: no markdown headers, tables, or code fences. 1-3 sentences unless more is explicitly asked for.

Actions:
- Anything that records, schedules, looks something up in EmptyOS, or reports from it is done with a [DO:app.method({"arg":"value"})] token inline in your reply. Only use verbs listed under SERVER ACTIONS.
- A verb with no parameters is still written with empty braces: [DO:app.method({})].
- What happens to a token is decided by the server, not by you: some run at once and the phone shows them as done (with an Undo button when they can be undone); others arrive as an Apply/Reject card. You cannot tell which, so say what you are doing in one short sentence ("Adding that as a task for tomorrow."), never claim it already happened, and never promise a card.
- Never describe an action you would take without emitting the [DO:] token — without the token, nothing happens.
- Never emit [BUTTON:...] tokens — this surface has no clickable reply buttons.

Manager log:
- When the user states a decision, a lasting preference, or a handoff for the computer session ("I'll skip the gym this week", "from now on no pushes on Sunday", "tell the computer to…"), add one line at the end of your reply: [LOG: <one short line in English, under 120 characters>]. It is removed from the reply the phone shows and goes into the shared log. Record only what the user said — never write that they approved or authorised something.
- Actions you emit are logged automatically (by name only) — do not add a [LOG:] for them. Never log chat, questions, or private details (money figures, health, other people's names).

Picking the verb:
- A number after food, transport, or a shop ("午饭 45", "coffee 5.80") is an expense: amount = the number, description = the rest.
- Something to do later — 记得 / 需要 / todo / 整理 / 交 / 复核 / "need to" / "by Friday" — is a task to add, even when it names a report, invoices, or a month. Only summarise spending when asked what was spent.
- 提醒我 / "remind me" is a reminder: due = the day ("today", "tomorrow", or YYYY-MM-DD), time = the clock time ("9am", "20:00"). "半小时后" / "in 30 minutes" goes into due exactly as the user said it.
- Dates: 明天 = tomorrow, 后天 = the day after, 明早七点 = due tomorrow, time 07:00. A weekday or 下周三 becomes the YYYY-MM-DD it falls on.
- "X 是什么意思", "what does X mean", "define X", "查一下 X" for an English word is a dictionary lookup of that one word — not a vault search.
- "今天有什么安排" / "what's on today" is the calendar agenda for today plus today's tasks.
- A line about the user's day ("今天跑了5公里", "feeling tired") is a journal entry. A loose thought with no verb is a capture.
- Plain chat or a question about the world: just answer, no token.

If asked for something you have no verb for, say so plainly instead of pretending."""

SUPERSEDED_PERSONAS = (TELEGRAM_BRIDGE_PERSONA_V1, TELEGRAM_BRIDGE_PERSONA_V2)

# Telegram hard message limit is 4096 chars; leave headroom for safety.
CHUNK_LIMIT = 3900

# Bridge slash-commands handled by the plugin itself, NOT forwarded to the
# rooms agent. Matched case-insensitively on the whole (stripped) message,
# tolerating Telegram's "/clear@botname" mention suffix.
CLEAR_COMMANDS = {"/clear", "/new", "/reset"}


def is_clear_command(text: str) -> bool:
    """True when the whole message is a bridge session-reset command."""
    t = (text or "").strip().lower()
    t = t.split("@", 1)[0]  # /clear@mybot → /clear
    return t in CLEAR_COMMANDS

# `[BUTTON:label|DO:app.method({...})]` click-to-execute wrappers are a web-UI
# affordance with no Telegram equivalent — strip any the model emits anyway.
_BUTTON_RE = re.compile(r"\[BUTTON:[^\|\]]*\|DO:.*?\)\s*\]", re.DOTALL)


# ── Update parsing (the auth line) ─────────────────────────────────────────


def parse_update(update: dict, allowed_chat_id: str) -> tuple[str | None, dict]:
    """Classify one getUpdates entry.

    Returns (kind, payload):
      ("message",     {"text", "chat_id", "message_id"})   — text from the owner
      ("unsupported", {"chat_id"})                          — owner sent non-text
      ("callback",    {"cq_id","chat_id","message_id","data"}) — owner tapped a button
      (None, {})                                            — anything else, DROPPED
    Drop cases include: wrong/unknown chat id, edited_message, channel posts,
    malformed updates. `allowed_chat_id` compare is string-vs-string.
    """
    if not isinstance(update, dict) or not allowed_chat_id:
        return None, {}

    msg = update.get("message")
    if isinstance(msg, dict):
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id != str(allowed_chat_id):
            return None, {}
        text = msg.get("text")
        if isinstance(text, str) and text.strip():
            return "message", {
                "text": text,
                "chat_id": chat_id,
                "message_id": msg.get("message_id"),
            }
        voice = msg.get("voice") or msg.get("audio")
        if isinstance(voice, dict) and voice.get("file_id"):
            return "voice", {
                "file_id": voice["file_id"],
                "duration": int(voice.get("duration") or 0),
                "chat_id": chat_id,
                "message_id": msg.get("message_id"),
            }
        return "unsupported", {"chat_id": chat_id}

    cq = update.get("callback_query")
    if isinstance(cq, dict):
        cq_msg = cq.get("message") or {}
        chat_id = str((cq_msg.get("chat") or {}).get("id", ""))
        from_id = str((cq.get("from") or {}).get("id", ""))
        # Both the chat the card lives in AND the tapper must be the owner.
        if chat_id != str(allowed_chat_id) or from_id != str(allowed_chat_id):
            return None, {}
        return "callback", {
            "cq_id": cq.get("id", ""),
            "chat_id": chat_id,
            "from_id": from_id,
            "message_id": cq_msg.get("message_id"),
            "data": cq.get("data") or "",
        }

    return None, {}


def next_offset(updates: list[dict], current: int) -> int:
    """Telegram getUpdates confirmation offset: max(update_id) + 1."""
    out = current
    for u in updates:
        uid = u.get("update_id")
        if isinstance(uid, int) and uid + 1 > out:
            out = uid + 1
    return out


# ── Brain Dump mode (/dump ↔ /chat toggle) ─────────────────────────────────
#
# A persistent per-chat mode: while ON, every inbound message (typed or
# voice-transcribed) is routed to the braindump pipeline — clean summary +
# Apply/Reject cards — instead of the chat agent. The plugin owns the
# routing + state; this module owns the pure command parse + the reply copy.

BRAINDUMP_ON_MSG = (
    "\U0001F9E0 Brain Dump mode on — send me your thoughts (type or voice) "
    "and I'll turn them into a summary + tasks/notes. /chat to exit."
)
BRAINDUMP_OFF_MSG = "\U0001F4AC Back to normal chat."
BRAINDUMP_DISABLED_MSG = (
    "Brain Dump isn't enabled — turn it on in Settings "
    "(braindump.feature.enabled) first."
)

# Telegram bot menu (autocomplete). Registered once per boot by the plugin.
BOT_COMMANDS = [
    {"command": "dump", "description": "Brain Dump — turn a ramble into tasks/notes"},
    {"command": "chat", "description": "Back to normal chat"},
]

# Leading /dump or /chat, case-insensitive, optional @botname, trailing text
# ignored ("/dump chase the report" still toggles). "/dumpx" is NOT a command.
_COMMAND_RE = re.compile(r"^/(dump|chat)(?:@\w+)?(?:\s|$)", re.IGNORECASE)


def parse_command(text: str) -> str | None:
    """Return "dump" / "chat" for a leading slash command, else None."""
    if not isinstance(text, str):
        return None
    m = _COMMAND_RE.match(text.strip())
    return m.group(1).lower() if m else None


# ── Callback data (inline-button payload) ──────────────────────────────────


def callback_data(verb: str, action_id: str) -> str:
    """"ap:act-…" / "rj:act-…" — stays far under Telegram's 64-byte cap."""
    return f"{verb}:{action_id}"


def parse_callback_data(data: str) -> tuple[str, str] | None:
    """Parse "ap:" / "rj:" / "un:" + action_id. None on anything else."""
    if not isinstance(data, str) or ":" not in data:
        return None
    verb, _, action_id = data.partition(":")
    if verb not in ("ap", "rj", "un") or not action_id:
        return None
    return verb, action_id


# ── Rendering (HTML parse mode; escape everything interpolated) ────────────


# ── Minimal disclosure ────────────────────────────────────────────────────
# A card leaves the machine and persists in Telegram's chat history, so it
# carries the least that still lets the owner decide Apply/Reject. The full
# payload stays local, on /rooms/.

# Values under these keys are NEVER disclosed — either structurally large
# (a note body, a diff side) or the highest-risk thing we could print (a
# shell command). You cannot meaningfully approve a command on a phone
# anyway; repo.exec is in ALWAYS_GATE_VERBS precisely so it gets read locally.
NEVER_DISCLOSE_KEYS = frozenset({
    "cmd", "command", "content", "body", "old", "new", "patch", "diff",
})
# Longer than this and we send a size instead of the value.
MAX_VALUE_CHARS = 120
# Hard ceiling on the whole args block.
MAX_ARGS_BLOCK_CHARS = 400


def _scan_secrets(text: str) -> str:
    """Pattern name if `text` looks like a secret or personal data, else "".

    Reuses the outbound scanner so Telegram inherits the same high-confidence
    secret patterns AND the machine's `.eos-personal` patterns. Fails CLOSED:
    if the scanner can't be imported, treat the value as sensitive rather than
    printing it — an unscannable value is exactly the one not to leak.
    """
    try:
        from emptyos.capabilities.outbound_scan import scan_outbound
    except Exception:
        return "unscannable"
    try:
        findings = scan_outbound(text)
    except Exception:
        return "unscannable"
    return findings[0].pattern_name if findings else ""


def redact_args(args: dict) -> str:
    """Render `args` for outbound disclosure. Keys stay visible, values don't.

    Keys are always shown: knowing WHICH fields a verb was given is decidable
    information and leaks nothing. Values are shown only when short, not under
    a never-disclose key, and not secret-shaped.
    """
    if not isinstance(args, dict) or not args:
        return ""
    out: list[str] = []
    for key, value in args.items():
        k = str(key)
        if k.lower() in NEVER_DISCLOSE_KEYS:
            size = len(value) if isinstance(value, str) else len(str(value))
            out.append(f"{k}: <hidden, {size} chars — review on /rooms/>")
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            out.append(f"{k}: {value}")
            continue
        if not isinstance(value, str):
            # dict/list — shape only, never contents.
            out.append(f"{k}: <{type(value).__name__}, {len(value)} item(s)>")
            continue
        if len(value) > MAX_VALUE_CHARS:
            out.append(f"{k}: <str, {len(value)} chars — review on /rooms/>")
            continue
        hit = _scan_secrets(value)
        out.append(f"{k}: <redacted: {hit}>" if hit else f"{k}: {value}")
    block = "\n".join(out)
    if len(block) > MAX_ARGS_BLOCK_CHARS:
        block = block[:MAX_ARGS_BLOCK_CHARS] + "\n… (truncated — review on /rooms/)"
    return block


def card_expired(action: dict, *, now_ts: str, ttl_s: float) -> bool:
    """Is this card too old to act on? `ttl_s <= 0` disables the check.

    Delegates the timestamp arithmetic to `emptyos.sdk.utils.is_past_ttl`, which
    fails open on an unparseable `ts` (refusing a real card is worse — the atomic
    claim re-checks status at apply time anyway; this only stops a
    long-scrolled-back tap from firing silently) and, importantly, normalises a
    NAIVE stored timestamp to UTC. The hand-rolled version compared a naive `ts`
    against an aware `now`, which raises TypeError and silently meant "never
    expires".
    """
    from emptyos.sdk.utils import is_past_ttl

    return is_past_ttl(str(action.get("ts", "")), ttl_s, now_ts)


def render_card(action: dict) -> tuple[str, dict]:
    """Pending action → (HTML text, reply_markup) for one Telegram message.

    HTML mode is used because it has no ambient metacharacters — with
    `html.escape` on every interpolated value, hostile args can't break out
    of the card markup. Payload disclosure is minimised via `redact_args`;
    the full args stay local.
    """
    verb = f"{action.get('app', '?')}.{action.get('method', '?')}"
    lines = [f"\U0001F4CB <b>{html.escape(verb)}</b>"]

    block = redact_args(action.get("args") or {})
    if block:
        lines.append(f"<pre>{html.escape(block)}</pre>")

    for change in action.get("proposed_changes") or []:
        path = change.get("path") or "?"
        n = len(change.get("diff_lines") or [])
        lines.append(html.escape(f"✏ {path} ({n} diff lines — review on /rooms/)"))

    cmd = action.get("proposed_command")
    if isinstance(cmd, dict) and cmd.get("cmd"):
        # Never the command itself. A shell command is the single most
        # dangerous thing to put in a third party's chat history, and it is
        # not approvable from a phone.
        lines.append(html.escape(
            f"$ <command hidden, {len(str(cmd.get('cmd')))} chars — review on /rooms/>"
        ))

    if action.get("error"):
        # Errors can embed paths and payload fragments.
        err = str(action["error"])
        hit = _scan_secrets(err)
        lines.append(html.escape(
            f"⚠ <error redacted: {hit} — review on /rooms/>" if hit
            else f"⚠ {err[:MAX_VALUE_CHARS]}"
        ))

    aid = action.get("id", "")
    # Always point at the local surface, even when nothing was hidden: the full
    # payload, diff and room context only exist there. A relative path, never a
    # URL — the host/port come from [network] config (CLAUDE.md rule 17) and a
    # pure helper has no business guessing them.
    lines.append(html.escape(f"🔒 full payload: /rooms/#{aid}"))
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Apply", "callback_data": callback_data("ap", aid)},
            {"text": "❌ Reject", "callback_data": callback_data("rj", aid)},
        ]]
    }
    return "\n".join(lines), markup


def _speakable_result(raw: str) -> str:
    """Unwrap a voice-shaped result for a text surface.

    Applied voice verbs return {say, card?, link?}; rooms stores it
    stringified (Python repr). Aura would SPEAK the `say` — on Telegram we
    print it instead of the raw dict. Falls back to the raw string when the
    result isn't dict-shaped.
    """
    raw = (raw or "").strip()
    if not raw.startswith("{"):
        return raw
    try:
        import ast

        d = ast.literal_eval(raw)
    except Exception:
        return raw
    if not isinstance(d, dict):
        return raw
    parts = []
    say = d.get("say")
    if isinstance(say, str) and say.strip():
        parts.append(say.strip())
    link = d.get("link")
    if isinstance(link, dict) and link.get("href"):
        parts.append(f"{link.get('text') or 'Open'}: {link['href']}")
    card = d.get("card")
    if isinstance(card, dict) and isinstance(card.get("data"), list):
        for item in card["data"][:6]:
            if isinstance(item, dict) and item.get("text"):
                parts.append(f"• {item['text']}")
    return "\n".join(parts) if parts else raw


def render_resolution(verb: str, result: dict, *, action_id: str = "") -> str:
    """Outcome of apply/reject → HTML replacing the card text (keyboard removed).

    `result` is what rooms returned: the action dict on success
    (status applied/rejected, result snippet) or {"error": ...} on failure /
    already-resolved (cards self-heal on tap).

    `action_id` is the label fallback. The atomic claim's refusal
    (`{"error": "already approving"}`) carries no app/method, which used to
    render as a bare "?.?" — and concurrent taps made that common as soon as
    the claim landed. The id is always known by the caller, so use it.
    """
    result = result if isinstance(result, dict) else {}
    action = result.get("action") if isinstance(result.get("action"), dict) else result
    if action.get("app") and action.get("method"):
        label = f"{action['app']}.{action['method']}"
    else:
        label = action_id or "action"

    err = result.get("error")
    if err:
        return f"⚠ <b>{html.escape(label)}</b>\n{html.escape(str(err)[:300])}"
    if verb == "rj" or action.get("status") == "rejected":
        return f"❌ <b>{html.escape(label)}</b> — rejected"
    snippet = _speakable_result(str(action.get("result") or ""))[:300]
    tail = f"\n{html.escape(snippet)}" if snippet else ""
    return f"✅ <b>{html.escape(label)}</b> — applied{tail}"


def render_auto_applied(action: dict) -> tuple[str, dict | None]:
    """An action that ran by itself (or tried to) → (HTML text, reply_markup).

    It carries an Undo button when rooms recorded a way back (`undo_id`);
    otherwise it is a plain confirmation. The verb's reply usually echoes its
    argument ("Added: <task>"), so it gets the limits `redact_args` puts on a
    card value: withheld over `MAX_VALUE_CHARS` or when the outbound scanner
    flags it. The label is the verb the gate judged (`decided_as`), the name a
    hold uses.
    """
    label = action.get("decided_as") or f"{action.get('app', '?')}.{action.get('method', '?')}"
    if action.get("status") == "failed":
        err = str(action.get("error") or "failed")
        hit = _scan_secrets(err)
        why = f"<error redacted: {hit} — review on /rooms/>" if hit else err[:MAX_VALUE_CHARS]
        return f"⚠ <b>{html.escape(label)}</b> — didn't run\n{html.escape(why)}", None
    snippet = _speakable_result(str(action.get("result") or ""))
    hit = _scan_secrets(snippet) if snippet else ""
    if hit or len(snippet) > MAX_VALUE_CHARS:
        snippet = f"<reply withheld{': ' + hit if hit else ''} — see /rooms/#{action.get('id', '')}>"
    tail = f"\n{html.escape(snippet)}" if snippet else ""
    text = f"⚡ <b>{html.escape(label)}</b> — done{tail}"
    if not action.get("undo_id"):
        return text, None
    return text, undo_markup(action.get("id", ""))


def undo_markup(action_id: str) -> dict:
    """The one-button keyboard that undoes `action_id`."""
    return {"inline_keyboard": [[
        {"text": "↩️ Undo", "callback_data": callback_data("un", action_id)},
    ]]}


def render_undo_result(result: dict, *, action_id: str = "") -> str:
    """Outcome of an Undo tap → HTML replacing the confirmation (keyboard removed)."""
    result = result if isinstance(result, dict) else {}
    action = result.get("action") if isinstance(result.get("action"), dict) else {}
    label = action.get("decided_as") or (
        f"{action['app']}.{action['method']}"
        if action.get("app") and action.get("method") else action_id or "action")
    if result.get("ok"):
        return f"↩️ <b>{html.escape(label)}</b> — undone"
    if result.get("already"):
        # Reversed already (another tap, or undo-last) — don't turn the
        # confirmation into a warning.
        return f"↩️ <b>{html.escape(label)}</b> — already undone"
    if result.get("in_progress"):
        # A double tap mid-undo: the first tap edits this message with the
        # real outcome, so claim nothing here.
        return f"↩️ <b>{html.escape(label)}</b> — undoing…"
    why = result.get("message") or result.get("error") or "couldn't undo"
    return f"⚠ <b>{html.escape(label)}</b>\n{html.escape(str(why)[:300])}"


def strip_button_tokens(text: str) -> str:
    return _BUTTON_RE.sub("", text or "").strip()


def chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Split on newline boundaries where possible; hard-split otherwise."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 1, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


# ── Periodic session auth (password/token challenge) ──────────────────────
#
# The chat_id allowlist proves *which account* is talking; the periodic
# password challenge proves the *person* still holds the secret — covering a
# stolen phone / compromised Telegram account. Flow (state-driven, pure here;
# the plugin owns I/O):
#   session expired → first inbound gets AUTH_PROMPT (never counted as a
#   failed attempt) → next message is the attempt → success unlocks for the
#   TTL and the password message is DELETED from the chat history; failure
#   counts toward a lockout (5 wrong → ~15 min lock). Button taps while
#   locked are refused and re-prompt.

AUTH_PROMPT = "\U0001F512 Session locked — reply with the bridge password to continue."
AUTH_MAX_FAILS = 5
AUTH_LOCKOUT_S = 900.0


def verify_password(attempt: str, secret: str) -> bool:
    """Constant-time compare; an empty secret never verifies (fail closed)."""
    secret = (secret or "").strip()
    if not secret:
        return False
    return hmac.compare_digest((attempt or "").strip(), secret)


def is_authed(state: dict, now: float) -> bool:
    try:
        return float(state.get("authed_until") or 0) > now
    except (TypeError, ValueError):
        return False


def is_locked_out(state: dict, now: float) -> bool:
    try:
        return float(state.get("lock_until") or 0) > now
    except (TypeError, ValueError):
        return False


def note_auth_success(state: dict, now: float, ttl_s: float) -> None:
    state["authed_until"] = now + ttl_s
    state["fails"] = 0
    state["awaiting_password"] = False
    state.pop("lock_until", None)


def note_auth_failure(
    state: dict, now: float, max_fails: int = AUTH_MAX_FAILS, lock_s: float = AUTH_LOCKOUT_S
) -> bool:
    """Record one wrong attempt. Returns True when this attempt trips the lockout."""
    fails = int(state.get("fails") or 0) + 1
    if fails >= max_fails:
        state["fails"] = 0
        state["lock_until"] = now + lock_s
        state["awaiting_password"] = False
        return True
    state["fails"] = fails
    return False


# ── Offset state (data/telegram/state.json) ────────────────────────────────


def load_state(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")


# ── Manager memory (tg-life-surface T9) ────────────────────────────────────
# The phone bot and the computer manager session are one manager. They share
# two vault notes: `profile.md` (persona, voice, standing rules) and `log.md`
# (one dated line per decision or addition). Both are read fresh on every
# phone turn and handed to rooms as live context, so an edit to either reaches
# the next turn with no reseed; the persona itself stays user-owned.
#
# Everything here crosses to the model provider each turn, so it is bounded.

# Bounds, all per turn. The profile is ~2.4k chars of body today, so 2500 keeps
# it whole while stopping it from growing the turn unnoticed. 10 lines / 1500
# chars is the digest size the plan set (tg-life-surface T9). A digest line is
# cut at 300 so one long computer-side entry (they run to ~600) cannot crowd
# the other nine out. A phone line is cut at 160: the persona asks for under
# 120, and the margin keeps a slightly long one whole.
MANAGER_PROFILE_MAX_CHARS = 2500
MANAGER_LOG_MAX_LINES = 10
MANAGER_LOG_MAX_CHARS = 1500
MANAGER_LOG_DIGEST_LINE_MAX = 300
MANAGER_LOG_LINE_MAX = 160

_LOG_ENTRY_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} · ")
# A [LOG: …] token sits at the end of a line (the persona puts it last), so it
# ends at the last `]` on that line — a wikilink inside it stays inside it.
_LOG_TOKEN_RE = re.compile(r"\[LOG:[ \t]*(.*?)\][ \t]*(?=\r?\n|$)", re.I | re.M)
_LOG_SEAM_RE = re.compile(r"\][ \t]*\[LOG:[ \t]*", re.I)
MAX_LOG_TOKENS_PER_TURN = 2


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def manager_profile_text(raw: str, max_chars: int = MANAGER_PROFILE_MAX_CHARS) -> str:
    """profile.md → the body the model reads: frontmatter and `>` editor notes
    dropped, capped at a line boundary so a long profile can't grow the turn."""
    body = strip_frontmatter(raw or "")
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith(">")]
    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if len(body) <= max_chars:
        return body
    cut = body.rfind("\n", 0, max_chars - 1)
    return body[: cut if cut > 0 else max_chars - 1].rstrip() + "\n…"


def manager_log_digest(raw: str, max_lines: int = MANAGER_LOG_MAX_LINES,
                       max_chars: int = MANAGER_LOG_MAX_CHARS) -> str:
    """The newest log entries, oldest first, within both bounds. Only dated
    entry lines count — the note's header prose never reaches the model."""
    entries = [ln.strip() for ln in (raw or "").splitlines() if _LOG_ENTRY_RE.match(ln.strip())]
    tail = ([_cap(ln, MANAGER_LOG_DIGEST_LINE_MAX) for ln in entries[-max_lines:]]
            if max_lines > 0 else [])
    while tail and len("\n".join(tail)) > max_chars:
        tail.pop(0)
    return "\n".join(tail)


def manager_context(profile_raw: str, log_raw: str) -> str:
    """Live context for one phone turn; empty when neither note has content."""
    parts = []
    profile = manager_profile_text(profile_raw)
    if profile:
        parts.append("Manager profile (shared with the computer session):\n" + profile)
    digest = manager_log_digest(log_raw)
    if digest:
        parts.append("Manager log, newest last:\n" + digest)
    return "\n\n".join(parts)


def extract_log_tokens(text: str) -> tuple[str, list[str]]:
    """Strip every [LOG: …] token from a reply → (clean reply, logged lines).

    All tokens leave the phone reply (rooms history still holds the raw
    reply); at most MAX_LOG_TOKENS_PER_TURN are kept, so a looping model can't
    flood the log.
    """
    if not isinstance(text, str) or "[log:" not in text.lower():
        return text, []
    # Two tokens on one line match as one (the match ends at the line's last
    # `]`), so split a capture at any `] [LOG:` seam it still contains.
    found = [part.strip() for m in _LOG_TOKEN_RE.findall(text)
             for part in _LOG_SEAM_RE.split(m) if part.strip()]
    clean = _LOG_TOKEN_RE.sub("", text)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, found[:MAX_LOG_TOKENS_PER_TURN]


def manager_log_line(when, text: str) -> str | None:
    """One `- YYYY-MM-DD HH:MM · phone · <text>` line, or None when there is
    nothing safe to write (empty, or the outbound scanner flags it — the log is
    read back to the model on every later turn)."""
    one = " ".join(str(text or "").split())
    if not one or _scan_secrets(one):
        return None
    return f"- {when:%Y-%m-%d %H:%M} · phone · {_cap(one, MANAGER_LOG_LINE_MAX)}"


def action_log_text(action: dict, *, undone: bool = False) -> str:
    """What the log says about a verb that ran (or was undone) from the phone.

    The verb and the action id only — never the verb's reply. That reply echoes
    whatever the argument was (a task naming a person, a fee, a diagnosis) and
    third-party text (an imported invite title), and the log goes back to the
    model on every later turn; the id lets the computer side look it up.
    """
    label = action.get("decided_as") or f"{action.get('app', '?')}.{action.get('method', '?')}"
    ref = f" ({action['id']})" if action.get("id") else ""
    return f"{'undid' if undone else 'ran'} {label}{ref}"
