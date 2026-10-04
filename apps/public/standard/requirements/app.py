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

from emptyos.sdk import BaseApp, cli_command, parse_llm_json, web_route
from emptyos.sdk.utils import csv_to_rows, now_iso, read_upload_text, safe_path_segment, sniff_columns

from .quality import lint_statement

log = logging.getLogger("emptyos.requirements")

TAG = "requirement"
# The tag kb gives a project's client documents (kb/shared.py SOURCE_TAG; apps
# never import each other). Read here only to list projects that have one.
SOURCE_TAG = "kb-source"
DEFAULT_PROJECT = "inbox"
STATUSES = ("proposed", "approved", "verified", "superseded")
PRIORITIES = ("must", "should", "could")
# Compliance disposition — the column a client's tender reviewer reads. It is
# the engineer's declared position against the clause; `status` is the
# lifecycle of the requirement record. Both are set by a human; extraction
# never fills disposition.
DISPOSITIONS = ("comply", "partial", "deviate", "not-applicable")

# Requirement extraction from a source's clause notes (a client spec or a
# standard registered in kb). Clause bodies are batched into one think() call
# per chunk; the reply is one row per obligation, each pinned to the clause it
# came from so the requirement traces back through `references:`.
# 3000 chars ≈ 750 tokens of clause text per call: with the ~700-token system
# prompt and a reply of the same order it stays inside a 4k-context local model
# AND a fallback provider can answer the whole chunk — a 6000-char chunk let a
# local model return a truncated list that read as real rows. Measured
# 2026-10-01 on the mock spec: ~8 s per chunk on claude-cli. The clause cap
# bounds one preview (and the claude-cli semaphore it holds, one chunk at a
# time) — extraction awaits in the route today; a background job with progress
# is deferred (docs/DEFERRED-WORK.md).
_EXTRACT_CHUNK_CHARS = 3000
_MAX_EXTRACT_CLAUSES = 60
# Per-call budget. The think chain's global default (30 s) killed claude-cli
# mid-reply on an AS 2067:2016 chunk and the chain fell through to ollama, which
# returned empty content (2026-10-01). 180 s is a ceiling (~20× the measured
# chunk), not a target. dev-gotchas § "A tool-using claude-cli think() inherits
# the chain's global timeout" also says to pin the provider; this call does NOT
# pin, on purpose: a hosted or local-only deployment has no claude-cli, and the
# user pins through the model pill (`think.app.requirements`) when they want
# one provider to read a confidential document.
_EXTRACT_THINK_TIMEOUT_S = 180
_CLAUSE_PREFIX_RE = re.compile(r"^\s*(?:§|¶|clause|cl\.?|section|sec\.?)\s*", re.IGNORECASE)
_CLAUSE_NUM_RE = re.compile(r"(\d+(?:\.\d+)*)")
EXTRACT_REQUIREMENTS_SYSTEM = """You extract requirements from numbered clauses of an engineering \
specification or standard. You transcribe obligations — you do not invent, merge, \
interpret or complete them.

Return ONLY a JSON array (no prose, no code fence). One element per obligation:
{"clause": "3.4", "statement": "The Contractor shall submit the earthing design for review \
before installation.", "title": "Earthing design submitted for review"}

Valid example for two clauses where the second has no obligation:
[{"clause": "3.4", "statement": "The Contractor shall submit the earthing design for review \
before installation.", "title": "Earthing design submitted for review"},
 {"clause": "3.4", "statement": "Cable trenches must be backfilled with thermally stabilised \
sand.", "title": "Trench backfill material"}]

Rules:
- An obligation is a sentence carrying shall / must / should / is required / is not permitted, \
or an imperative addressed to the obligated party (the Contractor, the Supplier, the Designer). \
A "should" sentence is a recommendation-level obligation and IS extracted. A "may" sentence \
grants permission and is extracted only when it constrains a party (e.g. "may only ...").
- "will" states a fact or the OTHER party's intent ("The Principal will provide access") — \
that is not the obligated party's requirement. Emit nothing for it.
- "statement" is the obligation sentence VERBATIM from the clause text. Do not paraphrase, \
do not shorten, do not fix grammar, do not translate.
- "clause" is the clause number the sentence sits in, exactly as given in the input header.
- "title" is a short noun phrase (under 80 characters) naming the obligation.
- One element per obligation sentence. Never merge two sentences into one element; never \
split one sentence into two.
- Definitions, scope statements, notes, informative text and headings are NOT obligations. \
Emit nothing for them.
- A clause with no obligation contributes no element. An input with no obligations returns [].
- Output strictly the JSON array and nothing else."""

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


