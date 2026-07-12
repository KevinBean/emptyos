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
from datetime import date, timedelta
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    cli_command,
    parse_frontmatter,
    set_frontmatter_field,
    web_route,
)
from emptyos.sdk.utils import parse_llm_json

from .parser import (
    STATUS_EMOJI,
    STATUS_TONE,
    dominant_status,
    parse_day,
    parse_work,
    render_work,
    replace_section,
    split_sections,
)

WEEKLY_ROLLUP_SYSTEM = (
    "You write a factual weekly work summary from a list of dated work items "
    "(format: date | project | status | text). Group by project, past tense, "
    "most substantial project first. End with blocked or in-review items on "
    "their own final line, if any. Keep it under 200 words.\n\n"
    "Do NOT:\n"
    "- Invent work that is not in the list.\n"
    "- Add praise, filler, or self-assessment.\n"
    "- Speculate about outcomes or next steps.\n"
    "- Use headings or bullet lists; short paragraphs only."
)

SMART_LOG_SYSTEM = (
    "You convert ONE natural-language work-log sentence into strict JSON: "
    '{"project": str, "text": str, "status": str, "date": str}.\n'
    "project: choose ONLY from the provided known-projects list; if none "
    'fits, use "General".\n'
    "status: one of complete, in-progress, todo, next, waiting, review, "
    "blocked. Default in-progress.\n"
    'date: YYYY-MM-DD only when the sentence names a day explicitly, else "".\n\n'
    "Do NOT:\n"
    "- Output anything except the JSON object.\n"
    "- Invent a project that is not in the list.\n"
    "- Rewrite the work text beyond removing the project/status/date words."
)

