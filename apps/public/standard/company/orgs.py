"""company — org CRUD — create / read / list / append-section / archive.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The Org primitive's lifecycle: building the standard body, listing with member counts, hydrating a full org (sections + members + role assignments), creating, appending to a body section, and archiving. Source of truth for org notes under 30_Resources/EmptyOS/org/<id>/<id>.md..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.list_members (members) to fold member counts + role assignments into get_org/list_orgs; self._csv_normalize (spine) for values/roles.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk.utils import slugify
from .shared import _now, _vault_rel_org, _vault_rel_member
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   _build_org_body     = _orgs._build_org_body
#   list_orgs           = _orgs.list_orgs
#   get_org             = _orgs.get_org
#   add_org             = _orgs.add_org
#   append_org_section  = _orgs.append_org_section
#   archive_org         = _orgs.archive_org
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _build_org_body(
    self, *, name: str, kind: str, vision: str, mission: str, values: str,
    culture: str, roles: str, parent: str, company_id: str,
) -> str:
    """Render the standard body for a new org note. Empty section
    bodies use a `_not set yet_` placeholder so the structure shows
    through even on a freshly-created org."""
    def _sec(text: str) -> str:
        return (text or "").strip() or "_not set yet_"
    return (
        f"# {name}\n\n"
        f"**Kind:** {kind}\n"
        + (f"**Parent:** [[{parent}]]\n" if parent else "")
        + (f"**Persona Sim:** [[{company_id}]]\n" if company_id else "")
        + "\n## Mission\n\n" + _sec(mission)
        + "\n\n## Vision\n\n" + _sec(vision)
        + "\n\n## Values\n\n" + _sec(values)
        + "\n\n## Culture\n\n" + _sec(culture)
        + "\n\n## Roles\n\n" + _sec(roles)
        + "\n\n## Decisions\n\n_Append-only log. Latest first._\n"
        + "\n## Rituals\n\n_Standing meetings, reviews, cadence._\n"
        + "\n## Members\n\nMembers live as separate notes under `members/`.\n"
        + "\n## Assets\n\n_Linked KB notes + vault paths fed into every scenario prompt. Manage via the Assets panel or `POST /orgs/api/orgs/<id>/assets`._\n"
        + "\n## Notes\n\nFree-form notes about this organisation.\n"
    )


async def list_orgs(
    self, *, reality: str = "", scope: str = "", kind: str = "",
) -> list[dict]:
    rows = self._orgs.list()
    if reality:
        rows = [r for r in rows if (r.get("reality") or "real") == reality]
    if scope:
        rows = [r for r in rows if (r.get("scope") or "member") == scope]
    if kind:
        rows = [r for r in rows if (r.get("kind") or "other") == kind]
    all_members = self._members.list()
    humans_by_org: dict[str, int] = {}
    ai_by_org: dict[str, int] = {}
    for m in all_members:
        oid = (m.get("org_id") or "").strip()
        if not oid:
            continue
        mode = (m.get("mode") or "human").strip() or "human"
        if mode == "ai":
            ai_by_org[oid] = ai_by_org.get(oid, 0) + 1
        else:
            humans_by_org[oid] = humans_by_org.get(oid, 0) + 1
    for o in rows:
        oid = o.get("id") or (o.get("file") or "").replace(".md", "")
        o["id"] = oid
        o["reality"] = (o.get("reality") or "real").strip() or "real"
        o["scope"] = (o.get("scope") or "member").strip() or "member"
        o["kind"] = (o.get("kind") or "other").strip() or "other"
        o["human_count"] = humans_by_org.get(oid, 0)
        o["ai_count"] = ai_by_org.get(oid, 0)
        o["member_count"] = o["human_count"] + o["ai_count"]
    return rows


async def get_org(self, id: str) -> dict | None:
    oid = (id or "").strip()
    if not oid:
        return None
    rel = _vault_rel_org(oid)
    fm = self.vault_get_properties(rel) or {}
    if not fm:
        return None
    sections: dict[str, str] = {}
    for sec in self.ORG_BODY_SECTIONS:
        try:
            sections[sec] = (self.vault_read_section(rel, sec) or "").strip()
        except Exception:
            sections[sec] = ""
    members = await self.list_members(org_id=oid)
    # Resolve human members to a person snapshot via apps/people/.
    for m in members:
        if (m.get("mode") or "human") != "human":
            m["person"] = None
            continue
        pid = (m.get("person_id") or "").strip()
        if not pid:
            m["person"] = None
            continue
        try:
            person = await self.call_app("people", "get_person", id=pid)
        except Exception:
            person = None
        m["person"] = person if isinstance(person, dict) else None
    # AI members get their system prompt hydrated from the body section.
    for m in members:
        if (m.get("mode") or "human") != "ai":
            continue
        mid = m.get("id")
        if not mid:
            continue
        mrel = _vault_rel_member(oid, mid)
        sp = (self.vault_read_section(mrel, "System Prompt") or "").strip()
        m["system_prompt"] = sp
    raw_values = (fm.get("values") or "").strip()
    values_list = [v.strip() for v in raw_values.split(",") if v.strip()] if raw_values else []
    raw_roles = (fm.get("roles") or "").strip()
    roles_list = [r.strip() for r in raw_roles.split(",") if r.strip()] if raw_roles else []
    # Roles → member assignments (one role can have N members; vacancy = no member).
    # Slug-match both sides so "Narrative Strategist" on a member matches
    # "narrative-strategist" in the org's roles list. The displayed role
    # text on the member row stays whatever the user/persona originally
    # wrote — slugification is only used for the assignment lookup.
    role_slug_to_label = {slugify(r): r for r in roles_list}
    role_assignments: dict[str, list[dict]] = {r: [] for r in roles_list}
    for m in members:
        mrole = (m.get("role") or "").strip()
        if not mrole:
            continue
        mrole_slug = slugify(mrole)
        label = role_slug_to_label.get(mrole_slug)
        if label:
            role_assignments[label].append(m)
            m["matched_role"] = label  # surface for UI chip
        elif mrole in role_assignments:
            # Back-compat: exact-string match still works
            role_assignments[mrole].append(m)
            m["matched_role"] = mrole
    return {
        "id": oid,
        "name": fm.get("name") or oid,
        "kind": fm.get("kind") or "other",
        "reality": fm.get("reality") or "real",
        "scope": fm.get("scope") or "member",
        "vision": fm.get("vision") or "",
        "mission": fm.get("mission") or "",
        "values": values_list,
        "values_raw": raw_values,
        "culture": fm.get("culture") or "",
        "roles": roles_list,
        "roles_raw": raw_roles,
        "role_assignments": role_assignments,
        "parent": fm.get("parent") or "",
        "company_id": fm.get("company_id") or "",
        "created": fm.get("created") or "",
        "vault_path": rel,
        "sections": sections,
        "members": members,
    }


async def add_org(
    self, name: str, *,
    kind: str = "team",
    reality: str = "real",
    scope: str = "member",
    mission: str = "",
    vision: str = "",
    values: str = "",
    culture: str = "",
    roles: str = "",
    parent: str = "",
    company_id: str = "",
) -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "name required"}
    kind = (kind or "team").strip() or "team"
    if kind not in self.ORG_KINDS:
        return {"error": f"kind must be one of {list(self.ORG_KINDS)}"}
    reality = (reality or "real").strip() or "real"
    if reality not in self.ORG_REALITIES:
        return {"error": f"reality must be one of {list(self.ORG_REALITIES)}"}
    scope = (scope or "member").strip() or "member"
    if scope not in self.ORG_SCOPES:
        return {"error": f"scope must be one of {list(self.ORG_SCOPES)}"}
    values_text = self._csv_normalize(values)
    roles_text = self._csv_normalize(roles)
    oid = slugify(name)
    rel = _vault_rel_org(oid)
    if self.vault_get_properties(rel):
        return {"error": f"org '{oid}' already exists"}
    fm = {
        "id": oid,
        "name": name,
        "kind": kind,
        "reality": reality,
        "scope": scope,
        "vision": (vision or "").strip(),
        "mission": (mission or "").strip(),
        "values": values_text,
        "culture": (culture or "").strip(),
        "roles": roles_text,
        "parent": parent,
        "company_id": company_id,
        "tags": ["org"],
        "created": _now(),
    }
    body = self._build_org_body(
        name=name, kind=kind,
        vision=vision, mission=mission, values=values_text,
        culture=culture, roles=roles_text, parent=parent, company_id=company_id,
    )
    self.vault_create_note(rel, fm, body)
    await self.emit("orgs:org_created", {
        "id": oid, "name": name, "kind": kind, "reality": reality, "scope": scope,
    })
    return {"id": oid, "name": name, "vault_path": rel}


async def append_org_section(
    self, org_id: str, section: str, text: str, *, prepend_date: bool = True,
) -> dict:
    oid = (org_id or "").strip()
    section = (section or "").strip()
    text = (text or "").strip()
    if not oid or not section or not text:
        return {"error": "org_id, section, and text required"}
    if section not in self.ORG_BODY_SECTIONS:
        return {"error": f"section must be one of {self.ORG_BODY_SECTIONS}"}
    rel = _vault_rel_org(oid)
    if not self.vault_get_properties(rel):
        return {"error": f"org '{oid}' not found"}
    line = text
    if prepend_date:
        line = f"- {_now()[:10]} — {text}"
    try:
        self.vault_append_section(rel, section, line)
    except Exception as e:
        return {"error": f"append failed: {e}"}
    await self.emit("orgs:org_updated", {
        "id": oid, "field": f"section:{section}", "value": text,
    })
    return {"ok": True, "section": section, "line": line}


async def archive_org(self, org_id: str) -> dict:
    oid = (org_id or "").strip()
    rel = _vault_rel_org(oid)
    if not self.vault_get_properties(rel):
        return {"error": f"org '{oid}' not found"}
    self.vault_update(rel, {"archived": True, "archived_ts": _now()})
    await self.emit("orgs:org_archived", {"id": oid})
    return {"ok": True, "archived": rel}
