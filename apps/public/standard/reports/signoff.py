"""Reports — merge approver-role list with recorded signoff facts.

Pure helper shared by render_html.py and render_docx.py's signoff-block
renderers (reports-no-signoff-tracking: the signoff block was a static
printed table with blank signature/date lines — no approval event was ever
captured).

`approvers` frontmatter stays a plain list of role strings, unchanged.
Storing the actual signoff facts (who/when/signature) as a list of DICTS
on `approvers` itself was considered and rejected: `_serialize_fm`'s list
branch (`emptyos/runtime/vault_index.py`) does `str(item)` unconditionally
for every list entry, with no dict-in-list flow-style handling — a dict
item would serialize as Python's dict repr and read back as an opaque
string on the next load, silently breaking every consumer expecting a
mapping. The actual signoff facts live in a separate `signoffs_json`
scalar string field instead, the same JSON-in-frontmatter convention
`attribute_schema` already uses for exactly this reason.
"""

from __future__ import annotations

import json


def merged_approver_rows(approvers, signoffs_json: str) -> list[dict]:
    """[{role, name, date, signature}] — one row per configured approver
    role, filled in from a matching recorded signoff if one exists."""
    if isinstance(approvers, str):
        approvers = [a.strip() for a in approvers.split(",") if a.strip()]
    if not isinstance(approvers, list):
        approvers = []
    try:
        signoffs = json.loads(signoffs_json or "[]")
    except (TypeError, ValueError):
        signoffs = []
    if not isinstance(signoffs, list):
        signoffs = []
    by_role = {
        str(s["role"]): s for s in signoffs if isinstance(s, dict) and s.get("role")
    }

    rows = []
    for a in approvers:
        role = a.get("role", "") if isinstance(a, dict) else str(a)
        signed = by_role.get(role) or {}
        rows.append({
            "role": role,
            "name": signed.get("name", ""),
            "date": signed.get("date", ""),
            "signature": signed.get("signature", ""),
        })
    return rows
