# Pure intent helpers — token parsing, schema validation, prompt rendering,
# scoping. No app/kernel access; everything operates on plain data so it's
# trivially testable and reusable from any app surface (voice-assistant chat
# pipeline, rooms multi-participant dispatcher, plan-then-execute paths).
#
# Lifted from apps/voice-assistant/intents.py. The voice-specific intent
# prompt header is now an optional kwarg on render_intent_block — pass your
# own framing or rely on the generic default. Voice-assistant pre-binds its
# header via a thin shim in apps/voice-assistant/intents.py.

import json
import re

# Inline tool-call token. Keep the regex non-greedy so multiple intents in one
# reply parse independently. Used by the streaming dispatcher (single-intent,
# immediate-fire). Plan mode uses find_intents() instead, which scans balanced
# braces and so handles nested args.
INTENT_RE = re.compile(r"\[INTENT:([\w\.\-]+)\((\{.*?\})\)\]")
MAX_INTENTS_IN_PROMPT = 12

# Generic fallback header used when callers don't supply their own framing.
# Voice-assistant overrides this with a stricter voice-specific block.
DEFAULT_INTENT_PROMPT_HEADER = (
    'Tools — emit `[INTENT:app.verb({"arg":"value"})]` inline in your reply to invoke one. '
    "Use a tool only when the user clearly asks for that action. Never invent tools.\n"
    "Available:"
)


def find_intents(text: str) -> list[tuple[str, str, int, int]]:
    """Extract every `[INTENT:verb({...})]` token via balanced-brace scan.

    Replaces the V1 regex (`INTENT_RE`) which stopped at the first `}` and
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
        verb = text[b + 8 : paren].strip()
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
        if text[end_args : end_args + 2] != ")]":
            i = end_args
            continue
        args_raw = text[paren + 1 : end_args]
        out.append((verb, args_raw, b, end_args + 2))
        i = end_args + 2
    return out


def validate_args(schema: dict, args: dict) -> tuple[bool, str]:
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


def render_intent_block(scoped: list[dict], header: str | None = None) -> str:
    """Render the intent appendix for the system prompt. Empty when no scope.

    `header` lets each surface (voice, rooms, plan-mode) frame the available-
    tools block its own way. Falls back to DEFAULT_INTENT_PROMPT_HEADER.
    """
    if not scoped:
        return ""
    lines = ["", header or DEFAULT_INTENT_PROMPT_HEADER]
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


def scope_intents(
    intents: dict[str, dict],
    companions: dict[str, dict],
    recent_apps,
    companion_id: str | None,
) -> list[dict]:
    """Return the intents to surface to the LLM this turn.

    Inclusion rules (canonical, see .claude/rules/voice-intents.md):
    - `always: true` regardless of context
    - belongs to the active companion's source app
    - belongs to one of the last N apps whose intent fired

    Capped at MAX_INTENTS_IN_PROMPT — order: always, then companion-app,
    then recent. Truncated intents simply don't appear in the prompt.

    `companions` maps companion_id → entry with `_app_id`. Pass {} if you
    don't have a companion concept (e.g. rooms with N participants resolves
    `companion_id` per @-mention to the participant's source app).
    """
    if not intents:
        return []

    companion_app = None
    if companion_id and companion_id in companions:
        companion_app = companions[companion_id].get("_app_id")

    always, companion_intents, recent_intents = [], [], []
    for entry in intents.values():
        app_id = entry.get("_app_id")
        if entry.get("always"):
            always.append(entry)
        elif companion_app and app_id == companion_app:
            companion_intents.append(entry)
        elif app_id in recent_apps:
            recent_intents.append(entry)

    ordered = always + companion_intents + recent_intents
    return ordered[:MAX_INTENTS_IN_PROMPT]


def intent_embedding_text(entry: dict) -> str:
    """Text fed to the embedder for an intent. We embed verb + description +
    example because the user utterance ("set a reminder for 5pm") rarely
    matches the verb itself ("reminders.set") — the description/example is
    where the semantic signal lives.
    """
    verb = entry.get("verb", "")
    desc = (entry.get("description") or "").strip()
    example = (entry.get("example") or "").strip()
    parts = [verb]
    if desc:
        parts.append(desc)
    if example and example != desc:
        parts.append(example)
    return " — ".join(parts)


def scope_intents_by_relevance(
    intents: dict[str, dict],
    companions: dict[str, dict],
    companion_id: str | None,
    ranked_verbs: list[str],
) -> list[dict]:
    """Embedding-aware variant of `scope_intents`. Inclusion order:

    1. `always: true` (regardless of relevance)
    2. companion-app intents (in their own relevance order if listed in
       `ranked_verbs`, otherwise in registration order)
    3. fill remaining slots from `ranked_verbs` (best-cosine first), skipping
       any already added

    `ranked_verbs` is the verb list in descending similarity to the current
    user utterance — caller does the embedding pass since it has the query.

    Cap is the same MAX_INTENTS_IN_PROMPT so the LLM prompt stays bounded.
    """
    if not intents:
        return []

    companion_app = None
    if companion_id and companion_id in companions:
        companion_app = companions[companion_id].get("_app_id")

    seen: set[str] = set()
    out: list[dict] = []

    # 1. always-intents
    for entry in intents.values():
        if entry.get("always"):
            verb = entry.get("verb")
            if verb and verb not in seen:
                out.append(entry)
                seen.add(verb)

    # 2. companion-app intents (relevance-ordered if available)
    if companion_app:
        comp_entries = [e for e in intents.values()
                        if e.get("_app_id") == companion_app and e.get("verb") not in seen]
        if ranked_verbs:
            order = {v: i for i, v in enumerate(ranked_verbs)}
            comp_entries.sort(key=lambda e: order.get(e.get("verb"), 1_000_000))
        for entry in comp_entries:
            if len(out) >= MAX_INTENTS_IN_PROMPT:
                break
            verb = entry.get("verb")
            if verb and verb not in seen:
                out.append(entry)
                seen.add(verb)

    # 3. fill from relevance ranking
    for verb in ranked_verbs:
        if len(out) >= MAX_INTENTS_IN_PROMPT:
            break
        if verb in seen:
            continue
        entry = intents.get(verb)
        if entry is not None:
            out.append(entry)
            seen.add(verb)

    return out[:MAX_INTENTS_IN_PROMPT]


def build_plan_dict(reply_text: str, scoped: list[dict]) -> dict:
    """Parse intent tokens out of an LLM reply, validate against schemas,
    return the canonical plan shape consumed by `execute_plan` and the UI."""
    scoped_verbs = {e.get("verb"): e for e in scoped}
    found = find_intents(reply_text)
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
                ok, msg = validate_args(entry.get("args") or {}, args)
                if not ok:
                    err = msg
        calls.append(
            {
                "verb": verb,
                "args": args or {},
                "raw_args": args_raw,
                "app": entry.get("_app_id") if entry else None,
                "method": entry.get("method") if entry else None,
                "description": (entry.get("description") or entry.get("example"))
                if entry
                else None,
                "card": entry.get("card") if entry else None,
                "error": err,
            }
        )
    return {"raw_reply": reply_text, "say": cleaned, "calls": calls}
