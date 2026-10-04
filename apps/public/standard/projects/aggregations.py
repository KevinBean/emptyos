"""Projects — cross-project aggregation queries.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: queries that walk every project and produce a flat list —
upcoming deadlines, all open/closed tasks, room-attached tasks. These are
the read paths the task app and rooms UI consume via ``call_app``.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self.list_projects``, ``self._projects_dir``,
``self._archive_dir``, ``self._parse_tasks`` (defined on the spine).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import (
    DONE_PATTERN,
    ROOM_PATTERN,
    compute_task_decay,
    extract_due,
    parse_frontmatter,
    scheduled,
    web_route,
)

from .shared import is_area_home

if TYPE_CHECKING:
    from .app import ProjectsApp  # noqa: F401 — for type hints only


# ─── Bind to ProjectsApp class as ────────────────────────────────────
#   get_deadlines        = _aggregations.get_deadlines
#   api_deadlines        = _aggregations.api_deadlines
#   get_all_tasks        = _aggregations.get_all_tasks
#   tasks_for_room       = _aggregations.tasks_for_room
#   _iter_project_files  = _aggregations._iter_project_files
#   api_all_tasks        = _aggregations.api_all_tasks
#   api_tasks_for_room   = _aggregations.api_tasks_for_room
#   _deadline_nudge_enabled  = _aggregations._deadline_nudge_enabled
#   scheduled_deadline_nudge = _aggregations.scheduled_deadline_nudge
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


async def get_deadlines(self, days: int = 90, overdue_days: int = 7) -> list[dict]:
    """Projects with deadlines in a time window."""
    projects = await self.list_projects()
    results = []
    for p in projects:
        dl = p.get("deadline")
        if not dl or is_area_home(p):
            continue
        days_left = p.get("days_until_deadline")
        if days_left is None:
            continue
        if -overdue_days <= days_left <= days:
            results.append(
                {
                    "id": p["id"],
                    "name": p["name"],
                    "deadline": dl,
                    "days_left": days_left,
                    "overdue": days_left < 0,
                    "status": p["status"],
                    "progress": p["progress"],
                }
            )
    results.sort(key=lambda x: x["days_left"])
    return results


@web_route("GET", "/api/deadlines")
async def api_deadlines(self, request):
    """Projects with deadlines in a time window. Used by briefing, hub."""
    days = int(request.query_params.get("days", "90"))
    overdue_days = int(request.query_params.get("overdue_days", "7"))
    return await self.get_deadlines(days, overdue_days)


def _deadline_nudge_enabled(self) -> bool:
    """Daily project-deadline summary toggle (dark default)."""
    return bool(self.setting_or_config(
        "projects.feature.deadline-nudge.enabled",
        False,
        config_key="feature.deadline-nudge.enabled",
    ))


@scheduled("0 8 * * *", id="projects-deadline-nudge")
async def scheduled_deadline_nudge(self):
    """Push one bounded daily summary for imminent/overdue project deadlines.

    Last of the four apps this pass to adopt the shared proactive_notify
    pattern (quotes/task/people/journal already did) — reuses the
    existing get_deadlines() query, which already computes exactly the
    window this nudge needs.
    """
    if not self._deadline_nudge_enabled():
        return {"enabled": False, "sent": False}
    days = int(self.setting_or_config(
        "projects.deadline_alert_days", 7, config_key="deadline_alert_days",
    ))
    items = await self.get_deadlines(days=days, overdue_days=days)
    if not items:
        return {"enabled": True, "sent": False, "count": 0}

    overdue = [i for i in items if i["overdue"]]

    def _item_label(i):
        when = "overdue" if i["overdue"] else f"{i['days_left']}d"
        return f"{i['name']} ({when})"

    top = "; ".join(_item_label(i) for i in items[:3])
    label = f"{len(items)} project deadline{'s' if len(items) != 1 else ''}"
    if overdue:
        label += f" ({len(overdue)} overdue)"
    await self.proactive_notify(
        kind="projects-deadline",
        text=f"{label}: {top}",
        dedup_key=f"projects-deadline:{date.today().isoformat()}",
        priority="high" if overdue else "info",
        link={"text": "Open projects", "href": "/projects/"},
    )
    return {"enabled": True, "sent": True, "count": len(items), "overdue": len(overdue)}


async def get_all_tasks(self, status_filter: str = "", include_archived: bool = False) -> list[dict]:
    """All tasks across all projects.

    ``include_archived=False`` (the default) skips ``40_Archive`` — an
    archived project's unchecked boxes are abandoned work, not open tasks,
    and they were silently inflating every overdue count downstream.
    """
    vault = self.vault_root
    today = date.today()
    all_tasks = []
    # Each task gets its project's area as list_projects resolves it (own, else
    # inherited from the nearest ancestor), unfiltered and archive included —
    # so a task row and its project can never disagree about the area.
    area_of = {p["id"]: p.get("area") for p in await self.list_projects()}
    search_dirs = [self._projects_dir()]
    if include_archived:
        search_dirs.append(self._archive_dir())
    for search_dir in search_dirs:
        if not search_dir or not search_dir.exists():
            continue
        for f in self._iter_project_files(search_dir):
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            fm = parse_frontmatter(content)
            if status_filter and fm.get("status", "active") != status_filter:
                continue
            project_id = f.stem
            _, _, task_list = self._parse_tasks(content)
            # Resolve intra-project depends_on/blocks so the flat list carries
            # ready/blocked_by — the task app's Focus view reads blocked_by to
            # auto-classify a dependency-blocked task as #waiting (with the
            # blocker name) instead of guessing from text cues.
            self._resolve_dependencies(task_list)
            rel_path = str(f.relative_to(vault))
            for t in task_list:
                due_str = extract_due(t["text"])
                done_m = DONE_PATTERN.search(t["text"])
                done_date = done_m.group(1) if done_m else ""
                room_m = ROOM_PATTERN.search(t["text"])
                room_id = room_m.group(1) if room_m else ""
                overdue_days, tier = (
                    compute_task_decay(due_str, today)
                    if due_str and not t["done"]
                    else (0, "fresh")
                )
                all_tasks.append(
                    {
                        "text": t["text"],
                        "done": t["done"],
                        "file": rel_path,
                        "line": t["line"] + 1,
                        "due": due_str,
                        "done_date": done_date,
                        "overdue_days": overdue_days,
                        "tier": tier,
                        "project": project_id,
                        "area": area_of.get(project_id),
                        "milestone": next(
                            (m["value"] for m in t["meta"] if m["type"] == "milestone"), None
                        ),
                        "room_id": room_id,
                        "blocked_by": t.get("blocked_by", []),
                        "ready": t.get("ready", True),
                    }
                )
    return all_tasks


async def tasks_for_room(self, room_id: str, status_filter: str = "") -> list[dict]:
    """All tasks (across projects) attached to a given room. Thin filter
    over `get_all_tasks` — kept here because the projects app owns the
    scan; rooms calls it via `call_app("projects", "tasks_for_room", ...)`.
    """
    if not room_id:
        return []
    tasks = await self.get_all_tasks(status_filter)
    return [t for t in tasks if t.get("room_id") == room_id]


def _iter_project_files(self, directory: Path):
    """Yield main .md file for each project in a directory."""
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_file() and entry.suffix == ".md":
            yield entry
        elif entry.is_dir():
            main = entry / f"{entry.name}.md"
            if main.exists():
                yield main
                continue
            for alt in ("README.md", "index.md"):
                p = entry / alt
                if p.exists():
                    yield p
                    break


@web_route("GET", "/api/all-tasks")
async def api_all_tasks(self, request):
    """All tasks across all projects. Used by task app."""
    status_filter = request.query_params.get("status", "")
    include_archived = request.query_params.get("include_archived", "") in ("1", "true")
    return await self.get_all_tasks(status_filter, include_archived=include_archived)


@web_route("GET", "/api/tasks-for-room/{room_id}")
async def api_tasks_for_room(self, request):
    """Tasks attached to *room_id* via the 🗨️ marker. Consumed by the
    rooms UI to render its 'attached tasks' panel."""
    status_filter = request.query_params.get("status", "")
    return await self.tasks_for_room(
        request.path_params["room_id"], status_filter,
    )