def _infer_priority(statement: str) -> str:
    """MoSCoW priority from the obligation's own verb — deterministic, never the LLM's guess.

    shall / must / required / not permitted → must; should → should;
    may / could → could. Unknown wording → should (the app's default).
    "will" is deliberately absent: in requirements writing (ISO/IEC/IEEE 29148
    vocabulary) it states a fact or the other party's intent, not an
    obligation, so it must not be promoted to `must`; "can" states possibility.
    """
    s = (statement or "").lower()
    if re.search(r"\b(shall|must|is required|are required|not permitted|prohibited)\b", s):
        return "must"
    if re.search(r"\bshould\b", s):
        return "should"
    if re.search(r"\b(may|could|optionally)\b", s):
        return "could"
    return "should"


def _norm_clause_no(raw) -> str:
    """The model's clause label → the bare number the source keys on.

    ``"Clause 2.1"``, ``"§2.1"``, ``"2.1."``, ``"3.4 (a)"`` → ``"2.1"`` / ``"3.4"``.
    ``""`` when no number is present.
    """
    s = _CLAUSE_PREFIX_RE.sub("", str(raw or ""))
    m = _CLAUSE_NUM_RE.search(s)
    return m.group(1) if m else ""


def _coerce_references(value) -> list[str]:
    """Citations → a clean list. Accepts a list, a newline-separated string (the
    detail editor: one citation per line, so "AS/NZS 3008.1.1:2017, Table 3"
    survives a round trip whole), or a single-line comma-separated string (the
    add dialog, as _refs_list reads it). Anything else is dropped."""
    if isinstance(value, str):
        parts = value.splitlines() if "\n" in value else value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = [str(v) for v in value if isinstance(v, (str, int, float))]
    else:
        return []
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


