"""Requirements — capture, trace-to-standard, verify, and track requirements for any project.

Requirements are project-scoped vault notes (tag ``requirement``) stored under
``10_Projects/{project}/requirements/REQ-nnn.md``. They trace *into* the KB via
the existing ``references:`` citation mechanism (resolved through
``kb.resolve_reference``) — they are NOT KB notes: KB is global reusable
knowledge, a requirement is project-scoped, stateful, and lifecycle-bearing.

Wires existing primitives, does not rebuild them:
  - traceability  → ``call_app("kb", "resolve_reference")`` (fail-soft)
  - verification  → ``call_app(<app>, "run_conformance")`` on demand (no
                    persisted result store exists)
  - surface       → boards contribution + the custom page
  - impact query  → computed here (KB's reverse index only covers kb-tagged notes)
"""

from __future__ import annotations

import logging
import re

from emptyos.sdk import BaseApp, cli_command, web_route
from emptyos.sdk.utils import now_iso, safe_path_segment

log = logging.getLogger("emptyos.requirements")

TAG = "requirement"
DEFAULT_PROJECT = "inbox"
STATUSES = ("proposed", "approved", "verified", "superseded")
PRIORITIES = ("must", "should", "could")

# proposed/approved/verified/superseded → shared STATUS_VARIANTS whitelist
# (draft/active/pass/archived) so the badge colours render across all themes.
STATUS_COLOR_MAP = {
    "proposed": "draft",
    "approved": "active",
    "verified": "pass",
    "superseded": "archived",
}

# verified_by = "conformance:<app>/<endpoint>/<case_id>" runs a real conformance
# case; anything else is manual evidence text the human verifies by hand.
_CONFORMANCE_RE = re.compile(r"^conformance:([^/]+)/([^/]+)/(.+)$")
_REQ_ID_RE = re.compile(r"REQ-(\d+)", re.IGNORECASE)