# Statuses that carry over to the next workday (anything not finished).
CARRYOVER_STATUSES = ("in-progress", "blocked", "waiting", "todo", "next")


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
        # Read the UI-configurable Settings store (the ⚙ panel), NOT a
        # hardcoded value — keeps the shipped app generic (CLAUDE.md rule 15).
        return self.setting("worklog.default_employer", "") or ""

    def _default_status(self) -> str:
        return self.setting("worklog.default_status", "in-progress") or "in-progress"

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
            groups = parse_work(split_sections(content).get("Work", ""))
            grp = next((g for g in groups if g["project"].lower() == project.lower()), None)
            if grp is None:
                grp = {"project": project, "items": []}
                groups.append(grp)
            grp["items"].append({"text": text, "status": None if status == "note" else status})
            new_content = replace_section(content, "Work", render_work(groups))
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
            groups = parse_work(split_sections(content).get("Work", ""))
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
            new_content = replace_section(content, "Work", render_work(groups))
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

    # ── reads ─────────────────────────────────────────────────────────
    async def _load_day(self, path: Path) -> dict | None:
        try:
            content = await self.read(str(path))
        except Exception:
            return None
        try:
            d = date.fromisoformat(path.stem)
        except ValueError:
            try:
                d = date.fromisoformat(str(parse_frontmatter(content).get("date", "")))
            except ValueError:
                return None
        fm = parse_frontmatter(content)
        return {
            "date": d.isoformat(),
            "weekday": d.strftime("%A"),
            "employer": (fm.get("employer") or "").strip(),
            "parsed": parse_day(content),
            "path": path,
        }

    async def _all_days(self, employer: str = "") -> list[dict]:
        base = self._worklog_dir()
        days: list[dict] = []
        if not base.exists():
            return days
        for year_dir in sorted(base.iterdir(), reverse=True):
            if not year_dir.is_dir():
                continue
            for f in year_dir.glob("*.md"):
                day = await self._load_day(f)
                if not day:
                    continue
                if employer and day["employer"].lower() != employer.lower():
                    continue
                days.append(day)
        days.sort(key=lambda x: x["date"], reverse=True)
        return days

    def _summarize(self, day: dict) -> dict:
        groups = day["parsed"]["projects"]
        return {
            "date": day["date"],
            "weekday": day["weekday"],
            "employer": day["employer"],
            "projects": [g["project"] for g in groups if g["items"]],
            "project_count": sum(1 for g in groups if g["items"]),
            "item_count": sum(len(g["items"]) for g in groups),
            "status_counts": day["parsed"]["status_counts"],
            "dominant": dominant_status(groups),
            "has_plan": bool(day["parsed"]["plan"]),
        }

    @web_route("GET", "/api/day")
    async def api_day(self, request):
        date_s = request.query_params.get("date", "") or date.today().isoformat()
        try:
            d = date.fromisoformat(date_s)
        except ValueError:
            return {"error": "bad date"}
        path = self._daily_path(d)
        day = await self._load_day(path)
        if not day:
            return {"date": d.isoformat(), "weekday": d.strftime("%A"), "exists": False,
                    "employer": self._default_employer(), "plan": "", "update": "",
                    "projects": [], "timesheet": [], "notes": []}
        try:
            vault_rel = str(path.resolve().relative_to(self.vault_root.resolve())).replace("\\", "/")
        except Exception:
            vault_rel = ""
        p = day["parsed"]
        return {"date": day["date"], "weekday": day["weekday"], "exists": True,
                "employer": day["employer"], "_vault_path": vault_rel, **p}

    @web_route("GET", "/api/recent")
    async def api_recent(self, request):
        try:
            days_n = int(request.query_params.get("days", "30"))
        except ValueError:
            days_n = 30
        employer = request.query_params.get("employer", "")
        cutoff = (date.today() - timedelta(days=days_n)).isoformat() if days_n > 0 else ""
        out = []
        for day in await self._all_days(employer):
            if cutoff and day["date"] < cutoff:
                break
            out.append(self._summarize(day))
        return {"days": out, "count": len(out)}

    async def _project_names(self, employer: str = "") -> list[tuple[str, int]]:
        """Known projects ranked by item count — shared by the picker + smart-parse."""
        names: dict[str, int] = {}
        for day in await self._all_days(employer):
            for g in day["parsed"]["projects"]:
                if g["items"]:
                    names[g["project"]] = names.get(g["project"], 0) + len(g["items"])
        projects, err = await self.try_call_app("projects", "list_projects")
        if not err:
            known_lower = {known.lower() for known in names}
            for project in projects or []:
                if str(project.get("status") or "").lower() in {
                    "archived", "completed", "shelved",
                }:
                    continue
                name = str(project.get("name") or "").strip()
                project_employer = str(project.get("employer") or "").strip()
                if employer and project_employer.lower() != employer.lower():
                    continue
                if name and name.lower() not in known_lower:
                    names[name] = 0
                    known_lower.add(name.lower())
        return sorted(names.items(), key=lambda kv: kv[1], reverse=True)

    async def project_activity(
        self, project: str = "", days: int = 30, limit: int = 20,
        employer: str = "",
    ) -> dict:
        """Recent Worklog items for one exact project label."""
        project = (project or "").strip()
        if not project:
            return {"error": "project required"}
        days = max(1, min(int(days or 30), 366))
        limit = max(1, min(int(limit or 20), 100))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        items: list[dict] = []
        status_counts: dict[str, int] = {}
        active_days: set[str] = set()
        for day in await self._all_days(employer):
            if day["date"] < cutoff:
                break
            for group in day["parsed"]["projects"]:
                if group["project"].strip().lower() != project.lower():
                    continue
                for item in group["items"]:
                    status = item.get("status") or "note"
                    status_counts[status] = status_counts.get(status, 0) + 1
                    active_days.add(day["date"])
                    if len(items) < limit:
                        items.append({
                            "date": day["date"],
                            "weekday": day["weekday"],
                            "employer": day["employer"],
                            "text": item["text"],
                            "status": status,
                        })
        return {
            "project": project,
            "employer": employer,
            "items": items,
            "item_count": sum(status_counts.values()),
            "day_count": len(active_days),
            "status_counts": status_counts,
        }

    async def project_names(self, employer: str = "") -> list[tuple[str, int]]:
        """Public cross-app contract: ranked ``(project, item_count)`` list.

        Thin wrapper over ``_project_names`` so other apps (e.g. worklog-capture)
        can ground drafts in the known-project vocabulary via ``call_app`` without
        reaching an underscore-private method.
        """
        return await self._project_names(employer)

    @web_route("GET", "/api/projects")
    async def api_projects(self, request):
        employer = request.query_params.get("employer", "")
        ranked = await self.project_names(employer)
        return {"projects": [{"name": n, "items": c} for n, c in ranked]}

    @web_route("GET", "/api/by-project")
    async def api_by_project(self, request):
        name = (request.query_params.get("name", "") or "").strip().lower()
        employer = request.query_params.get("employer", "")
        if not name:
            return {"error": "name required"}
        days = []
        for day in await self._all_days(employer):
            hits = [g for g in day["parsed"]["projects"] if name in g["project"].lower() and g["items"]]
            if not hits:
                continue
            items = []
            for g in hits:
                items.extend(g["items"])
            days.append({"date": day["date"], "weekday": day["weekday"],
                         "employer": day["employer"], "items": items})
        return {"project": name, "days": days, "day_count": len(days)}

    @web_route("GET", "/api/heatmap")
    async def api_heatmap(self, request):
        employer = request.query_params.get("employer", "")
        year = request.query_params.get("year", "")
        data: dict[str, int] = {}
        for day in await self._all_days(employer):
            if year and not day["date"].startswith(year):
                continue
            data[day["date"]] = sum(len(g["items"]) for g in day["parsed"]["projects"])
        return {"data": data}

    async def month_cells(self, month: str = "", employer: str = "") -> dict:
        """Month-grid cells for a given ``YYYY-MM`` — the ``api_month`` payload.

        Public so other apps (calendar) can aggregate a worklog layer via
        ``call_app("worklog", "month_cells", ...)``. Response shape must stay
        byte-identical to what /api/month always returned.
        """
        month = month or date.today().strftime("%Y-%m")
        cells = []
        for day in await self._all_days(employer):
            if not day["date"].startswith(month):
                continue
            groups = [g for g in day["parsed"]["projects"] if g["items"]]
            tone = STATUS_TONE.get(dominant_status(day["parsed"]["projects"]) or "", "")
            items = [{"id": g["project"], "label": g["project"], "tone": tone} for g in groups[:4]]
            cells.append({"date": day["date"], "items": items})
        return {"month": month, "cells": cells}

    @web_route("GET", "/api/month")
    async def api_month(self, request):
        return await self.month_cells(
            request.query_params.get("month", ""),
            request.query_params.get("employer", ""),
        )

    @web_route("GET", "/api/status-rollup")
    async def api_status_rollup(self, request):
        try:
            days_n = int(request.query_params.get("days", "14"))
        except ValueError:
            days_n = 14
        employer = request.query_params.get("employer", "")
        cutoff = (date.today() - timedelta(days=days_n)).isoformat()
        totals: dict[str, int] = {}
        blocked, review = [], []
        for day in await self._all_days(employer):
            if day["date"] < cutoff:
                break
            for st, c in day["parsed"]["status_counts"].items():
                totals[st] = totals.get(st, 0) + c
            for g in day["parsed"]["projects"]:
                for it in g["items"]:
                    row = {"date": day["date"], "project": g["project"], "text": it["text"]}
                    if it["status"] == "blocked":
                        blocked.append(row)
                    elif it["status"] == "review":
                        review.append(row)
        return {"totals": totals, "blocked": blocked, "review": review}

    def _rollup_window(self, request) -> tuple[int, str, str]:
        """Parse the shared ``?days=&employer=`` pair → (days, employer, cutoff)."""
        try:
            days_n = int(request.query_params.get("days", "7"))
        except ValueError:
            days_n = 7
        days_n = max(1, min(days_n, 92))
        employer = request.query_params.get("employer", "")
        cutoff = (date.today() - timedelta(days=days_n)).isoformat()
        return days_n, employer, cutoff

    @web_route("GET", "/api/rollup")
    async def api_rollup(self, request):
        """AI weekly rollup over the window — the timesheet/review artifact.

        Feeds ``date | project | status | text`` lines to the model (own vault
        content to the configured think chain — no aggregation needed here),
        persists each rollup via ``save_calculation`` for the audit trail.
        """
        days_n, employer, cutoff = self._rollup_window(request)
        lines: list[str] = []
        for day in await self._all_days(employer):
            if day["date"] < cutoff:
                break
            for g in day["parsed"]["projects"]:
                for it in g["items"]:
                    st = it["status"] or "note"
                    lines.append(f"{day['date']} | {g['project']} | {st} | {it['text']}")
        if not lines:
            return {"error": f"no work items in the last {days_n} days"}
        lines.reverse()  # oldest first reads chronologically
        rollup = await self.think(
            "\n".join(lines), system=WEEKLY_ROLLUP_SYSTEM, domain="text", temperature=0.3
        )
        saved = None
        try:
            saved = self.save_calculation(
                label=f"Work rollup — last {days_n}d ending {date.today().isoformat()}",
                inputs={"days": days_n, "employer": employer or "(all)", "items": len(lines)},
                result={"rollup": rollup},
                method="worklog.rollup",
            )
        except Exception:
            pass  # the rollup itself still returns; the note is best-effort
        return {
            "rollup": rollup,
            "items": len(lines),
            "days": days_n,
            "saved": (saved or {}).get("path", ""),
            "provenance": self.last_provenance(),
        }

    @web_route("POST", "/api/smart-parse")
    async def api_smart_parse(self, request):
        """Parse a natural-language log sentence → {project, text, status, date}.

        Returns the parse ONLY — never writes. The form is the confirm surface;
        the user still presses Log.
        """
        data = await self.read_json(request)
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text required"}
        known = [n for n, _ in (await self._project_names(data.get("employer", "")))[:40]]
        raw = await self.think(
            f"Known projects: {', '.join(known) or '(none)'}\nSentence: {text}",
            system=SMART_LOG_SYSTEM,
            domain="text",
            temperature=0.1,
        )
        parsed = parse_llm_json(raw, fallback={}) or {}
        project = str(parsed.get("project") or "General").strip() or "General"
        if project != "General" and known and project not in known:
            project = "General"
        status = str(parsed.get("status") or "").strip().lower()
        if status not in STATUS_EMOJI:
            status = self._default_status()
        out_text = str(parsed.get("text") or "").strip() or text
        date_s = str(parsed.get("date") or "").strip()
        try:
            date.fromisoformat(date_s)
        except ValueError:
            date_s = ""
        return {"project": project, "text": out_text, "status": status, "date": date_s,
                "provenance": self.last_provenance()}

    @web_route("GET", "/api/carryover")
    async def api_carryover(self, request):
        """Open items from the most recent logged day before today — the
        standup opener. The client offers to re-log them onto today."""
        employer = request.query_params.get("employer", "")
        today_s = date.today().isoformat()
        for day in await self._all_days(employer):
            if day["date"] >= today_s:
                continue
            items = [
                {"project": g["project"], "text": it["text"], "status": it["status"]}
                for g in day["parsed"]["projects"]
                for it in g["items"]
                if it["status"] in CARRYOVER_STATUSES
            ]
            return {"from": day["date"], "weekday": day["weekday"], "items": items}
        return {"from": "", "items": []}

    @web_route("GET", "/api/timesheet.pdf")
    async def api_timesheet_pdf(self, request):
        """Styled timesheet PDF over ``?days=&employer=`` — parsed Timesheet
        rows plus per-day item counts, rendered via the shared PDF profile."""
        days_n, employer, cutoff = self._rollup_window(request)
        today_s = date.today().isoformat()
        rows: list[str] = []
        day_lines: list[str] = []
        total_hours = 0.0
        for day in reversed([d for d in await self._all_days(employer) if d["date"] >= cutoff]):
            n_items = sum(len(g["items"]) for g in day["parsed"]["projects"])
            projects = ", ".join(g["project"] for g in day["parsed"]["projects"] if g["items"])
            day_lines.append(f"| {day['date']} {day['weekday'][:3]} | {n_items} | {projects} |")
            for t in day["parsed"].get("timesheet") or []:
                try:
                    hours = float(t.get("hours") or 0)
                except (TypeError, ValueError):
                    hours = 0.0
                total_hours += hours
                note = t.get("note") or ""
                rows.append(f"| {day['date']} | {t.get('project', '')} | {hours:g} | {note} |")
        emp_label = employer or self._default_employer() or "All employers"
        md = (
            "```\n"
            f"TIMESHEET\n{emp_label} · {cutoff} → {today_s}\n"
            f"generated {today_s}\n"
            "```\n\n"
            "## Hours\n\n"
        )
        if rows:
            md += "| Date | Project | Hours | Note |\n|---|---|---|---|\n" + "\n".join(rows)
            md += f"\n\n**Total: {total_hours:g} h**\n"
        else:
            md += "_No timesheet rows recorded in this window._\n"
        md += "\n## Days\n\n| Day | Items | Projects |\n|---|---|---|\n" + "\n".join(day_lines or ["| — | 0 | |"])
        fname = f"timesheet-{today_s}.pdf"
        out_dir = self.data_dir / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self.render_pdf(md, str(out_dir / fname), style="slate")
        except Exception as e:
            return {"error": f"PDF render failed: {e}"}
        return self.serve_data_file("exports", fname, media_type="application/pdf")

    async def _employer_counts(self) -> dict[str, int]:
        seen: dict[str, int] = {}
        for day in await self._all_days():
            e = day["employer"] or "—"
            seen[e] = seen.get(e, 0) + 1
        return seen

    async def employer_names(self) -> list[str]:
        """Public cross-app contract: known employers ranked by day count.

        Excludes the ``—`` (no-employer) sentinel — callers grounding drafts want
        real employer labels only (see worklog-capture).
        """
        counts = await self._employer_counts()
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [n for n, _ in ranked if n and n != "—"]

    @web_route("GET", "/api/employers")
    async def api_employers(self, request):
        seen = await self._employer_counts()
        return {"employers": [{"name": n, "days": c} for n, c in
                              sorted(seen.items(), key=lambda kv: kv[1], reverse=True)],
                "default": self._default_employer()}

    # ── hub panel ─────────────────────────────────────────────────────
    async def panel_active(self) -> list[dict] | None:
        day = await self._load_day(self._daily_path(date.today()))
        if not day:
            return None
        rows = []
        for g in day["parsed"]["projects"]:
            for it in g["items"]:
                if it["status"] in ("in-progress", "blocked"):
                    emoji = STATUS_EMOJI.get(it["status"], "")
                    rows.append({"title": f"{emoji} {it['text']}", "subtitle": g["project"],
                                 "href": f"/worklog/#{day['date']}"})
        return rows or None

    async def panel_blocked(self) -> list[dict] | None:
        """Blocked + in-review items from the last 7 days — the 'waiting on
        someone' glanceable. None (invisible) when nothing is stuck."""
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        rows: list[dict] = []
        for day in await self._all_days():
            if day["date"] < cutoff:
                break
            for g in day["parsed"]["projects"]:
                for it in g["items"]:
                    if it["status"] in ("blocked", "review"):
                        emoji = STATUS_EMOJI.get(it["status"], "")
                        rows.append({
                            "title": f"{emoji} {g['project']} — {it['text']}",
                            "subtitle": day["date"],
                            "href": f"/worklog/#{day['date']}",
                        })
        return rows or None

    # ── voice ─────────────────────────────────────────────────────────
    async def voice_log_work(self, project: str = "", text: str = "", status: str = "") -> dict:
        try:
            await self.log_work("", project, text, status)
        except ValueError as e:
            return {"say": f"Couldn't log that: {e}"}
        today = date.today().isoformat()
        return {"say": f"Logged under {project or 'General'}.",
                "link": {"text": "Open work log", "href": f"/worklog/#{today}"}}

    async def _resolve_item(self, date_s: str, project: str, query: str) -> tuple[str, str] | None:
        """Substring-match a work item within a day → (project, exact_text) or None."""
        q = (query or "").strip().lower()
        if not q:
            return None
        proj = (project or "").strip().lower()
        day = await self._load_day(self._daily_path(_parse_date(date_s)))
        if not day:
            return None
        for g in day["parsed"]["projects"]:
            if proj and g["project"].lower() != proj:
                continue
            for it in g["items"]:
                if q in it["text"].lower():
                    return g["project"], it["text"]
        return None

    async def voice_set_status(self, project: str = "", item_text: str = "", status: str = "") -> dict:
        hit = await self._resolve_item("", project, item_text)
        if not hit:
            return {"say": f"Couldn't find a work item matching “{item_text}” today."}
        proj, exact = hit
        res = await self.set_status("", proj, exact, status)
        if res.get("error"):
            return {"say": f"Couldn't update that: {res['error']}"}
        today = date.today().isoformat()
        return {"say": f"Marked “{exact[:48]}” as {status or 'done'}.",
                "link": {"text": "Open work log", "href": f"/worklog/#{today}"}}

    async def voice_set_plan(self, text: str = "") -> dict:
        if not (text or "").strip():
            return {"say": "No plan text provided."}
        await self._set_prose("", "Plan", text)
        today = date.today().isoformat()
        return {"say": "Plan updated.",
                "link": {"text": "Open work log", "href": f"/worklog/#{today}"}}

    async def voice_set_update(self, text: str = "") -> dict:
        if not (text or "").strip():
            return {"say": "No update text provided."}
        await self._set_prose("", "Update", text)
        today = date.today().isoformat()
        return {"say": "Update recorded.",
                "link": {"text": "Open work log", "href": f"/worklog/#{today}"}}

    # ── cli ───────────────────────────────────────────────────────────
    @cli_command("worklog", help="Work log operations: today | log | recent")
    async def cmd_worklog(self, action: str = "today", project: str = "", text: str = "",
                          status: str = ""):
        if action == "log" and text:
            res = await self.log_work("", project, text, status)
            self.print_rich(f"[green]Logged[/green] {res['project']}: {text}")
            return
        if action == "recent":
            for day in (await self._all_days())[:14]:
                s = self._summarize(day)
                self.print_rich(f"  {s['date']} {s['weekday'][:3]} — {s['item_count']} items "
                                f"· {', '.join(s['projects'][:3])}")
            return
        # today
        day = await self._load_day(self._daily_path(date.today()))
        if not day:
            self.print_rich("[dim]No worklog today.[/dim]")
            return
        for g in day["parsed"]["projects"]:
            self.print_rich(f"[bold]{g['project']}[/bold]")
            for it in g["items"]:
                self.print_rich(f"  {STATUS_EMOJI.get(it['status'] or '', '·')} {it['text']}")


def _parse_date(date_s: str) -> date:
    if date_s:
        try:
            return date.fromisoformat(date_s)
        except ValueError:
            pass
    return date.today()