def _coerce_evidence(value) -> list[str]:
    """Evidence pointers (vault paths / URLs) → a clean list. Accepts a list or a
    newline-separated string (one per line — a URL may carry commas);
    normalised once at the write boundary. Anything else is dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.splitlines()
    elif isinstance(value, (list, tuple)):
        parts = [str(v) for v in value if isinstance(v, (str, int, float))]
    else:
        return []
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


class RequirementsApp(BaseApp):
    # Fields boards / cross-app callers may flip without domain workflow.
    SETTABLE_FIELDS = {"status", "priority", "title", "verified_by", "disposition", "evidence",
                       "references"}

    async def on_start(self):
        log.info("requirements started")

    # ── path helpers ───────────────────────────────────────────────────
    def _req_dir(self, project: str) -> str:
        # Under the same tree the projects app owns (vault-map `projects_dir`),
        # never a hardcoded 10_Projects.
        return f"{self.vault_projects_root()}/{safe_path_segment(project)}/requirements"

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
            "disposition": str(p.get("disposition", "") or ""),
            "source_clause": str(p.get("source_clause", "") or ""),
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
        projects = {r["project"] for r in await self.list_all() if r["project"]}
        # A project's first client document arrives before its first
        # requirement; list it too, or its compliance matrix is unreachable.
        for note in self.vault_query(tags=[SOURCE_TAG]) or []:
            p = str((note.get("properties") or {}).get("project") or "").strip()
            if p:
                projects.add(p)
        projects = sorted(projects)
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
        row = self._row(note)
        statement = self.vault_read_section(note["path"], "Statement") or ""
        return {
            **row,
            "statement": statement,
            "quality": lint_statement(statement, row["priority"]),
            "rationale": self.vault_read_section(note["path"], "Rationale") or "",
            "created": str(p.get("created", "") or ""),
            "updated": str(p.get("updated", "") or ""),
            "last_verified": str(p.get("last_verified", "") or ""),
            "evidence": _coerce_evidence(p.get("evidence")),
            "dispositions": list(DISPOSITIONS),
            "resolved_references": await self._resolve_refs(refs, row["project"]),
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
        source_clause: str = "",
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
            "disposition": "",
            "evidence": [],
            # The kb clause note this row was extracted from ('' when typed by hand).
            "source_clause": (source_clause or "").strip(),
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
            source_clause=body.get("source_clause", ""),
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
        if field == "disposition" and value not in DISPOSITIONS and value not in ("", None):
            return {"error": f"invalid disposition '{value}'"}
        if field == "evidence":
            value = _coerce_evidence(value)
        if field == "references":
            value = _coerce_references(value)
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
    async def impact(self, reference: str = "", project: str = "") -> dict:
        """Which requirements trace to `reference` (a citation string or clause slug).
        ``project`` lets a client-document citation resolve inside that project.

        Computed here because KB's reverse citation index only covers kb-tagged
        notes; a requirement note is not kb-tagged, so it never appears there.
        """
        reference = (reference or "").strip()
        project = (project or "").strip()
        if not reference:
            return {"reference": reference, "slug": None, "requirements": []}
        target_slug = reference
        resolved = await self._resolve_one(reference, project)
        if resolved.get("matched"):
            target_slug = resolved["slug"]
        hits = []
        for note in self.vault_query(tags=[TAG]):
            row = self._row(note)
            for ref in self._refs_list(note.get("properties", {}) or {}):
                r = await self._resolve_one(ref, row["project"])
                if (r.get("matched") and r["slug"] == target_slug) or ref == reference:
                    hits.append(row)
                    break
        return {"reference": reference, "slug": target_slug, "requirements": hits}

    # ── wording checks (quality.py) ────────────────────────────────────
    async def quality_report(self, project: str = "") -> dict:
        """Every requirement whose statement draws a wording finding. Reads each
        statement, so it is its own call — the list view never pays for it."""
        import asyncio

        # One query, each note read from its own path — never re-found by id,
        # which costs a full index scan per row and maps duplicate ids to one note.
        checked, flagged = 0, []
        for note in self.vault_query(tags=[TAG]) or []:
            r = self._row(note)
            if project and r["project"] != project:
                continue
            if r["status"] == "superseded":      # retired: its wording no longer matters
                continue
            # A file read per requirement: off the event loop, so a big project
            # cannot stall the daemon (.claude/rules/debugging.md).
            text = await asyncio.to_thread(self.vault_read_section, note["path"], "Statement")
            checked += 1
            findings = lint_statement(text or "", r["priority"])
            if findings:
                flagged.append({"id": r["id"], "req_id": r["req_id"], "project": r["project"],
                                "title": r["title"], "findings": findings})
        flagged.sort(key=lambda x: (x["project"].lower(), x["req_id"]))
        return {"checked": checked, "flagged": flagged}

    @web_route("GET", "/api/quality")
    async def api_quality(self, request):
        return await self.quality_report((request.query_params.get("project") or "").strip())

    @web_route("GET", "/api/impact")
    async def api_impact(self, request):
        return await self.impact(request.query_params.get("reference", ""),
                                 request.query_params.get("project", ""))

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
            resolved = await self._resolve_refs(self._refs_list(props), r["project"])
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
                "disposition": r["disposition"],
                "verified_by": r["verified_by"],
                "evidence": "; ".join(_coerce_evidence(props.get("evidence"))),
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
            "standard_clause", "status", "disposition", "verified_by", "evidence", "last_verified",
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

    # ── compliance matrix (clause-first) ───────────────────────────────
    async def compliance_rows(self, project: str) -> list[dict]:
        """One row per SOURCE CLAUSE of the project's registered documents —
        the inverse join of the RTM. A clause with no requirement against it
        appears with an empty requirement column and disposition ``uncovered``:
        that row is the missed obligation a tender review exists to catch.

        Sources are the project's own documents (kb ``list_sources`` with
        ``include_global=False``); a global standard's clauses are not listed,
        because "every clause of IEC 60287" is not a client obligation set.
        """
        project = (project or "").strip()
        if not project:
            return []
        try:
            sources = await self.call_app("kb", "list_sources", project=project, include_global=False)
        except Exception:  # noqa: BLE001 — kb optional; no sources → empty matrix
            return []
        reqs = [r for r in await self.list_all() if r["project"] == project]
        # requirement → the set of clause slugs it covers (its source clause plus
        # every resolved citation), each requirement counted once per clause.
        by_clause: dict[str, list[dict]] = {}
        for r in reqs:
            note = self._find(r["id"])
            props = (note or {}).get("properties", {}) or {}
            r = {**r, "evidence": _coerce_evidence(props.get("evidence"))}
            slugs: list[str] = [r["source_clause"]] if r["source_clause"] else []
            for x in await self._resolve_refs(self._refs_list(props), project):
                if x["matched"] and x["slug"] and x["slug"] not in slugs:
                    slugs.append(x["slug"])
            for slug in slugs:
                by_clause.setdefault(slug, []).append(r)
        out: list[dict] = []
        for src in (sources or {}).get("sources", []):
            try:
                detail = await self.call_app("kb", "source_clauses", reference_slug=src["slug"], bodies=False)
            except Exception:  # noqa: BLE001
                continue
            for c in (detail or {}).get("clauses", []):
                hits = by_clause.get(c["slug"], [])
                dispositions = sorted({h["disposition"] for h in hits if h["disposition"]})
                statuses = sorted({h["status"] for h in hits})
                if not hits:
                    disposition = "uncovered"
                elif not dispositions:
                    disposition = "undeclared"
                elif len(dispositions) > 1:
                    # Two requirements disagree — a reviewer must see the conflict,
                    # never a joined string that reads as a position nobody declared.
                    disposition = "conflict"
                else:
                    disposition = dispositions[0]
                out.append({
                    "source": src.get("standard_id") or src.get("title") or src["slug"],
                    # Not exported: the page needs them to write a requirement
                    # against THIS edition's clause (two editions share an id).
                    "edition": str(src.get("edition") or ""),
                    "clause_slug": c["slug"],
                    "clause": c["clause"],
                    "clause_title": c.get("clause_title") or "",
                    "requirements": ", ".join(h["req_id"] for h in hits),
                    "disposition": disposition,
                    "status": ", ".join(statuses),
                    "evidence": "; ".join(e for h in hits for e in h["evidence"]),
                    "covered": bool(hits),
                })
        return out

    async def compliance_markdown(self, project: str) -> str:
        """The compliance matrix as markdown — the ``[provides.report]`` method.
        Renders from the same ``compliance_rows`` the JSON route returns, never a
        second derivation (app-reports.md § consistency invariant)."""
        from emptyos.sdk.utils import format_markdown_table

        rows = await self.compliance_rows(project)
        columns = ["source", "clause", "clause_title", "requirements", "disposition", "status", "evidence"]
        covered = sum(1 for r in rows if r["covered"])
        head = (f"# Compliance matrix — {project}\n\n"
                f"{len(rows)} source clauses · {covered} covered · {len(rows) - covered} uncovered\n\n")
        if not rows:
            return head + "_No registered source documents for this project._\n"
        return head + format_markdown_table(rows, columns) + "\n"

    @web_route("GET", "/api/export/compliance")
    async def api_export_compliance(self, request):
        """Clause-first compliance matrix for one project.

        ``?project=<id>`` is required. ``?format=csv`` downloads CSV;
        ``?format=md`` / ``pdf`` go through ``report_response`` (the
        ``[provides.report]`` contract); the default (``json``) returns
        ``{markdown, rows, summary}`` for the page.
        """
        from emptyos.sdk.utils import rows_to_csv

        project = (request.query_params.get("project") or "").strip()
        fmt = (request.query_params.get("format") or "json").strip().lower()
        if not project:
            return {"error": "project is required"}
        if fmt in ("pdf", "md", "markdown"):
            md = await self.compliance_markdown(project)
            # A refused project id (a reserved device stem, a slash) is not an
            # error here — the matrix is still readable — but its file needs a name.
            name = f"compliance-{safe_path_segment(project) or 'project'}"
            return await self.report_response(md, name=name, fmt=fmt)
        rows = await self.compliance_rows(project)
        columns = ["source", "clause", "clause_title", "requirements", "disposition", "status", "evidence"]
        if fmt == "csv":
            from starlette.responses import Response

            return Response(
                rows_to_csv(rows, columns),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="compliance.csv"'},
            )
        covered = sum(1 for r in rows if r["covered"])
        return {"markdown": await self.compliance_markdown(project), "rows": rows,
                "summary": {"clauses": len(rows), "covered": covered, "uncovered": len(rows) - covered}}

    # ── extraction from a registered source ────────────────────────────
    @staticmethod
    def _chunk_clauses(clauses: list[dict]) -> list[list[dict]]:
        chunks: list[list[dict]] = []
        cur: list[dict] = []
        size = 0
        for c in clauses:
            n = len(c.get("body") or "") + 80
            if cur and size + n > _EXTRACT_CHUNK_CHARS:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(c)
            size += n
        if cur:
            chunks.append(cur)
        return chunks

    async def _extract_chunk(self, chunk: list[dict]) -> list[dict]:
        """One think() over a batch of clauses → the model's candidate rows.
        A reply that is not JSON, or not a list of objects, yields ``[]``; a
        provider failure propagates for ``extract_preview`` to answer in-band."""
        parts = []
        for c in chunk:
            parts.append(f"### Clause {c['clause']} — {c.get('clause_title') or ''}\n{c.get('body') or ''}".strip())
        raw = await self.think("\n\n".join(parts), system=EXTRACT_REQUIREMENTS_SYSTEM,
                               domain="text", temperature=0.2, timeout_s=_EXTRACT_THINK_TIMEOUT_S)
        data = parse_llm_json(raw if isinstance(raw, str) else "", fallback=[])
        if isinstance(data, dict):
            data = data.get("requirements") or data.get("items") or [data]
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    async def extract_preview(self, project: str, source_slug: str) -> dict:
        """Propose requirement rows from a source's clause notes. Writes nothing.

        Same reply shape as ``api_import_preview`` so the reviewed batch goes to
        ``api_import_confirm`` → ``add()`` unchanged. Every row lands
        ``proposed``; priority comes from the obligation's own verb
        (``_infer_priority``), never from the model.
        """
        project = (project or "").strip()
        source_slug = (source_slug or "").strip()
        if not project:
            return {"error": "project is required"}
        if not source_slug:
            return {"error": "source_slug is required"}
        try:
            src = await self.call_app("kb", "source_clauses", reference_slug=source_slug, bodies=True)
        except Exception as e:  # noqa: BLE001 — kb absent or the call failed
            return {"error": f"kb unavailable: {e}"}
        if not isinstance(src, dict) or src.get("error"):
            return {"error": (src or {}).get("error", "source not found") if isinstance(src, dict) else "source not found"}
        clauses = [c for c in src.get("clauses", []) if (c.get("body") or "").strip()]
        warnings: list[str] = []
        if len(clauses) > _MAX_EXTRACT_CLAUSES:
            warnings.append(f"source has {len(clauses)} clauses — only the first {_MAX_EXTRACT_CLAUSES} were read")
            clauses = clauses[:_MAX_EXTRACT_CLAUSES]
        if not clauses:
            return {"rows": [], "warnings": ["source has no clause notes with text — ingest it first"],
                    "summary": {"total": 0, "new": 0, "duplicate": 0, "skipped": 0, "projects": [project]},
                    "source": {"slug": source_slug, "title": src.get("title", "")}}
        by_clause = {_norm_clause_no(c["clause"]): c for c in clauses}
        std_label = f"{src.get('standard_id') or src.get('standard') or ''} {src.get('edition') or ''}".strip()
        existing = await self._existing_titles_by_project()
        rows: list[dict] = []
        skipped = 0
        provenances: list[dict] = []
        for chunk in self._chunk_clauses(clauses):
            try:
                items = await self._extract_chunk(chunk)
            except Exception as e:  # noqa: BLE001 — no provider, consent refused, timeout: answer in-band
                return {"error": f"the model could not read the clauses: {e}",
                        "rows": rows, "source": {"slug": source_slug, "title": src.get("title", "")}}
            prov = self.last_provenance() or {}
            if prov.get("mode"):
                provenances.append(prov)
            for item in items:
                statement = str(item.get("statement") or "").strip()
                clause_no = _norm_clause_no(item.get("clause"))
                src_clause = by_clause.get(clause_no)
                if not statement or src_clause is None:
                    skipped += 1  # no statement, or a clause number the source does not have
                    continue
                title = str(item.get("title") or "").strip() or statement[:80]
                rows.append({
                    "statement": statement,
                    "title": title[:80],
                    "project": project,
                    "priority": _infer_priority(statement),
                    "references": f"{std_label} §{clause_no}".strip(),
                    "source_clause_slug": src_clause["slug"],
                    "clause": clause_no,
                    "duplicate": title[:80].casefold() in existing.get(project, set()),
                })
        if len(rows) > MAX_IMPORT_ROWS:
            warnings.append(f"{len(rows)} obligations found — only the first {MAX_IMPORT_ROWS} are shown; "
                            "split the source or extract per chapter")
            rows = rows[:MAX_IMPORT_ROWS]
        new_count = sum(1 for r in rows if not r["duplicate"])
        # One chip for the batch: the first provider that answered, plus every
        # provider that took part when the chain moved mid-run.
        providers = sorted({str(p.get("provider") or "") for p in provenances if p.get("provider")})
        return {
            "rows": rows,
            "warnings": warnings,
            "summary": {"total": len(rows), "new": new_count, "duplicate": len(rows) - new_count,
                        "skipped": skipped, "projects": [project], "clauses_read": len(clauses)},
            "source": {"slug": source_slug, "title": src.get("title", ""), "standard_id": src.get("standard_id", "")},
            "provenance": provenances[0] if provenances else {},
            "providers": providers,
        }

    @web_route("POST", "/api/extract/preview")
    async def api_extract_preview(self, request):
        """Body ``{project, source_slug}`` → proposed rows for review (writes nothing).
        Confirm the reviewed batch through ``POST /api/import/confirm``."""
        body = await self.read_json(request)
        return await self.extract_preview(str(body.get("project") or ""), str(body.get("source_slug") or ""))

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
                           priority=priority, references=references,
                           source_clause=str(r.get("source_clause_slug") or "").strip())
            imported += 1

        return {"ok": True, "imported": imported, "skipped": skipped}

    # ── kb citation resolution (fail-soft) ─────────────────────────────
    async def _resolve_one(self, reference: str, project: str = "") -> dict:
        """``project`` lets a client-document citation ("XYZ-SPEC-001 cl 3.4")
        resolve against that project's registered sources before the global KB."""
        try:
            r = await self.call_app("kb", "resolve_reference", reference=reference,
                                    project=(project or "").strip())
            return r if isinstance(r, dict) else {"matched": False}
        except Exception:  # noqa: BLE001 — kb optional; degrade to unresolved
            return {"matched": False, "slug": None, "reason": "kb-unavailable"}

    async def _resolve_refs(self, refs: list[str], project: str = "") -> list[dict]:
        out = []
        for ref in refs:
            r = await self._resolve_one(ref, project)
            out.append({
                "reference": ref,
                "matched": bool(r.get("matched")),
                "slug": r.get("slug"),
                "scope": r.get("scope", ""),
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
                {"id": "disposition", "label": "Disposition", "type": "select",
                 "options": list(DISPOSITIONS)},
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