class RequirementsApp(BaseApp):
    # Fields boards / cross-app callers may flip without domain workflow.
    SETTABLE_FIELDS = {"status", "priority", "title", "verified_by"}

    async def on_start(self):
        log.info("requirements started")

    # ── path helpers ───────────────────────────────────────────────────
    def _projects_base(self) -> str:
        # Read the *projects* app's configured dir (its own vault-map section),
        # so a machine that relocated projects_dir is honoured — requirements
        # store under the same 10_Projects tree projects owns, not a hardcoded one.
        vm = getattr(self.kernel, "vault_map", None)
        rel = (vm.get("projects", "projects_dir", "10_Projects") if vm else "10_Projects")
        return (rel or "10_Projects").strip("/")

    def _req_dir(self, project: str) -> str:
        return f"{self._projects_base()}/{safe_path_segment(project)}/requirements"

    def _req_path(self, project: str, req_id: str) -> str:
        return f"{self._req_dir(project)}/{safe_path_segment(req_id)}.md"

    def _next_req_id(self, project: str) -> str:
        mx = 0
        for note in self.vault_query(tags=[TAG], project=project):
            m = _REQ_ID_RE.match(str((note.get("properties") or {}).get("req_id", "")))
            if m:
                mx = max(mx, int(m.group(1)))
        return f"REQ-{mx + 1:03d}"

    # ── row shaping ────────────────────────────────────────────────────
    @staticmethod
    def _refs_list(props: dict) -> list[str]:
        refs = props.get("references")
        if isinstance(refs, (list, tuple)):
            return [str(r).strip() for r in refs if str(r).strip()]
        if isinstance(refs, str) and refs.strip():
            return [r.strip() for r in refs.split(",") if r.strip()]
        return []

    def _row(self, note: dict) -> dict:
        p = note.get("properties", {}) or {}
        project = str(p.get("project", "") or "")
        req_id = str(p.get("req_id", "") or "")
        status = str(p.get("status", "") or "proposed")
        return {
            "id": f"{project}~{req_id}",
            "req_id": req_id,
            "project": project,
            "title": str(p.get("title", "") or note.get("name", "")),
            "status": status,
            "priority": str(p.get("priority", "") or ""),
            "verified": "verified" if status == "verified" else "unverified",
            "references": ", ".join(self._refs_list(p)),
            "verified_by": str(p.get("verified_by", "") or ""),
        }

    def _find(self, rid: str) -> dict | None:
        """rid = '{project}~{req_id}' → the matching requirement note (or None)."""
        project, _, req_id = (rid or "").partition("~")
        if not req_id:
            return None
        rows = self.vault_query(tags=[TAG], project=project, req_id=req_id)
        return rows[0] if rows else None

    # ── boards / list surface ──────────────────────────────────────────
    async def list_all(self) -> list[dict]:
        """Every requirement as a board/list row, sorted by project then id."""
        rows = [self._row(n) for n in self.vault_query(tags=[TAG])]
        rows.sort(key=lambda r: (r["project"].lower(), r["req_id"]))
        return rows

    @web_route("GET", "/api/items")
    async def api_items(self, request):
        project = (request.query_params.get("project") or "").strip()
        status = (request.query_params.get("status") or "").strip()
        rows = await self.list_all()
        if project:
            rows = [r for r in rows if r["project"] == project]
        if status:
            rows = [r for r in rows if r["status"] == status]
        projects = sorted({r["project"] for r in await self.list_all() if r["project"]})
        return {"items": rows, "projects": projects, "statuses": list(STATUSES),
                "priorities": list(PRIORITIES)}

    @web_route("GET", "/api/requirements/{rid}")
    async def api_get(self, request):
        rid = request.path_params.get("rid", "")
        note = self._find(rid)
        if not note:
            return {"error": "requirement not found"}
        p = note.get("properties", {}) or {}
        refs = self._refs_list(p)
        return {
            **self._row(note),
            "statement": self.vault_read_section(note["path"], "Statement") or "",
            "rationale": self.vault_read_section(note["path"], "Rationale") or "",
            "created": str(p.get("created", "") or ""),
            "updated": str(p.get("updated", "") or ""),
            "last_verified": str(p.get("last_verified", "") or ""),
            "resolved_references": await self._resolve_refs(refs),
            "_vault_path": note["path"],
        }

    # ── create ─────────────────────────────────────────────────────────
    async def add(
        self,
        statement: str = "",
        project: str = DEFAULT_PROJECT,
        title: str = "",
        priority: str = "should",
        references: str = "",
    ) -> dict:
        statement = (statement or "").strip()
        if not statement:
            return {"error": "statement is required"}
        project = (project or DEFAULT_PROJECT).strip() or DEFAULT_PROJECT
        priority = priority if priority in PRIORITIES else "should"
        title = (title or statement[:80]).strip()
        refs = [r.strip() for r in (references or "").split(",") if r.strip()]

        req_id = self._next_req_id(project)
        path = self._req_path(project, req_id)
        ts = now_iso()
        fm = {
            "tags": [TAG],
            "req_id": req_id,
            "project": project,
            "title": title,
            "status": "proposed",
            "priority": priority,
            "references": refs,
            "verified_by": "",
            "created": ts,
            "updated": ts,
        }
        body = f"## Statement\n\n{statement}\n\n## Rationale\n\n"
        async with self.note_lock(path):
            self.vault_create_note(path, fm, body)
        row = self._row({"properties": fm, "name": title, "path": path})
        await self.emit("requirements:created",
                        {"id": row["id"], "project": project, "req_id": req_id})
        return {**row, "_vault_path": path}

    @web_route("POST", "/api/items")
    async def api_add(self, request):
        body = await request.json()
        return await self.add(
            statement=body.get("statement", "") or body.get("text", ""),
            project=body.get("project", DEFAULT_PROJECT),
            title=body.get("title", ""),
            priority=body.get("priority", "should"),
            references=body.get("references", ""),
        )

    # ── mutate ─────────────────────────────────────────────────────────
    async def set_field(self, id: str, field: str, value) -> dict:
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        note = self._find(id)
        if not note:
            return {"error": "requirement not found"}
        if field == "status" and value not in STATUSES:
            return {"error": f"invalid status '{value}'"}
        if field == "priority" and value not in PRIORITIES:
            return {"error": f"invalid priority '{value}'"}
        path = note["path"]
        async with self.note_lock(path):
            self.vault_update(path, {field: value, "updated": now_iso()})
        await self.emit("requirements:updated",
                        {"id": id, "field": field, "value": value})
        return {"ok": True}

    @web_route("POST", "/api/requirements/{rid}/field")
    async def api_set_field(self, request):
        rid = request.path_params.get("rid", "")
        body = await request.json()
        return await self.set_field(rid, body.get("field", ""), body.get("value"))

    # ── verify ─────────────────────────────────────────────────────────
    async def verify(self, id: str) -> dict:
        """Verify a requirement. A conformance-shaped `verified_by` runs the case
        live and flips status to verified on pass; anything else is manual."""
        note = self._find(id)
        if not note:
            return {"error": "requirement not found"}
        vb = str((note.get("properties") or {}).get("verified_by", "") or "").strip()
        m = _CONFORMANCE_RE.match(vb)
        if not m:
            return {"ok": False, "mode": "manual",
                    "message": "No conformance case linked — set status by hand once "
                               "you have evidence (verified_by = "
                               "'conformance:<app>/<endpoint>/<case_id>' to auto-verify)."}
        app_id, endpoint, case_id = m.group(1), m.group(2), m.group(3)
        try:
            result = await self.call_app(app_id, "run_conformance",
                                         endpoint=endpoint, case_id=case_id)
        except Exception as e:  # noqa: BLE001 — fail-soft, the app may be absent
            return {"ok": False, "mode": "conformance", "error": str(e)}
        passed = bool(isinstance(result, dict) and result.get("passed"))
        if passed:
            path = note["path"]
            async with self.note_lock(path):
                self.vault_update(path, {"status": "verified",
                                         "last_verified": now_iso(),
                                         "updated": now_iso()})
            await self.emit("requirements:verified",
                            {"id": id, "case": f"{app_id}/{endpoint}/{case_id}"})
        return {"ok": passed, "mode": "conformance", "passed": passed,
                "case": f"{app_id}/{endpoint}/{case_id}", "result": result}

    @web_route("POST", "/api/requirements/{rid}/verify")
    async def api_verify(self, request):
        return await self.verify(request.path_params.get("rid", ""))

    # ── impact (reverse citation lookup) ───────────────────────────────
    async def impact(self, reference: str = "") -> dict:
        """Which requirements trace to `reference` (a citation string or clause slug).

        Computed here because KB's reverse citation index only covers kb-tagged
        notes; a requirement note is not kb-tagged, so it never appears there.
        """
        reference = (reference or "").strip()
        if not reference:
            return {"reference": reference, "slug": None, "requirements": []}
        target_slug = reference
        resolved = await self._resolve_one(reference)
        if resolved.get("matched"):
            target_slug = resolved["slug"]
        hits = []
        for note in self.vault_query(tags=[TAG]):
            row = self._row(note)
            for ref in self._refs_list(note.get("properties", {}) or {}):
                r = await self._resolve_one(ref)
                if (r.get("matched") and r["slug"] == target_slug) or ref == reference:
                    hits.append(row)
                    break
        return {"reference": reference, "slug": target_slug, "requirements": hits}

    @web_route("GET", "/api/impact")
    async def api_impact(self, request):
        return await self.impact(request.query_params.get("reference", ""))

    # ── kb citation resolution (fail-soft) ─────────────────────────────
    async def _resolve_one(self, reference: str) -> dict:
        try:
            r = await self.call_app("kb", "resolve_reference", reference=reference)
            return r if isinstance(r, dict) else {"matched": False}
        except Exception:  # noqa: BLE001 — kb optional; degrade to unresolved
            return {"matched": False, "slug": None, "reason": "kb-unavailable"}

    async def _resolve_refs(self, refs: list[str]) -> list[dict]:
        out = []
        for ref in refs:
            r = await self._resolve_one(ref)
            out.append({
                "reference": ref,
                "matched": bool(r.get("matched")),
                "slug": r.get("slug"),
            })
        return out

    # ── timeline entity_source ─────────────────────────────────────────
    async def requirement_path(self, id: str = "") -> str:
        note = self._find(id)
        return note["path"] if note else ""

    # ── boards contribution ────────────────────────────────────────────
    def board_presets(self):
        return [{
            "id": "requirements-all",
            "name": "Requirements",
            "description": "Every requirement across projects. Edited in the "
                           "Requirements app — this is a read-only view.",
            "source": {"type": "app", "app": "requirements", "method": "list_all"},
            "columns": [
                {"id": "req_id", "label": "ID", "type": "text"},
                {"id": "title", "label": "Requirement", "type": "text"},
                {"id": "project", "label": "Project", "type": "text"},
                {"id": "status", "label": "Status", "type": "select",
                 "options": list(STATUSES),
                 "color_map": {"proposed": "blue", "approved": "amber",
                               "verified": "green", "superseded": "gray"}},
                {"id": "priority", "label": "Priority", "type": "select",
                 "options": list(PRIORITIES)},
            ],
            "views": [
                {"type": "table", "default": True},
                {"type": "kanban", "group_by": "status"},
            ],
            "kanban_group_by": "status",
        }]

    # ── CLI ────────────────────────────────────────────────────────────
    @cli_command("list")
    async def cli_list(self):
        for r in await self.list_all():
            print(f"{r['project']}/{r['req_id']}  [{r['status']}]  {r['title']}")
