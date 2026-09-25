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
from emptyos.sdk.utils import csv_to_rows, now_iso, read_upload_text, safe_path_segment, sniff_columns

log = logging.getLogger("emptyos.requirements")

TAG = "requirement"
DEFAULT_PROJECT = "inbox"
STATUSES = ("proposed", "approved", "verified", "superseded")
PRIORITIES = ("must", "should", "could")

# CSV import (requirements-no-import): best-effort header -> canonical field
# map. A header is claimed by the first field whose alias it contains, so
# "statement" is checked before "title" — a "Description" column becomes the
# richer statement field, not the shorter title.
MAX_IMPORT_BYTES = 2 * 1024 * 1024  # a requirements spreadsheet is never 2 MB
MAX_IMPORT_ROWS = 500  # a sane ceiling on one import
_IMPORT_FIELD_ALIASES: dict[str, list[str]] = {
    "statement": ["requirement statement", "statement", "requirement text",
                  "description", "requirement", "text"],
    "title": ["title", "name", "summary"],
    "project": ["project"],
    "priority": ["priority", "moscow"],
    "references": ["references", "traces to", "standard", "clause", "reference"],
}

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

# Suspect-link flagging (requirements-no-suspect-link): a verified requirement
# whose note has been edited SINCE last_verified is "suspect" — the same
# concept as IBM DOORS Next's suspect link, computed here from the vault
# note's real file mtime rather than an app-tracked `updated` field, since a
# requirement note is meant to be hand-editable (a Statement edit made
# directly in the vault never touches `updated`, only the file's own mtime).
# A grace window absorbs the write-ordering race inside verify() itself,
# where `last_verified` (seconds precision) and the file's actual write
# (sub-second mtime) land in the same call but can round to different whole
# seconds.
_SUSPECT_GRACE_S = 5


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

    @staticmethod
    def _is_suspect(note: dict, status: str, last_verified: str) -> bool:
        if status != "verified" or not last_verified:
            return False
        modified = note.get("modified")
        if modified is None:
            return False
        try:
            from datetime import datetime

            lv_epoch = datetime.fromisoformat(last_verified).timestamp()
        except ValueError:
            return False
        return modified > lv_epoch + _SUSPECT_GRACE_S

    def _row(self, note: dict) -> dict:
        p = note.get("properties", {}) or {}
        project = str(p.get("project", "") or "")
        req_id = str(p.get("req_id", "") or "")
        status = str(p.get("status", "") or "proposed")
        last_verified = str(p.get("last_verified", "") or "")
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
            "suspect": self._is_suspect(note, status, last_verified),
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

    # ── RTM export ─────────────────────────────────────────────────────
    async def _rtm_rows(self, project: str = "") -> list[dict]:
        """Requirement -> standard clause -> verification evidence, one row
        per requirement — the report a client/regulatory reviewer asks for.
        No new data: joins list_all()'s rows with the same kb.resolve_reference
        lookup api_get() already does per-requirement."""
        rows = await self.list_all()
        if project:
            rows = [r for r in rows if r["project"] == project]
        out = []
        for r in rows:
            note = self._find(r["id"])
            props = (note or {}).get("properties", {}) or {}
            resolved = await self._resolve_refs(self._refs_list(props))
            clauses = ", ".join(
                (x["slug"] or x["reference"]) for x in resolved
            ) if resolved else ""
            out.append({
                "req_id": r["req_id"],
                "project": r["project"],
                "title": r["title"],
                "priority": r["priority"],
                "standard_clause": clauses,
                "status": r["status"],
                "verified_by": r["verified_by"],
                "last_verified": str(props.get("last_verified", "") or ""),
            })
        return out

    @web_route("GET", "/api/export/rtm")
    async def api_export_rtm(self, request):
        """Requirements Traceability Matrix — the single artifact a
        client/regulatory reviewer actually asks for. Flagged in gap
        analysis (requirements-no-rtm-export): a report view over data
        already computed elsewhere in this app, not new data.

        ``?project=<id>`` scopes to one project (default: all).
        ``?format=csv`` returns CSV (``rows_to_csv``); default is a
        markdown table (``format_markdown_table``), per the text-first-data
        convention (.claude/rules/text-first-data.md).
        """
        from emptyos.sdk.utils import format_markdown_table, rows_to_csv

        project = (request.query_params.get("project") or "").strip()
        fmt = (request.query_params.get("format") or "md").strip().lower()
        rows = await self._rtm_rows(project)
        columns = [
            "req_id", "project", "title", "priority",
            "standard_clause", "status", "verified_by", "last_verified",
        ]
        if fmt == "csv":
            from starlette.responses import Response

            return Response(
                rows_to_csv(rows, columns),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="rtm.csv"'},
            )
        title = f"# Requirements Traceability Matrix — {project or 'all projects'}\n\n"
        body = title + (format_markdown_table(rows, columns) if rows else "_No requirements found._")
        return {"markdown": body, "rows": rows}

    # ── CSV import ─────────────────────────────────────────────────────
    def _parse_import_csv(self, csv_text: str) -> dict:
        """Parse a requirements spreadsheet -> {rows, columns, mapping,
        warnings, skipped}. Each output row: {statement, title, project,
        priority, references}. A row with no statement is dropped (counted
        in `skipped`) — there's nothing to import without one. Priority and
        project are passed through unvalidated; `add()` already falls back
        to sane defaults for both, so this stays a thin pass-through."""
        raw_rows = csv_to_rows(csv_text)
        if not raw_rows:
            return {"rows": [], "columns": [], "mapping": {},
                    "warnings": ["no rows found in file"], "skipped": 0}

        columns = list(raw_rows[0].keys())
        mapping = sniff_columns(columns, _IMPORT_FIELD_ALIASES)
        warnings = []
        if "statement" not in mapping:
            warnings.append("could not find a requirement-statement column — every row will be skipped")

        out_rows = []
        skipped = 0
        for raw in raw_rows:
            statement = str(raw.get(mapping.get("statement", ""), "") or "").strip()
            if not statement:
                skipped += 1
                continue
            title = str(raw.get(mapping.get("title", ""), "") or "").strip()
            project = str(raw.get(mapping.get("project", ""), "") or "").strip() or DEFAULT_PROJECT
            priority = str(raw.get(mapping.get("priority", ""), "") or "").strip().lower()
            references = str(raw.get(mapping.get("references", ""), "") or "").strip()
            out_rows.append({
                "statement": statement,
                "title": title or statement[:80],
                "project": project,
                "priority": priority,
                "references": references,
            })
        if len(out_rows) > MAX_IMPORT_ROWS:
            warnings.append(f"file has {len(out_rows)} rows — only the first {MAX_IMPORT_ROWS} will be imported")
            out_rows = out_rows[:MAX_IMPORT_ROWS]
        return {"rows": out_rows, "columns": columns, "mapping": mapping,
                "warnings": warnings, "skipped": skipped}

    async def _existing_titles_by_project(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for row in await self.list_all():
            out.setdefault(row["project"], set()).add(row["title"].strip().casefold())
        return out

    @web_route("POST", "/api/import/preview")
    async def api_import_preview(self, request):
        """Parse a requirements spreadsheet and flag rows already present in
        the target project (by normalised title). Writes nothing.

        Flagged in gap analysis (requirements-no-import): onboarding an
        existing project's requirements from a client spreadsheet or
        another tool's export meant re-typing every row by hand. Follows
        the same propose->preview->confirm shape as expense's statement
        import (`.claude/rules/proposed-action.md`, impact-shaped) —
        csv_to_rows, then the reviewed batch is handed to `add()`, the
        existing locked, event-emitting write primitive.

        Returns {rows, columns, mapping, summary, warnings} where each row
        is {statement, title, project, priority, references, duplicate}.
        """
        csv_text, err = await read_upload_text(request, max_bytes=MAX_IMPORT_BYTES)
        if err:
            return {"error": err}
        parsed = self._parse_import_csv(csv_text)
        rows = parsed["rows"]
        if not rows:
            return {"error": "no importable requirements found", "warnings": parsed["warnings"],
                    "columns": parsed["columns"], "mapping": parsed["mapping"]}

        existing_titles = await self._existing_titles_by_project()
        out_rows = []
        new_count = 0
        for r in rows:
            duplicate = r["title"].casefold() in existing_titles.get(r["project"], set())
            if not duplicate:
                new_count += 1
            out_rows.append({**r, "duplicate": duplicate})

        return {
            "rows": out_rows,
            "columns": parsed["columns"],
            "mapping": parsed["mapping"],
            "warnings": parsed["warnings"],
            "summary": {
                "total": len(out_rows),
                "new": new_count,
                "duplicate": len(out_rows) - new_count,
                "skipped": parsed["skipped"],
                "projects": sorted({r["project"] for r in out_rows}),
            },
        }

    @web_route("POST", "/api/import/confirm")
    async def api_import_confirm(self, request):
        """Write the reviewed rows through the existing `add()` verb. Body:
        {rows: [{statement, title, project, priority, references}]}.
        Re-checks duplicates against the current vault (the source of
        truth may have grown since preview), so a stale confirm can't
        double-import.
        """
        body = await request.json()
        rows = body.get("rows") or []
        if not isinstance(rows, list) or not rows:
            return {"error": "rows required"}
        if len(rows) > MAX_IMPORT_ROWS:
            return {"error": "too many rows"}

        existing_titles = await self._existing_titles_by_project()
        imported = 0
        skipped = 0
        seen: dict[str, set[str]] = {}
        for r in rows:
            if not isinstance(r, dict):
                skipped += 1
                continue
            statement = str(r.get("statement", "")).strip()
            if not statement:
                skipped += 1
                continue
            project = str(r.get("project", "")).strip() or DEFAULT_PROJECT
            title = str(r.get("title", "")).strip() or statement[:80]
            key = title.casefold()
            if key in existing_titles.get(project, set()) or key in seen.get(project, set()):
                skipped += 1
                continue
            seen.setdefault(project, set()).add(key)
            priority = str(r.get("priority", "")).strip()
            references = str(r.get("references", "")).strip()
            await self.add(statement=statement, project=project, title=title,
                           priority=priority, references=references)
            imported += 1

        return {"ok": True, "imported": imported, "skipped": skipped}

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
