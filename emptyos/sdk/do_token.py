"""Shared [DO:] token grammar — the parse step of the review-gate paradigm.

Personas + CLI participants emit `[DO:app.method({"arg":"value"})]` tokens
inline in their replies. This module owns the regex + action-dict shape so
every consumer (rooms gate, company scenarios, future agents) can't drift
on the token format.

Persistence, side effects (sandbox diffs, undo prep), and apply-time
dispatch stay in the owning app. See `.claude/rules/proposed-action.md`
and `.claude/rules/room-review-gate.md`.

Today's consumers: `apps/rooms/_gate_server_actions`,
`apps/company/scenarios/base.gate_responses`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from emptyos.sdk.utils import parse_llm_json

DO_RE = re.compile(r"\[DO:([\w-]+)\.(\w+)\((\{.*?\})\)\]", re.DOTALL)


def new_action_id() -> str:
    return f"act-{uuid.uuid4().hex[:10]}"


def extract_do_tokens(
    text: str,
    *,
    source_actor: dict,
    context: dict | None = None,
) -> tuple[str, list[dict]]:
    """Parse `[DO:app.method({...})]` tokens from text.

    Returns ``(cleaned_text, [action_dict, ...])``. Each action carries:

    - ``id`` — unique slug (``act-<10-hex>``)
    - ``ts`` — ISO-8601 UTC timestamp
    - ``source_actor`` — caller-supplied {"type": "cli"|"agent"|"worker", ...}
    - ``app`` — the verb's namespace (`task`, `kb`, `publish`, …)
    - ``method`` — the verb (`add`, `api_doc_create`, …)
    - ``args`` — parsed via ``parse_llm_json``; ``{}`` on parse failure
    - ``status`` — always ``"pending"`` from this function

    ``context`` (optional) is merged into each action dict — typically
    ``{"room_id": "..."}`` for rooms or ``{"run_id": "..."}`` for company
    scenarios. The caller's owning surface decides which fields anchor the
    pending action back to its source.

    No allowlist enforcement here — the user is the gate, and apply-time
    errors surface as failed actions in the UI.
    """
    pending: list[dict] = []
    for match in DO_RE.finditer(text or ""):
        app_id, method, args_str = match.group(1), match.group(2), match.group(3)
        try:
            args = parse_llm_json(args_str, fallback={})
        except Exception:
            args = {}
        action = {
            "id": new_action_id(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "source_actor": source_actor,
            "app": app_id,
            "method": method,
            "args": args,
            "status": "pending",
        }
        if context:
            action.update(context)
        pending.append(action)
    cleaned = DO_RE.sub("", text or "").strip()
    return cleaned, pending
