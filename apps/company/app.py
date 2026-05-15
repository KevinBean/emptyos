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

from datetime import datetime, timezone

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import slugify

from .libraries import MemberLibrary, OrgLibrary
from .scenarios import SCENARIO_META, SCENARIOS
from .scenarios import workshop as workshop_mod
from .scenarios.base import list_runs, load_run, save_run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vault_rel_org(org_id: str) -> str:
    return f"30_Resources/EmptyOS/org/{org_id}/{org_id}.md"


def _vault_rel_member(org_id: str, member_id: str) -> str:
    return f"30_Resources/EmptyOS/org/{org_id}/members/{member_id}.md"


# Legacy paths — only used by the boot-time migration. Once `.migrated_v0.3_org_unification`
# sentinel is written, nothing else touches these. Old notes stay on disk as orphans
# until the user manually cleans `30_Resources/EmptyOS/company/`.
def _legacy_rel_company(cid: str) -> str:
    return f"30_Resources/EmptyOS/company/{cid}/{cid}.md"


def _legacy_rel_worker(cid: str, wid: str) -> str:
    return f"30_Resources/EmptyOS/company/{cid}/workers/{wid}.md"


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

    # ── Migration: v0.3 org unification ────────────────────────────
    #
    # Sentinel-gated, idempotent. Reads legacy `company` + `worker` notes
    # via vault_query (tags still present on old notes), writes them as
    # `org` + `org-member` notes in the new path. Old notes stay on disk
    # as orphans — no library queries them after the rename.

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
            if not path.startswith("30_Resources/EmptyOS/company/"):
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
            if not path.startswith("30_Resources/EmptyOS/company/"):
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

    # ── CRUD: orgs ────────────────────────────────────────────────

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

    # ── Assets: vault paths + KB slugs an org carries into scenarios ──

    @staticmethod
    def _is_vault_path(ref: str) -> bool:
        """A ref looks like a vault path if it has a slash or ends in .md."""
        r = (ref or "").strip()
        return ("/" in r) or r.endswith(".md")

    def _read_assets_refs(self, oid: str) -> list[str]:
        fm = self.vault_get_properties(_vault_rel_org(oid)) or {}
        raw = fm.get("assets") or []
        # Frontmatter may parse a single-entry list as a bare string — coerce.
        if isinstance(raw, str):
            raw = [raw]
        return [str(x).strip() for x in raw if str(x).strip()]

    def _render_assets_body(self, refs: list[str]) -> str:
        """Render the `## Assets` body section as wikilinks (vault paths) or
        bare slugs (KB) for human navigation. Frontmatter is the queryable
        index; body is for reading."""
        if not refs:
            return "_No assets linked yet. Add via the Assets panel or `POST /orgs/api/orgs/<id>/assets`._"
        lines = []
        for ref in refs:
            if self._is_vault_path(ref):
                lines.append(f"- [[{ref}]]")
            else:
                lines.append(f"- KB: `{ref}`")
        return "\n".join(lines)

    async def list_assets(self, org_id: str) -> list[dict]:
        """Resolve an org's `assets:` array into reader-friendly records.
        Hybrid: bare slug → KB note via kb_explain; vault path → vault body.
        Each body capped to ASSET_BODY_CHARS so 4 assets stay under ~8k.

        Returns [{kind, ref, title, body}, ...]. Failed lookups become
        {kind, ref, title: ref, body: "", error: "..."} so the caller still
        sees the broken link without crashing the scenario.
        """
        oid = (org_id or "").strip()
        if not oid or not self.vault_get_properties(_vault_rel_org(oid)):
            return []
        out: list[dict] = []
        for ref in self._read_assets_refs(oid):
            try:
                if self._is_vault_path(ref):
                    props = self.vault_get_properties(ref) or {}
                    body = self.vault_read_body(ref) or ""
                    title = (
                        props.get("title")
                        or props.get("name")
                        or ref.rsplit("/", 1)[-1].replace(".md", "")
                    )
                    out.append({
                        "kind": "vault",
                        "ref": ref,
                        "title": str(title),
                        "body": (body or "")[: self.ASSET_BODY_CHARS],
                    })
                else:
                    note = await self.kb_explain(ref) or {}
                    if note.get("error"):
                        out.append({
                            "kind": "kb", "ref": ref, "title": ref,
                            "body": "", "error": note.get("error"),
                        })
                        continue
                    # kb.get_note returns kind + title inside `properties`, not
                    # top-level. Docs additionally store their content in a
                    # `paragraphs_json` field that get_note doesn't decode — the
                    # full structured body is only available via kb.get_doc.
                    props = note.get("properties") or {}
                    kb_kind = (props.get("kind") or note.get("kind") or "").strip()
                    title = (
                        props.get("title")
                        or note.get("title")
                        or note.get("name")
                        or ref
                    )
                    body = note.get("body") or ""
                    if kb_kind == "doc":
                        try:
                            doc = await self.call_app("kb", "get_doc", slug=ref) or {}
                        except Exception:
                            doc = {}
                        paragraphs = doc.get("paragraphs") or []
                        if paragraphs:
                            # Canonical shape is {title, content}; tolerate
                            # the older {heading, text} aliases on read.
                            rendered = "\n\n".join(
                                f"## {(p.get('title') or p.get('heading') or '')}\n"
                                f"{(p.get('content') or p.get('text') or '')}".strip()
                                for p in paragraphs
                                if p.get("title") or p.get("heading")
                                   or p.get("content") or p.get("text")
                            )
                            if rendered:
                                body = rendered
                        title = doc.get("title") or title
                    out.append({
                        "kind": "kb",
                        "ref": ref,
                        "title": str(title),
                        "body": body[: self.ASSET_BODY_CHARS],
                        "kb_kind": kb_kind,
                    })
            except Exception as e:
                out.append({
                    "kind": "vault" if self._is_vault_path(ref) else "kb",
                    "ref": ref, "title": ref, "body": "", "error": str(e)[:200],
                })
        return out

    async def add_asset(self, org_id: str, ref: str) -> dict:
        oid = (org_id or "").strip()
        ref = (ref or "").strip()
        if not oid or not ref:
            return {"error": "org_id and ref required"}
        rel = _vault_rel_org(oid)
        if not self.vault_get_properties(rel):
            return {"error": f"org '{oid}' not found"}
        # Validate the ref points somewhere readable. Don't block on KB
        # unavailability — a freshly-clipped clause may be authored before
        # the KB note exists.
        if self._is_vault_path(ref) and not self.vault_get_properties(ref):
            return {"error": f"vault path '{ref}' not found"}
        refs = self._read_assets_refs(oid)
        if ref in refs:
            return {"ok": True, "ref": ref, "note": "already linked"}
        refs.append(ref)
        self.vault_update(rel, {"assets": refs})
        self._replace_section(rel, "Assets", self._render_assets_body(refs))
        await self.emit("orgs:org_updated", {
            "id": oid, "field": "assets", "value": ref, "op": "add",
        })
        return {"ok": True, "ref": ref, "asset_count": len(refs)}

    async def remove_asset(self, org_id: str, ref: str) -> dict:
        oid = (org_id or "").strip()
        ref = (ref or "").strip()
        if not oid or not ref:
            return {"error": "org_id and ref required"}
        rel = _vault_rel_org(oid)
        if not self.vault_get_properties(rel):
            return {"error": f"org '{oid}' not found"}
        refs = self._read_assets_refs(oid)
        if ref not in refs:
            return {"error": f"ref '{ref}' not linked"}
        refs = [r for r in refs if r != ref]
        self.vault_update(rel, {"assets": refs})
        self._replace_section(rel, "Assets", self._render_assets_body(refs))
        await self.emit("orgs:org_updated", {
            "id": oid, "field": "assets", "value": ref, "op": "remove",
        })
        return {"ok": True, "ref": ref, "asset_count": len(refs)}

    # ── CRUD: members ─────────────────────────────────────────────

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

    # ── Persona mirror to rooms (AI members only) ──────────────────

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

    # ── Boards integration ────────────────────────────────────────

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

    # ── Scenario dispatch (AI members only) ───────────────────────

    async def run_scenario(
        self, org_id: str, scenario_type: str, prompt: str,
        mode: str = "headless",
    ) -> dict:
        org = await self.get_org(org_id)
        if not org:
            return {"error": f"org '{org_id}' not found"}
        ai_members = [m for m in (org.get("members") or []) if (m.get("mode") or "human") == "ai"]
        if not ai_members:
            return {"error": "org has no AI members — add one to run scenarios"}
        if scenario_type not in SCENARIOS:
            return {"error": f"unknown scenario '{scenario_type}'"}
        prompt = (prompt or "").strip()
        if not prompt:
            return {"error": "prompt required"}
        if mode not in ("headless", "in-room"):
            mode = "headless"

        # Compose a scenario-compatible "company" shape so existing scenario
        # modules keep working without rewrites. They expect company.id +
        # company.name + workers list with id/name/role/emoji/model/system_prompt.
        # Vision/values/roles are passed through so org_context_suffix can
        # inject them into every per-member system prompt.
        shim = {
            "id": org["id"],
            "name": org["name"],
            "mission": org.get("mission") or "",
            "vision": org.get("vision") or "",
            "values": org.get("values") or "",
            "culture": org.get("culture") or "",
            "roles": org.get("roles") or "",
            "workers": ai_members,
        }

        if mode == "in-room":
            return await self._run_in_room(shim, ai_members, scenario_type, prompt)

        scenario = SCENARIOS[scenario_type]
        record = await scenario.run(self, shim, ai_members, prompt, mode)
        await self._append_run_memory(shim, ai_members, record)
        return record

    # Default framings when chaining one scenario's output into the next.
    # Each maps next_scenario → wrapper. The full composed prompt is:
    #   {framing}\n\n--- PRIOR {prior_scenario} OUTPUT ---\n{digest}\n--- END ---
    _CHAIN_FRAMINGS = {
        "critique": (
            "Red-team the following output from the team's prior {prior} session. "
            "Pick it apart from your role. Where will it fail in week 1? What's the "
            "biggest unstated assumption?"
        ),
        "workshop": (
            "The team produced the output below in a prior {prior} session. Revise "
            "it from your role — address the strongest objections, keep what works, "
            "produce a sharpened concrete draft."
        ),
        "interview": (
            "Given the prior {prior} session output below, answer the standing "
            "interview question from your role with the new context in mind."
        ),
    }

    async def chain_scenario(
        self,
        prior_run_id: str,
        next_scenario: str,
        *,
        framing: str = "",
        mode: str = "headless",
    ) -> dict:
        """Run `next_scenario` against the same org as `prior_run_id`, feeding
        the prior run's digest (or concatenated responses if no digest) into
        the new prompt. The resulting record carries `parent_run_id` so the
        run history forms a chain.

        `framing` overrides the default per-target framing in `_CHAIN_FRAMINGS`.
        """
        prior = load_run(self, (prior_run_id or "").strip())
        if not prior:
            return {"error": f"prior run '{prior_run_id}' not found"}
        if next_scenario not in SCENARIOS:
            return {"error": f"unknown scenario '{next_scenario}'"}
        org_id = (prior.get("company_id") or "").strip()
        if not org_id:
            return {"error": "prior run has no company_id — cannot chain"}

        # Source text: prefer the digest; fall back to concatenated responses
        # so an in-room run (no digest yet) can still seed a chain.
        digest_text = ((prior.get("digest") or {}).get("text") or "").strip()
        if not digest_text:
            parts: list[str] = []
            for r in prior.get("responses") or []:
                resp = (r.get("response") or "").strip()
                if not resp:
                    continue
                header = f"### {r.get('name') or r.get('worker_id') or '?'} ({r.get('role') or ''})"
                parts.append(header + "\n" + resp)
            digest_text = "\n\n".join(parts).strip()
        if not digest_text:
            return {"error": "prior run has no digest or responses to chain on"}

        prior_scenario = prior.get("scenario") or "session"
        framing = (framing or "").strip() or self._CHAIN_FRAMINGS.get(
            next_scenario,
            "Continue from the prior {prior} session output below.",
        ).format(prior=prior_scenario)
        composed_prompt = (
            f"{framing}\n\n"
            f"--- PRIOR {prior_scenario.upper()} OUTPUT (run {prior['id']}) ---\n"
            f"{digest_text}\n"
            f"--- END ---"
        )

        record = await self.run_scenario(
            org_id=org_id,
            scenario_type=next_scenario,
            prompt=composed_prompt,
            mode=mode,
        )
        if isinstance(record, dict) and not record.get("error") and record.get("id"):
            record["parent_run_id"] = prior["id"]
            record["chain"] = {
                "parent_run_id": prior["id"],
                "parent_scenario": prior_scenario,
                "framing": framing,
            }
            save_run(self, record)
        return record

    async def _run_in_room(
        self, org: dict, members: list[dict], scenario_type: str, prompt: str,
    ) -> dict:
        for m in members:
            await self._push_member_to_rooms({
                "id": m.get("id"), "name": m.get("name") or "",
                "org_id": org.get("id") or "",
                "role": m.get("role") or "", "emoji": m.get("emoji") or "",
                "model": m.get("model") or "", "mode": "ai",
            })
        title = f"{org.get('name', org.get('id'))} — {scenario_type}"
        participants = [{"type": "agent", "id": m.get("id")} for m in members]
        try:
            room = await self.call_app(
                "rooms", "create_room",
                title=title,
                participants=participants,
                system_prompt=(
                    f"You are debating the following proposal in front of "
                    f"the team. Each of you responds from your own role.\n\n"
                    f"PROPOSAL:\n{prompt}"
                ),
            )
        except Exception as e:
            record = await SCENARIOS[scenario_type].run(
                self, org, members, prompt, "headless",
            )
            record["in_room_fallback"] = (
                f"rooms unavailable; ran headless. "
                f"{type(e).__name__}: {str(e)[:120]}"
            )
            await self._append_run_memory(org, members, record)
            return record
        if not isinstance(room, dict) or "id" not in room or room.get("error"):
            record = await SCENARIOS[scenario_type].run(
                self, org, members, prompt, "headless",
            )
            err = room.get("error") if isinstance(room, dict) else "unknown"
            record["in_room_fallback"] = f"rooms refused room creation ({err}); ran headless."
            await self._append_run_memory(org, members, record)
            return record
        record = {
            "id": room["id"],
            "scenario": scenario_type,
            "company_id": org.get("id"),
            "company_name": org.get("name"),
            "prompt": prompt,
            "mode": "in-room",
            "started": _now(),
            "completed": "",
            "room": room,
            "responses": [],
            "digest": {"text": "", "provenance": {}},
        }
        save_run(self, record)
        return record

    async def _append_run_memory(self, org: dict, members: list[dict], record: dict) -> None:
        scenario = record.get("scenario", "")
        oid = org.get("id")
        run_id = record.get("id", "")
        line = f"- {record.get('completed') or _now()} — {scenario} ({run_id}) — _{(record.get('prompt') or '')[:80]}_"
        for m in members:
            mid = m.get("id")
            if not mid or not oid:
                continue
            try:
                self.vault_append_section(_vault_rel_member(oid, mid), "Scenarios", line)
            except Exception:
                continue

    # ── Web routes ────────────────────────────────────────────────

    @web_route("GET", "/api/orgs")
    async def api_list_orgs(self, request):
        reality = request.query_params.get("reality", "")
        scope = request.query_params.get("scope", "")
        kind = request.query_params.get("kind", "")
        return {"orgs": await self.list_orgs(reality=reality, scope=scope, kind=kind)}

    @web_route("GET", "/api/orgs/{oid}")
    async def api_get_org(self, request):
        oid = request.path_params["oid"]
        org = await self.get_org(oid)
        if not org:
            return {"error": "not found"}
        return org

    @web_route("POST", "/api/orgs")
    async def api_add_org(self, request):
        data = await self.read_json(request)
        return await self.add_org(
            name=data.get("name", ""),
            kind=data.get("kind", "team"),
            reality=data.get("reality", "real"),
            scope=data.get("scope", "member"),
            mission=data.get("mission", ""),
            vision=data.get("vision", ""),
            values=data.get("values", ""),
            culture=data.get("culture", ""),
            roles=data.get("roles", ""),
            parent=data.get("parent", ""),
            company_id=data.get("company_id", ""),
        )

    @web_route("POST", "/api/orgs/{oid}/append")
    async def api_append_org_section(self, request):
        oid = request.path_params["oid"]
        data = await self.read_json(request)
        return await self.append_org_section(
            org_id=oid,
            section=data.get("section", ""),
            text=data.get("text", ""),
            prepend_date=data.get("prepend_date", True),
        )

    @web_route("DELETE", "/api/orgs/{oid}")
    async def api_archive_org(self, request):
        oid = request.path_params["oid"]
        return await self.archive_org(oid)

    @web_route("GET", "/api/orgs/{oid}/assets")
    async def api_list_assets(self, request):
        oid = request.path_params["oid"]
        return {"assets": await self.list_assets(oid)}

    @web_route("POST", "/api/orgs/{oid}/assets")
    async def api_add_asset(self, request):
        oid = request.path_params["oid"]
        data = await self.read_json(request)
        return await self.add_asset(oid, data.get("ref", ""))

    @web_route("DELETE", "/api/orgs/{oid}/assets")
    async def api_remove_asset(self, request):
        oid = request.path_params["oid"]
        data = await self.read_json(request)
        return await self.remove_asset(oid, data.get("ref", ""))

    @web_route("POST", "/api/members")
    async def api_add_member(self, request):
        data = await self.read_json(request)
        return await self.add_member(
            org_id=data.get("org_id", ""),
            mode=data.get("mode", "human"),
            person_id=data.get("person_id", ""),
            name=data.get("name", ""),
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model", ""),
            emoji=data.get("emoji", ""),
            role=data.get("role", ""),
            dept=data.get("dept", ""),
            reports_to=data.get("reports_to", ""),
            joined=data.get("joined", ""),
        )

    @web_route("DELETE", "/api/members/{mid}")
    async def api_remove_member(self, request):
        mid = request.path_params["mid"]
        return await self.remove_member(mid)

    @web_route("GET", "/api/memberships")
    async def api_list_memberships(self, request):
        """Cross-app endpoint — kept under /memberships (not /members) since
        it's also used by `apps/people/` person detail. Behaviour: returns
        member rows decorated with `org_name` and `org_kind` when filtering
        by `person_id`, so the people app doesn't need to round-trip."""
        org_id = request.query_params.get("org_id", "")
        person_id = request.query_params.get("person_id", "")
        mode = request.query_params.get("mode", "")
        rows = await self.list_members(org_id=org_id, person_id=person_id, mode=mode)
        if person_id:
            orgs = {o["id"]: o for o in await self.list_orgs()}
            for m in rows:
                oid = m.get("org_id") or ""
                o = orgs.get(oid) or {}
                m["org_name"] = o.get("name") or oid
                m["org_kind"] = o.get("kind") or ""
        return {"memberships": rows}

    @web_route("POST", "/api/set-field")
    async def api_set_field(self, request):
        data = await self.read_json(request)
        return await self.set_field(
            id=data.get("id", ""),
            field=data.get("field", ""),
            value=data.get("value"),
        )

    @web_route("GET", "/api/scenarios")
    async def api_list_scenarios(self, request):
        return {
            "scenarios": [
                {"id": k, **v} for k, v in SCENARIO_META.items()
            ],
        }

    @web_route("POST", "/api/scenario/run")
    async def api_run_scenario(self, request):
        data = await self.read_json(request)
        # Accept either org_id (new) or company_id (legacy clients) — the
        # scenario shim is org-shaped internally.
        org_id = data.get("org_id") or data.get("company_id", "")
        return await self.run_scenario(
            org_id=org_id,
            scenario_type=data.get("scenario_type", ""),
            prompt=data.get("prompt", ""),
            mode=data.get("mode", "headless"),
        )

    @web_route("POST", "/api/scenario/chain")
    async def api_chain_scenario(self, request):
        """Run `next_scenario` against the same org as `prior_run_id`,
        feeding the prior run's digest into the new prompt. Result carries
        `parent_run_id` so runs form a chain (workshop → critique → revised
        workshop is the canonical loop)."""
        data = await self.read_json(request)
        return await self.chain_scenario(
            prior_run_id=data.get("prior_run_id", ""),
            next_scenario=data.get("next_scenario") or data.get("scenario_type", ""),
            framing=data.get("framing", ""),
            mode=data.get("mode", "headless"),
        )

    @web_route("GET", "/api/runs")
    async def api_list_runs(self, request):
        # Run records still carry `company_id` for back-compat with scenarios.base.
        cid = request.query_params.get("org_id") or request.query_params.get("company_id") or None
        return {"runs": list_runs(self, company_id=cid)}

    @web_route("GET", "/api/runs/{run_id}")
    async def api_get_run(self, request):
        rid = request.path_params["run_id"]
        record = load_run(self, rid)
        if not record:
            return {"error": "not found"}
        if record.get("scenario") == "workshop":
            record["pending"] = workshop_mod.list_pending(self, run_id=rid)
        return record

    @web_route("POST", "/api/pending/{action_id}/apply")
    async def api_apply_pending(self, request):
        aid = request.path_params["action_id"]
        return await workshop_mod.apply_pending(self, aid)

    @web_route("POST", "/api/pending/{action_id}/reject")
    async def api_reject_pending(self, request):
        aid = request.path_params["action_id"]
        return await workshop_mod.reject_pending(self, aid)

    # ── CLI ───────────────────────────────────────────────────────

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
