"""worklog — day/project/employer read + aggregate surfaces.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns every read path over the day-note corpus: loading and parsing
one day, scanning them all, and the aggregate views built on top (recent,
by-project, heatmap, month grid, status rollup, employers). Source of truth
for ``_all_days``, which nearly every other module consumes.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._daily_path / self._worklog_dir (spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from emptyos.sdk import parse_frontmatter, web_route
from emptyos.sdk.utils import clamp_days
from .parser import STATUS_TONE, dominant_status, parse_day
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import WorklogApp  # noqa: F401 — for type hints only


# ─── Bind to WorklogApp class as ────────────────────────────────
#   _load_day           = _reads._load_day
#   _all_days           = _reads._all_days
#   _summarize          = _reads._summarize
#   api_day             = _reads.api_day
#   timeline_items      = _reads.timeline_items
#   api_timeline_items  = _reads.api_timeline_items
#   api_recent          = _reads.api_recent
#   _project_names      = _reads._project_names
#   project_activity    = _reads.project_activity
#   project_names       = _reads.project_names
#   api_projects        = _reads.api_projects
#   api_by_project      = _reads.api_by_project
#   api_heatmap         = _reads.api_heatmap
#   month_cells         = _reads.month_cells
#   api_month           = _reads.api_month
#   api_status_rollup   = _reads.api_status_rollup
#   _employer_counts    = _reads._employer_counts
#   employer_names      = _reads.employer_names
#   api_employers       = _reads.api_employers
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── reads ─────────────────────────────────────────────────────────
async def _load_day(self, path: Path) -> dict | None:
    try:
        content = await self.read(str(path))
    except Exception:
        return None
    fm = parse_frontmatter(content)
    try:
        d = date.fromisoformat(path.stem)
    except ValueError:
        # Filename isn't a date (a renamed or hand-made note) — fall back to
        # the frontmatter, and drop the note if that doesn't date it either.
        try:
            d = date.fromisoformat(str(fm.get("date") or ""))
        except ValueError:
            return None
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
        "hours": day["parsed"].get("hours", 0.0),
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


async def timeline_items(self, days: int = 1) -> list[dict]:
    """Life-suite timeline contribution ([[contributes.life.timeline]]).

    One item per work item over the last ``days`` days, most recent
    first. Item shape (suite contract, docs/suites/life-cohesion.md):
    {ts, title, kind, href} + status/employer extras. Day notes carry no
    per-item times, so ts is the day at midnight.
    """
    n = clamp_days(days)
    items: list[dict] = []
    today = date.today()
    for i in range(n):
        d = today - timedelta(days=i)
        day = await self._load_day(self._daily_path(d))
        if not day:
            continue
        for g in day["parsed"]["projects"]:
            proj = (g.get("project") or "").strip()
            for it in g.get("items", []):
                text = (it.get("text") or "").strip()
                if not text:
                    continue
                items.append({
                    "ts": f"{day['date']}T00:00:00",
                    "title": f"[{proj}] {text}" if proj else text,
                    "kind": "worklog",
                    "href": "/worklog/",
                    "status": it.get("status", ""),
                    "employer": day.get("employer", ""),
                })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


@web_route("GET", "/api/timeline-items")
async def api_timeline_items(self, request):
    return {"items": await self.timeline_items(days=request.query_params.get("days"))}


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
