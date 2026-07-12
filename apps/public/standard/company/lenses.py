"""Build ``multi_lens_analyze`` lens entries from org member dicts.

Standalone module (no BaseApp / kernel coupling) so unit tests can import
without spinning up the runtime. Used by ``_run_in_room`` to convert the
org's AI members into the lens shape ``multi_lens_analyze`` expects.
"""

from __future__ import annotations


_SYSTEM_PROMPT_TRUNCATE = 240
_FALLBACK_FOCUS = "this member's perspective"


def members_to_lenses(members: list[dict]) -> list[dict]:
    """Map org members → ``[{"name": ..., "focus": ...}, ...]``.

    Drops members with no resolvable name; for each kept member, the focus
    is the role + (truncated) system prompt joined with " — ". When neither
    is present, falls back to a generic phrase rather than emitting a lens
    with an empty focus (which ``multi_lens_analyze`` would reject).
    """
    out: list[dict] = []
    for m in members or []:
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or m.get("role") or m.get("id") or "").strip()
        if not name:
            continue
        bits: list[str] = []
        role = (m.get("role") or "").strip()
        if role:
            bits.append(role)
        sp = (m.get("system_prompt") or "").strip()
        if sp:
            bits.append(sp[:_SYSTEM_PROMPT_TRUNCATE])
        focus = " — ".join(bits) if bits else _FALLBACK_FOCUS
        out.append({"name": name, "focus": focus})
    return out
