"""Work Log — professional work-activity log, project-grouped + status-tagged.

Vault structure: 60_Worklogs/{YEAR}/{YYYY-MM-DD}.md
Sections: Plan (prose), Work (### Project → status-tagged items), Timesheet, Notes.

Journal-parallel: one file per day, ``_daily_lock`` serializes read-modify-write,
emits fire OUTSIDE the lock (handlers may recurse via call_app). Distinct from
the personal ``journal`` app (mood/life) and the EmptyOS ``devlog`` — this is the
forward-going professional log, seeded with imported employment history
(``employer`` frontmatter discriminates).
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    parse_frontmatter,
    set_frontmatter_field,
    web_route,
)

from . import boards as _boards
from . import capture_ingest as _capture_ingest
from . import competency as _competency
from . import importing as _importing
from . import reads as _reads
from . import reporting as _reporting
from . import search as _search
from . import surfaces as _surfaces
from . import timer as _timer
from .shared import _parse_date

from .parser import (
    STATUS_EMOJI,
    parse_work,
    render_work_preserving,
    replace_section,
    split_sections,
)



class WorklogApp(BaseApp):

    # ── paths / config ────────────────────────────────────────────────
    def _entries_tmpl(self) -> str:
        return self.vault_config("entries", "60_Worklogs/{year}/{date}.md")

    def _worklog_dir(self) -> Path:
        raw = self._entries_tmpl()
        base = raw.split("/")[0] if "/" in raw else raw
        return self.vault_root / base

    def _daily_path(self, d: date) -> Path:
        raw = self._entries_tmpl()
        path = raw.replace("{year}", str(d.year)).replace("{date}", d.isoformat())
        return self.vault_root / path

    def _daily_lock(self, d: date) -> asyncio.Lock:
        return self.write_lock(f"worklog:{d.isoformat()}")

    def _default_employer(self) -> str:
        # Live ⚙ toggle first, then [apps.worklog] in emptyos.toml, then the
        # default — never a hardcoded value, so the shipped app stays generic
        # (CLAUDE.md rule 15, .claude/rules/app-ui-patterns.md).
        return self.setting_or_config(
            "worklog.default_employer", "", config_key="default_employer") or ""

    def _default_status(self) -> str:
        return self.setting_or_config(
            "worklog.default_status", "in-progress",
            config_key="default_status") or "in-progress"

    async def _ensure_daily(self, d: date, employer: str = "") -> str:
        """Get or create a day note. Caller holds ``_daily_lock`` for writes."""
        path = self._daily_path(d)
        try:
            content = await self.read(str(path))  # noqa: eos-rmw  (caller holds lock)
        except Exception:
            weekday = d.strftime("%A")
            emp = (employer or self._default_employer()).strip()
            emp_line = f"employer: {emp}\n" if emp else ""
            content = (
                f"---\ndate: {d.isoformat()}\ntags:\n  - worklog\n"
                f"{emp_line}author: user\n---\n\n"
                f"# {d.isoformat()} {weekday}\n\n"
                f"## Plan\n\n\n"
                f"## Work\n\n"
            )
            await self.write(str(path), content)
            await self.emit("worklog:created", {"date": d.isoformat()})
            return content

        requested = (employer or "").strip()
        current = str(parse_frontmatter(content).get("employer") or "").strip()
        if requested and current and requested.lower() != current.lower():
            raise ValueError(
                f"{d.isoformat()} is already categorized under {current}; "
                f"cannot log it under {requested}"
            )
        if requested and not current:
            content = set_frontmatter_field(content, "employer", requested)
            await self.write(str(path), content)
        return content

    # ── writes (all under _daily_lock; emit OUTSIDE the lock) ──────────
    async def log_work(self, date_s: str = "", project: str = "", text: str = "",
                       status: str = "", employer: str = "") -> dict:
        d = _parse_date(date_s)
        project = (project or "General").strip()
        employer = (employer or "").strip()
        if employer == "—":
            employer = ""
        text = (text or "").strip()
        if not text:
            raise ValueError("work text is empty")
        status = (status or self._default_status()).strip().lower()
        if status not in STATUS_EMOJI:
            status = "note"  # plain bullet, no emoji
        async with self._daily_lock(d):
            content = await self._ensure_daily(d, employer)
            actual_employer = str(
                parse_frontmatter(content).get("employer") or ""
            ).strip()
            work_body = split_sections(content).get("Work", "")
            groups = parse_work(work_body)
            grp = next((g for g in groups if g["project"].lower() == project.lower()), None)
            if grp is None:
                grp = {"project": project, "items": []}
                groups.append(grp)
            grp["items"].append({"text": text, "status": None if status == "note" else status})
            new_content = replace_section(
                content, "Work", render_work_preserving(work_body, groups))
            await self.write(str(self._daily_path(d)), new_content)
        await self.emit("worklog:logged",
                        {"date": d.isoformat(), "project": project, "text": text,
                         "status": status, "employer": actual_employer})
        return {"ok": True, "date": d.isoformat(), "project": project,
                "employer": actual_employer}

    async def set_status(self, date_s: str, project: str, item_text: str, status: str) -> dict:
        d = _parse_date(date_s)
        status = (status or "").strip().lower()
        async with self._daily_lock(d):
            try:
                content = await self.read(str(self._daily_path(d)))  # noqa: eos-rmw
            except Exception:
                return {"error": "no worklog for that day"}
            work_body = split_sections(content).get("Work", "")
            groups = parse_work(work_body)
            hit = False
            for g in groups:
                if g["project"].lower() != project.lower():
                    continue
                for it in g["items"]:
                    if it["text"].strip() == item_text.strip():
                        it["status"] = status if status in STATUS_EMOJI else None
                        hit = True
            if not hit:
                return {"error": "item not found"}
            new_content = replace_section(
                content, "Work", render_work_preserving(work_body, groups))
            await self.write(str(self._daily_path(d)), new_content)
        await self.emit("worklog:status-changed",
                        {"date": d.isoformat(), "project": project, "status": status})
        return {"ok": True}

    async def _set_prose(self, date_s: str, header: str, text: str) -> dict:
        d = _parse_date(date_s)
        async with self._daily_lock(d):
            content = await self._ensure_daily(d)
            new_content = replace_section(content, header, (text or "").strip())
            await self.write(str(self._daily_path(d)), new_content)
        return {"ok": True}

    # ── web: writes ───────────────────────────────────────────────────
    @web_route("POST", "/api/log")
    async def api_log(self, request):
        data = await self.read_json(request)
        if not (data.get("text") or "").strip():
            return {"error": "text required"}
        try:
            return await self.log_work(data.get("date", ""), data.get("project", ""),
                                       data.get("text", ""), data.get("status", ""),
                                       data.get("employer", ""))
        except ValueError as e:
            return {"error": str(e)}

    @web_route("POST", "/api/status")
    async def api_status(self, request):
        data = await self.read_json(request)
        return await self.set_status(data.get("date", ""), data.get("project", ""),
                                     data.get("item", ""), data.get("status", ""))

    @web_route("POST", "/api/plan")
    async def api_plan(self, request):
        data = await self.read_json(request)
        return await self._set_prose(data.get("date", ""), "Plan", data.get("text", ""))

    @web_route("POST", "/api/update")
    async def api_update(self, request):
        data = await self.read_json(request)
        return await self._set_prose(data.get("date", ""), "Update", data.get("text", ""))

    # ── Importing (extracted to importing.py) ──
    # ── CPEng competency evidence (extracted to competency.py) ──
    competency_report      = _competency.competency_report
    api_competency         = _competency.api_competency
    panel_competency       = _competency.panel_competency
    suggest_competencies   = _competency.suggest_competencies
    tag_competency         = _competency.tag_competency
    api_competency_suggest = _competency.api_competency_suggest
    api_competency_tag     = _competency.api_competency_tag

    portable_payload   = _importing.portable_payload
    api_portable       = _importing.api_portable
    import_portable    = _importing.import_portable
    api_import_preview = _importing.api_import_preview
    api_import         = _importing.api_import
    _import_request    = _importing._import_request

    # ── Reads (extracted to reads.py) ──
    _load_day          = _reads._load_day
    _all_days          = _reads._all_days
    _summarize         = _reads._summarize
    api_day            = _reads.api_day
    timeline_items     = _reads.timeline_items
    api_timeline_items = _reads.api_timeline_items
    api_recent         = _reads.api_recent
    _project_index     = _reads._project_index
    _project_names     = _reads._project_names
    project_activity   = _reads.project_activity
    project_names      = _reads.project_names
    api_projects       = _reads.api_projects
    api_by_project     = _reads.api_by_project
    api_heatmap        = _reads.api_heatmap
    month_cells        = _reads.month_cells
    api_month          = _reads.api_month
    api_status_rollup  = _reads.api_status_rollup
    _employer_counts   = _reads._employer_counts
    employer_names     = _reads.employer_names
    api_employers      = _reads.api_employers

    # ── Reporting (extracted to reporting.py) ──
    _window           = _reporting._window
    api_years         = _reporting.api_years
    api_rollup        = _reporting.api_rollup
    api_smart_parse   = _reporting.api_smart_parse
    draft_update      = _reporting.draft_update
    api_update_draft  = _reporting.api_update_draft
    api_carryover     = _reporting.api_carryover
    api_timesheet_pdf = _reporting.api_timesheet_pdf
    api_export_csv    = _reporting.api_export_csv

    # ── Search (extracted to search.py) ──
    search_items = _search.search_items
    api_search   = _search.api_search

    # ── Surfaces (extracted to surfaces.py) ──
    panel_active     = _surfaces.panel_active
    panel_blocked    = _surfaces.panel_blocked
    voice_log_work   = _surfaces.voice_log_work
    _resolve_item    = _surfaces._resolve_item
    voice_set_status = _surfaces.voice_set_status
    voice_set_plan   = _surfaces.voice_set_plan
    voice_set_update = _surfaces.voice_set_update
    cmd_worklog      = _surfaces.cmd_worklog

    # ── Boards-as-view-layer (extracted to boards.py) ──
    list_all         = _boards.list_all
    set_field        = _boards.set_field
    board_presets    = _boards.board_presets

    # ── Capture ingest lane (extracted to capture_ingest.py) ──
    pending_captures      = _capture_ingest.pending_captures
    apply_capture_draft   = _capture_ingest.apply_capture_draft
    dismiss_capture_draft = _capture_ingest.dismiss_capture_draft
    api_capture_pending   = _capture_ingest.api_capture_pending
    api_capture_apply     = _capture_ingest.api_capture_apply
    api_capture_dismiss   = _capture_ingest.api_capture_dismiss

    # ── Timer (extracted to timer.py) ──
    timer_status     = _timer.timer_status
    timer_start      = _timer.timer_start
    timer_stop       = _timer.timer_stop
    timer_cancel     = _timer.timer_cancel
    api_timer        = _timer.api_timer
    api_timer_start  = _timer.api_timer_start
    api_timer_stop   = _timer.api_timer_stop
    api_timer_cancel = _timer.api_timer_cancel
