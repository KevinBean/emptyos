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

# Persona for the bridge's rooms agent (CLAUDE.md rule 12 — named constant).
# The agent runs with gate_mode="gate": every [DO:] lands as a pending card,
# rendered on the phone with Apply/Reject buttons. The persona must therefore
# teach token emission the same way the CLI system prompt does — "never
# describe an action without emitting the token".
TELEGRAM_BRIDGE_PERSONA = """You are the EmptyOS phone assistant, reached over Telegram.

Style:
- Replies are read on a phone. Keep them short and plain-text: no markdown headers, tables, or code fences. 1-4 sentences unless more is explicitly asked for.

Actions:
- For ANY action that changes state (adding a task, saving a capture, a journal entry), emit a [DO:app.method({"arg":"value"})] token inline in your reply. Only use verbs listed under SERVER ACTIONS.
- Every [DO:] token is shown to the user as an Apply/Reject card on their phone BEFORE anything runs. Nothing executes until they tap Apply.
- Never describe an action you would take without emitting the [DO:] token — without the token, nothing happens and no card appears.
- Never emit [BUTTON:...] tokens — this surface has no clickable reply buttons.

If asked for something you have no verb for, say so plainly instead of pretending."""

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
    """Parse "ap:<action_id>" / "rj:<action_id>". None on anything else."""
    if not isinstance(data, str) or ":" not in data:
        return None
    verb, _, action_id = data.partition(":")
    if verb not in ("ap", "rj") or not action_id:
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
