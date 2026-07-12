"""company — boot-time v0.3 legacy company/worker → org/member migration.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The sentinel-gated, idempotent migration that rewrites legacy `company`+`worker` notes into the unified `org`+`org-member` shape, plus the room-persona reseed that mirrors every AI member into rooms on boot. Source of truth for the one-time data move; nothing else reads the legacy `30_Resources/EmptyOS/company/` path..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._build_org_body (orgs) to render migrated org bodies; self.list_members + self._push_member_to_rooms (members) for the reseed.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from .shared import _now, _vault_rel_org, _vault_rel_member, _legacy_rel_worker, _LEGACY_COMPANY_PREFIX
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CompanyApp  # noqa: F401 — for type hints only


# ─── Bind to CompanyApp class as ────────────────────────────────
#   _migrate_v0_3_org_unification  = _migration._migrate_v0_3_org_unification
#   _do_migration                  = _migration._do_migration
#   _reseed_room_personas          = _migration._reseed_room_personas
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def _migrate_v0_3_org_unification(self) -> None:
    sentinel = self.data_dir / ".migrated_v0.3_org_unification"
    if sentinel.exists():
        return
    try:
        await self._do_migration()
    except Exception as e:
        # Don't block boot on migration failure — log and continue.
        try:
            self.log_warn(f"v0.3 org-unification migration failed: {e}")
        except Exception:
            pass
        return
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(_now(), encoding="utf-8")


async def _do_migration(self) -> None:
    # 1) Migrate legacy companies → orgs (reality=virtual, scope=member).
    legacy_companies = self.vault_query(tags=["company"]) or []
    migrated_orgs: list[str] = []
    for entry in legacy_companies:
        path = (entry.get("path") or "").replace("\\", "/")
        if not path.startswith(_LEGACY_COMPANY_PREFIX):
            continue
        fm = entry.get("frontmatter") or self.vault_get_properties(path) or {}
        cid = (fm.get("id") or "").strip()
        if not cid:
            # Derive from filename if frontmatter is missing the id
            cid = path.rsplit("/", 1)[-1].replace(".md", "")
        new_rel = _vault_rel_org(cid)
        if self.vault_get_properties(new_rel):
            migrated_orgs.append(cid)
            continue
        name = fm.get("name") or cid
        mission = (fm.get("mission") or "").strip()
        culture = (fm.get("culture") or "").strip()
        new_fm = {
            "id": cid,
            "name": name,
            "kind": "other",
            "reality": "virtual",
            "scope": "member",
            "vision": "",
            "mission": mission,
            "values": "",
            "culture": culture,
            "roles": "",
            "parent": "",
            "company_id": "",
            "tags": ["org"],
            "created": fm.get("created") or _now(),
        }
        body = self._build_org_body(
            name=name, kind="other",
            vision="", mission=mission, values="",
            culture=culture, roles="", parent="", company_id="",
        )
        self.vault_create_note(new_rel, new_fm, body)
        migrated_orgs.append(cid)

    # 2) Migrate legacy workers → members (mode=ai).
    legacy_workers = self.vault_query(tags=["worker"]) or []
    for entry in legacy_workers:
        path = (entry.get("path") or "").replace("\\", "/")
        if not path.startswith(_LEGACY_COMPANY_PREFIX):
            continue
        fm = entry.get("frontmatter") or self.vault_get_properties(path) or {}
        wid = (fm.get("id") or "").strip()
        cid = (fm.get("company_id") or "").strip()
        if not wid or not cid:
            continue
        new_rel = _vault_rel_member(cid, wid)
        if self.vault_get_properties(new_rel):
            continue
        # Source the system prompt from the legacy worker's body section
        # (canonical location after the v0.2 promotion), with frontmatter
        # fallback for legacy notes that never went through that pass.
        legacy_path = _legacy_rel_worker(cid, wid)
        sys_prompt = (self.vault_read_section(legacy_path, "System Prompt") or "").strip()
        if not sys_prompt:
            sys_prompt = (fm.get("system_prompt") or "").strip()
        new_fm = {
            "id": wid,
            "org_id": cid,
            "mode": "ai",
            "role": fm.get("role") or "",
            "dept": fm.get("dept") or "",
            "reports_to": fm.get("reports_to") or "",
            "person_id": "",
            "joined": "",
            "left": "",
            "name": fm.get("name") or wid,
            "model": fm.get("model") or "",
            "emoji": fm.get("emoji") or "",
            "archived": fm.get("archived") or "",
            "tags": ["org-member"],
            "created": fm.get("created") or _now(),
        }
        name = fm.get("name") or wid
        role = fm.get("role") or ""
        body = (
            f"# {name}\n\n"
            f"**Mode:** ai\n"
            f"**Role:** {role or '_not set_'}\n"
            f"**Org:** [[{cid}]]\n\n"
            "## System Prompt\n\n"
            f"{sys_prompt or '_not set_'}\n\n"
            "## Scenarios\n\nScenario runs this member participated in.\n"
        )
        self.vault_create_note(new_rel, new_fm, body)


async def _reseed_room_personas(self) -> None:
    """After migration (or for any freshly-installed system), make sure
    every AI member is mirrored into rooms as a 1:1 agent. Idempotent
    on the rooms side via the source-tagged register_persona contract.
    """
    try:
        members = await self.list_members(mode="ai")
    except Exception:
        return
    for m in members:
        if (m.get("archived") or "") in ("True", "true", True, "1"):
            continue
        await self._push_member_to_rooms(m)
