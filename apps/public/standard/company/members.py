"""company — member CRUD + AI-member rooms persona mirror.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The Member primitive (mode=human links people/, mode=ai carries a system prompt): listing, adding, removing (soft-archive), and the idempotent mirror of every AI member into rooms as a 1:1 agent. Source of truth for member notes under <org>/members/<mid>.md..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.call_app('rooms', 'register_persona'|'unregister_persona') for the mirror; self.vault_read_section for the System Prompt body.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from emptyos.sdk.utils import slugify
from .shared import _now, _vault_rel_org, _vault_rel_member
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   list_members                   = _members.list_members
#   add_member                     = _members.add_member
#   remove_member                  = _members.remove_member
#   _push_member_to_rooms          = _members._push_member_to_rooms
#   _unregister_member_from_rooms  = _members._unregister_member_from_rooms
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def list_members(
    self, *, org_id: str = "", person_id: str = "", mode: str = "",
) -> list[dict]:
    rows = self._members.list()
    if org_id:
        rows = [r for r in rows if (r.get("org_id") or "") == org_id]
    if person_id:
        rows = [r for r in rows if (r.get("person_id") or "") == person_id]
    if mode:
        rows = [r for r in rows if (r.get("mode") or "human") == mode]
    for m in rows:
        mid = m.get("id") or (m.get("file") or "").replace(".md", "")
        m["id"] = mid
        m["mode"] = (m.get("mode") or "human").strip() or "human"
    return rows


async def add_member(
    self, org_id: str, *,
    mode: str = "human",
    # human-only
    person_id: str = "",
    # ai-only
    name: str = "",
    system_prompt: str = "",
    model: str = "",
    emoji: str = "",
    # common
    role: str = "",
    dept: str = "",
    reports_to: str = "",
    joined: str = "",
) -> dict:
    org_id = (org_id or "").strip()
    mode = (mode or "human").strip() or "human"
    if not org_id:
        return {"error": "org_id required"}
    if mode not in self.MEMBER_MODES:
        return {"error": f"mode must be one of {list(self.MEMBER_MODES)}"}
    if not self.vault_get_properties(_vault_rel_org(org_id)):
        return {"error": f"org '{org_id}' not found"}

    if mode == "human":
        person_id = (person_id or "").strip()
        if not person_id:
            return {"error": "person_id required for mode=human"}
        existing = await self.list_members(org_id=org_id, person_id=person_id)
        active = [m for m in existing if not (m.get("left") or "").strip()]
        if active:
            return {"error": f"person '{person_id}' is already a member of '{org_id}'"}
        mid = slugify(f"{org_id}-{person_id}-{_now()[:10]}")
        display_name = person_id
    else:  # ai
        name = (name or "").strip()
        if not name:
            return {"error": "name required for mode=ai"}
        mid = slugify(f"{org_id}-{name}")
        display_name = name
        if not system_prompt.strip():
            system_prompt = (
                f"You are {name}, the {role or 'team member'} at this org. "
                "Stay in role. Be specific. Disagree when you genuinely do."
            )

    rel = _vault_rel_member(org_id, mid)
    if self.vault_get_properties(rel):
        return {"error": f"member '{mid}' already exists"}

    fm: dict = {
        "id": mid,
        "org_id": org_id,
        "mode": mode,
        "role": role,
        "dept": dept,
        "reports_to": reports_to,
        "joined": joined or _now()[:10],
        "left": "",
        "tags": ["org-member"],
        "created": _now(),
    }
    if mode == "human":
        fm["person_id"] = person_id
        body = (
            f"# Member: {person_id} → {org_id}\n\n"
            f"**Mode:** human\n"
            f"**Person:** [[{person_id}]]\n"
            f"**Org:** [[{org_id}]]\n"
            f"**Role:** {role or '_not set_'}\n"
            + (f"**Reports to:** [[{reports_to}]]\n" if reports_to else "")
            + f"**Joined:** {fm['joined']}\n\n"
            "## Notes\n\nFree-form notes about this person's role in this org.\n"
        )
    else:  # ai
        fm["name"] = name
        fm["model"] = model
        fm["emoji"] = emoji
        body = (
            f"# {name}\n\n"
            f"**Mode:** ai\n"
            f"**Role:** {role or '_not set_'}\n"
            f"**Org:** [[{org_id}]]\n\n"
            "## System Prompt\n\n"
            f"{system_prompt}\n\n"
            "## Scenarios\n\nScenario runs this member participated in.\n"
        )

    self.vault_create_note(rel, fm, body)

    if mode == "ai":
        await self._push_member_to_rooms({
            **fm, "system_prompt": system_prompt,
        })

    await self.emit("orgs:member_added", {
        "org_id": org_id, "member_id": mid, "mode": mode, "name": display_name,
    })
    return {"id": mid, "org_id": org_id, "mode": mode, "vault_path": rel}


async def remove_member(self, member_id: str) -> dict:
    mid = (member_id or "").strip()
    members = await self.list_members()
    target = next((m for m in members if m.get("id") == mid), None)
    if not target:
        return {"error": "member not found"}
    oid = target.get("org_id") or ""
    rel = _vault_rel_member(oid, mid)
    self.vault_update(rel, {"left": _now()[:10], "archived": True, "archived_ts": _now()})
    if (target.get("mode") or "human") == "ai":
        await self._unregister_member_from_rooms(mid, oid)
    await self.emit("orgs:member_removed", {
        "org_id": oid, "member_id": mid, "mode": target.get("mode") or "human",
    })
    return {"ok": True, "archived": rel}


async def _push_member_to_rooms(self, member: dict) -> None:
    """Idempotent: mirror an AI member into rooms as a 1:1 agent.
    Quietly no-ops when rooms is uninstalled — AI members still work
    in headless scenarios via direct self.think()."""
    if (member.get("mode") or "human") != "ai":
        return
    mid = member.get("id")
    oid = member.get("org_id") or ""
    if not mid:
        return
    sys_prompt = (member.get("system_prompt") or "").strip()
    if not sys_prompt:
        rel = _vault_rel_member(oid, mid)
        sys_prompt = (self.vault_read_section(rel, "System Prompt") or "").strip()
    try:
        await self.call_app(
            "rooms", "register_persona",
            id=mid,
            name=member.get("name") or mid,
            system_prompt=sys_prompt,
            model=member.get("model") or "",
            source=f"orgs:{oid}",
            emoji=member.get("emoji") or "",
        )
    except Exception:
        pass


async def _unregister_member_from_rooms(self, member_id: str, org_id: str) -> None:
    try:
        await self.call_app(
            "rooms", "unregister_persona",
            id=member_id, source=f"orgs:{org_id}",
        )
    except Exception:
        pass
