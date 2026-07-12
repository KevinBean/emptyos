"""company — boards view-layer contract — flat member rows + cross-app set_field.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The boards-as-view-layer integration: list_all returns flat member rows, set_field gates writes through SETTABLE_FIELDS and routes them to the right note (member or org), re-mirroring AI personas when a persona-shaping field changes..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.list_members + self.list_orgs to resolve a row id; self._replace_section + self._csv_normalize (spine); self._push_member_to_rooms (members).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .shared import _vault_rel_org, _vault_rel_member
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   list_all   = _boards.list_all
#   set_field  = _boards.set_field
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def list_all(self) -> list[dict]:
    """Flat member rows for boards-app presets. Includes both modes."""
    rows = await self.list_members()
    out = []
    for m in rows:
        out.append({
            "id": m.get("id"),
            "file": m.get("file"),
            "mode": m.get("mode"),
            "name": m.get("name") or m.get("person_id") or "",
            "role": m.get("role"),
            "dept": m.get("dept"),
            "org_id": m.get("org_id"),
            "reports_to": m.get("reports_to"),
            "model": m.get("model"),
            "person_id": m.get("person_id"),
            "joined": m.get("joined"),
            "created": m.get("created"),
        })
    return out


async def set_field(self, id: str, field: str, value) -> dict:
    if field not in self.SETTABLE_FIELDS:
        return {"error": f"field '{field}' not settable"}
    # Try members first (more numerous than orgs).
    members = await self.list_members()
    target = next((m for m in members if m.get("id") == id), None)
    if target:
        oid = target.get("org_id", "")
        rel = _vault_rel_member(oid, id)
        if field == "system_prompt":
            self._replace_section(rel, "System Prompt", str(value or "").strip())
        else:
            self.vault_update(rel, {field: value})
        # Re-mirror to rooms when an AI member's persona-shaping field changes.
        if (target.get("mode") or "human") == "ai":
            refreshed = dict(target)
            refreshed[field] = value
            await self._push_member_to_rooms(refreshed)
        await self.emit("orgs:member_updated", {
            "member_id": id, "org_id": oid, "field": field, "value": value,
        })
        return {"ok": True}
    # Fall back to orgs.
    orgs = await self.list_orgs()
    target = next((o for o in orgs if o.get("id") == id), None)
    if target:
        if field == "kind" and value not in self.ORG_KINDS:
            return {"error": f"kind must be one of {list(self.ORG_KINDS)}"}
        if field == "reality" and value not in self.ORG_REALITIES:
            return {"error": f"reality must be one of {list(self.ORG_REALITIES)}"}
        if field == "scope" and value not in self.ORG_SCOPES:
            return {"error": f"scope must be one of {list(self.ORG_SCOPES)}"}
        if field == "values":
            value = self._csv_normalize(value)
        if field == "roles":
            value = self._csv_normalize(value)
        rel = _vault_rel_org(id)
        self.vault_update(rel, {field: value})
        await self.emit("orgs:org_updated", {"id": id, "field": field, "value": value})
        return {"ok": True}
    return {"error": "id not found"}
