"""Orgs — one primitive covering real teams, virtual sims, external orgs, and mixed.

An **org** is a saveable organisation (real or virtual) with N **members**
(humans linked to apps/people/ or AI personas with system prompts). Scenarios
(critique / workshop / interview) fan a prompt out across an org's AI members.

Axes:
  - `reality` ∈ {real, virtual} — does this org exist in the world, or is it a sim?
  - `scope`   ∈ {member, external} — are you in it, or just tracking it?
  - `kind`    ∈ {team, household, side-business, community, employer, vendor, other}

Members:
  - `mode=human` → carries `person_id` → links into apps/people/
  - `mode=ai`    → carries `name`, `system_prompt`, `model`, `emoji` → mirrors into rooms

Orgs + members live in the vault under `30_Resources/EmptyOS/org/<org_id>/`.
Scenario run records persist as JSON under `data/apps/company/runs/`.
Pending [DO:] actions (workshop only) live at `data/apps/company/pending/`.

Note: app id stays `company` for internal stability — manifest display name is
"Orgs" and URL prefix is `/orgs`. The internal name `CompanyApp` is preserved
for the same reason (rename would ripple into every dependency graph).
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, cli_command

from . import assets as _assets
from . import boards as _boards
from . import dispatch as _dispatch
from . import members as _members
from . import migration as _migration
from . import orgs as _orgs
from . import routes as _routes
from .libraries import MemberLibrary, OrgLibrary
from .shared import (  # noqa: F401  (re-exported for tests / external imports)
    _LEGACY_COMPANY_PREFIX,
    _legacy_rel_company,
    _legacy_rel_worker,
    _now,
    _vault_rel_member,
    _vault_rel_org,
)


class CompanyApp(BaseApp):

    # Whitelist for cross-app set_field calls (boards inline-edit, voice intents).
    SETTABLE_FIELDS = {
        # Org fields
        "name", "kind", "reality", "scope",
        "vision", "mission", "values", "culture", "roles",
        "parent", "company_id",
        # Member fields (both modes)
        "role", "dept", "reports_to", "emoji", "model", "system_prompt",
        # Member human-only
        "person_id", "joined", "left",
    }

    ORG_BODY_SECTIONS = [
        "Mission", "Vision", "Values", "Culture", "Roles",
        "Decisions", "Rituals", "Members", "Assets", "Notes",
    ]

    # Per-asset body cap inside list_assets; total org-context budget enforced
    # in scenarios/base.org_context_suffix.
    ASSET_BODY_CHARS = 2000

    ORG_KINDS = ("team", "household", "side-business", "community", "employer", "vendor", "other")

    ORG_REALITIES = ("real", "virtual")

    ORG_SCOPES = ("member", "external")

    MEMBER_MODES = ("human", "ai")

    async def setup(self):
        await super().setup()
        self._orgs = OrgLibrary(self)
        self._members = MemberLibrary(self)
        await self._migrate_v0_3_org_unification()
        await self._reseed_room_personas()

    def _replace_section(self, rel_path: str, section: str, new_text: str) -> None:
        """Replace the body of a `## <section>` heading in a vault note."""
        import re
        vault = self.kernel.config.notes_path
        if not vault:
            return
        path = vault / rel_path
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8")
        heading = f"## {section}"
        pattern = re.compile(
            rf"(^{re.escape(heading)}\s*\n)(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        replacement = f"{heading}\n\n{new_text.rstrip()}\n\n"
        if pattern.search(content):
            content = pattern.sub(replacement, content, count=1)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + replacement
        path.write_text(content, encoding="utf-8")
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            try:
                vi._index_one(rel_path, path)
            except Exception:
                pass

    def _csv_normalize(self, raw) -> str:
        """Accept list or comma-separated string, return canonical CSV text."""
        if isinstance(raw, (list, tuple)):
            return ", ".join(str(v).strip() for v in raw if str(v).strip())
        return (raw or "").strip()

    @cli_command("list")
    async def cli_list(self):
        """List all orgs."""
        rows = await self.list_orgs()
        if not rows:
            return "No orgs yet. Create one via /orgs/ or `eos company add`."
        lines = []
        for o in rows:
            badge = f"{o['reality']}/{o['scope']}"
            counts = f"{o.get('member_count', 0)} members ({o.get('ai_count', 0)} AI)"
            lines.append(f"{o['id']:<24} {o.get('name', ''):<32} {badge:<18} {counts}")
        return "\n".join(lines)

    @cli_command("show")
    async def cli_show(self, id: str):
        """Show an org and its members."""
        o = await self.get_org(id)
        if not o:
            return f"Org '{id}' not found."
        lines = [
            f"{o['name']} ({o['id']}) — {o['reality']}/{o['scope']}, kind={o['kind']}",
            f"Vision: {o.get('vision') or '—'}",
            f"Mission: {o.get('mission') or '—'}",
            f"Culture: {o.get('culture') or '—'}",
            f"Members ({len(o['members'])}):",
        ]
        for m in o["members"]:
            mode = m.get("mode") or "human"
            who = m.get("name") if mode == "ai" else (m.get("person_id") or m.get("id"))
            lines.append(f"  - [{mode}] {who} — {m.get('role') or 'no role'} ({m.get('id')})")
        return "\n".join(lines)

    # ── Assets (extracted to assets.py) ──
    _is_vault_path      = _assets._is_vault_path
    _read_assets_refs   = _assets._read_assets_refs
    _render_assets_body = _assets._render_assets_body
    list_assets         = _assets.list_assets
    add_asset           = _assets.add_asset
    remove_asset        = _assets.remove_asset

    # ── Boards (extracted to boards.py) ──
    list_all  = _boards.list_all
    set_field = _boards.set_field

    # ── Dispatch (extracted to dispatch.py) ──
    run_scenario         = _dispatch.run_scenario
    _CHAIN_FRAMINGS      = _dispatch._CHAIN_FRAMINGS
    chain_scenario       = _dispatch.chain_scenario
    _run_in_room         = _dispatch._run_in_room
    _append_run_memory   = _dispatch._append_run_memory
    route_to_specialist  = _dispatch.route_to_specialist

    # ── Members (extracted to members.py) ──
    list_members                  = _members.list_members
    add_member                    = _members.add_member
    remove_member                 = _members.remove_member
    _push_member_to_rooms         = _members._push_member_to_rooms
    _unregister_member_from_rooms = _members._unregister_member_from_rooms

    # ── Migration (extracted to migration.py) ──
    _migrate_v0_3_org_unification = _migration._migrate_v0_3_org_unification
    _do_migration                 = _migration._do_migration
    _reseed_room_personas         = _migration._reseed_room_personas

    # ── Orgs (extracted to orgs.py) ──
    _build_org_body    = _orgs._build_org_body
    list_orgs          = _orgs.list_orgs
    get_org            = _orgs.get_org
    add_org            = _orgs.add_org
    append_org_section = _orgs.append_org_section
    archive_org        = _orgs.archive_org

    # ── Routes (extracted to routes.py) ──
    api_list_orgs          = _routes.api_list_orgs
    api_get_org            = _routes.api_get_org
    api_add_org            = _routes.api_add_org
    api_append_org_section = _routes.api_append_org_section
    api_archive_org        = _routes.api_archive_org
    api_list_assets        = _routes.api_list_assets
    api_add_asset          = _routes.api_add_asset
    api_remove_asset       = _routes.api_remove_asset
    api_add_member         = _routes.api_add_member
    api_remove_member      = _routes.api_remove_member
    api_list_memberships   = _routes.api_list_memberships
    api_set_field          = _routes.api_set_field
    api_list_scenarios     = _routes.api_list_scenarios
    api_run_scenario       = _routes.api_run_scenario
    api_chain_scenario     = _routes.api_chain_scenario
    api_route_to_specialist = _routes.api_route_to_specialist
    api_list_runs          = _routes.api_list_runs
    api_get_run            = _routes.api_get_run
    api_apply_pending      = _routes.api_apply_pending
    api_reject_pending     = _routes.api_reject_pending
