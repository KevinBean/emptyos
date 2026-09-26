"""Projects — scan 10_Projects/ for project notes, parse frontmatter status.

Tracks status lifecycle: idea -> active -> completed -> archived
Also: blocked, shelved. Provides task counts, health assessment, CRUD.
Supports typed projects (personal, engineering, development) with stages
and tool discovery via manifest [provides.project-tools].
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    cli_command,
    parse_frontmatter,
    set_frontmatter_field,
    web_route,
)
from emptyos.sdk.markdown_tasks import complete as _mt_complete
from emptyos.sdk.markdown_tasks import is_done as _mt_is_done
from emptyos.sdk.markdown_tasks import is_open as _mt_is_open
from emptyos.sdk.markdown_tasks import reopen as _mt_reopen
from emptyos.sdk.markdown_tasks import text_matches as _mt_text_matches

from . import reading as _reading
# Re-exported into the spine namespace so sibling helpers can reach them as
# ``_core.PROJECT_STRUCTURE`` / ``_core.META_PREFIXES`` / ``_core._META_RE`` /
# ``_core.scan_task_meta_block`` (operations.py) — keep all six even though
# the spine only references some.
from .shared import (
    META_PREFIXES,
    PROJECT_FEATURES,
    PROJECT_STATUSES,
    PROJECT_STRUCTURE,
    PROJECT_TYPES,
    _META_RE,
    insert_task_line,
    scan_task_meta_block,
)

from . import dev_features as _dev
from . import extended as _ext
from . import operations as _ops
from . import scheduling as _scheduling
from . import panels as _panels
from . import aggregations as _aggregations
from . import viz_scene as _viz_scene
from . import workspace as _workspace


HEALTH_SYSTEM = """You are a project triage analyst rating one project's health.

Output exactly three lines, in this order:
1. Health rating: one of `healthy`, `at-risk`, or `critical`.
2. One-line diagnosis (≤15 words) — what is the dominant signal driving the rating.
3. One concrete next action the user should take (≤15 words, imperative verb).

Weigh: deadline proximity / overdue, stale_days since last edit, ratio of done to total tasks, and any blockers visible in the content preview.

Do NOT:
- Invent metrics that aren't in the input.
- Output more than three lines or any markdown headers, bullets, or bold.
- Hedge with phrases like "perhaps", "might be", "could possibly".
- Repeat the project name back at the user.
- Add an empathetic preamble or closing sign-off.
"""

HEALTH_USER_TMPL = """Project: {name}
Status: {status}, Tasks: {done}/{total} done, Progress: {progress}%, Stale: {stale_days}d since edit
Deadline: {deadline}{overdue_clause}

Content preview:
{snippet}"""
from .dependencies import resolve_dependencies as _resolve_dependencies

# --- Project Folder Standard ---
# Every project is a directory. The system creates and expects this layout.
# Flat .md files in 10_Projects/ are legacy — scanned but flagged for upgrade.


# --- Project Type Definitions ---


# --- Feature Registry (superset architecture) ---
# Every project gets ALL features; type defines which are ON by default.
# Per-project frontmatter `features:` dict overrides defaults.


class ProjectsApp(BaseApp):

    _resolve_dependencies = staticmethod(_resolve_dependencies)

    @cli_command("projects", help="List and filter projects")
    async def cmd_projects(self, action: str = "list", status: str = ""):
        projects = await self.list_projects(status)
        if not projects:
            print("  No projects found")
            return
        for p in projects:
            tasks = f"[{p['done_tasks']}/{p['total_tasks']}]" if p["total_tasks"] > 0 else ""
            dl = f" (due {p['deadline']})" if p.get("deadline") else ""
            print(f"  {p['status']:<12} {p['name']:<35} {tasks}{dl}")

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        status = request.query_params.get("status", "")
        return await self.list_projects(status)

    @web_route("GET", "/api/projects")
    async def api_projects(self, request):
        """List all projects with status, task counts, health assessment."""
        status = request.query_params.get("status", "")
        return await self.list_projects(status)

    @web_route("GET", "/api/projects/{id}")
    async def api_project_detail(self, request):
        """Single project detail with full task list."""
        project_id = request.path_params.get("id", "")

        target = self._find_project_file(project_id)
        if not target or not target.exists():
            return {"error": "Project not found"}

        p = self._read_project(target)
        if not p:
            return {"error": "Failed to read project"}

        content = await self.read(str(target))
        _, _, task_list = self._parse_tasks(content)
        task_list = self._resolve_dependencies(task_list)

        # Extract goal section
        goal = ""
        in_goal = False
        for line in content.split("\n"):
            if line.strip().startswith("## Goal"):
                in_goal = True
                continue
            if in_goal and line.startswith("## "):
                break
            if in_goal:
                goal += line + "\n"

        has_deps = any(t["depends_on"] or t["blocks"] for t in task_list)
        ready_count = sum(1 for t in task_list if t["ready"] and not t["done"])
        blocked_count = sum(1 for t in task_list if not t["ready"] and not t["done"])

        reports: list[dict] = []
        try:
            reports = (
                await self.call_app("reports", "list_for_project", project_id=project_id) or []
            )
        except Exception:
            reports = []

        # 4D-timeline contract: hand the vault-relative path to the frontend
        # so the auto-mounted 📅 button on `[data-entity-path]` can fire.
        vault_rel = self.vault_rel(target)

        # Readable note body (everything after frontmatter) — consumed by the
        # standalone workspace page's read pane. Derived from the already-read
        # `content` so there's no second file read. Inline detail ignores it.
        note_body = content
        if note_body.startswith("---"):
            fm_end = note_body.find("---", 3)
            if fm_end > 0:
                note_body = note_body[fm_end + 3:]
        note_body = note_body.strip()

        return {
            **p,
            "tasks": task_list,
            "goal": goal.strip(),
            "has_dependencies": has_deps,
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "reports": reports,
            "note_body": note_body,
            "_vault_path": vault_rel,
        }

    def _project_worklog_scope(self, project_id: str) -> tuple[str, str]:
        target = self._find_project_file(project_id)
        if not target or not target.exists():
            return "", ""
        project = self._read_project(target) or {}
        return (
            str(project.get("name") or project_id).strip(),
            str(project.get("employer") or "").strip(),
        )

    async def project_worklog(self, project_id: str, days: int = 30, limit: int = 12) -> dict:
        """Read recent Worklog activity for a project workspace."""
        name, employer = self._project_worklog_scope(project_id)
        if not name:
            return {"available": False, "error": "Project not found", "items": []}
        data, err = await self.try_call_app(
            "worklog", "project_activity", project=name, days=days, limit=limit,
            employer=employer,
        )
        if err:
            return {"available": False, "error": err, "project": name, "items": []}
        return {"available": True, **(data or {})}

    async def log_project_work(
        self, project_id: str, text: str = "", status: str = "in-progress",
        date_s: str = "", employer: str = "",
    ) -> dict:
        """Write one explicit project-scoped entry through Worklog's own path."""
        name, project_employer = self._project_worklog_scope(project_id)
        if not name:
            return {"error": "Project not found"}
        text = (text or "").strip()
        if not text:
            return {"error": "text required"}
        result, err = await self.try_call_app(
            "worklog", "log_work", date_s=date_s, project=name,
            text=text, status=status, employer=(employer or project_employer).strip(),
        )
        if err:
            return {"error": f"Worklog unavailable: {err}"}
        return {**(result or {}), "project_id": project_id}

    @web_route("GET", "/api/projects/{id}/worklog")
    async def api_project_worklog(self, request):
        project_id = request.path_params.get("id", "")
        try:
            days = int(request.query_params.get("days", "30"))
            limit = int(request.query_params.get("limit", "12"))
        except ValueError:
            return {"error": "bad days or limit"}
        return await self.project_worklog(project_id, days=days, limit=limit)

    @web_route("POST", "/api/projects/{id}/worklog")
    async def api_log_project_work(self, request):
        project_id = request.path_params.get("id", "")
        data = await self.read_json(request)
        return await self.log_project_work(
            project_id,
            text=data.get("text", ""),
            status=data.get("status", "in-progress"),
            date_s=data.get("date", ""),
            employer=data.get("employer", ""),
        )

    # ── Task assignee — soft bridge to staff's workflow-agent dispatch engine
    # (optional_apps=["staff"]; absence tolerated, same shape as the worklog
    # integration above). No new task lifecycle here — this only wires a UI
    # entry point + a task-line backlink onto staff's existing
    # queued/running/done/error job engine. See
    # 30_Resources/EmptyOS/insights or docs/OPEN-SOURCE-BORROWING-PLAN.md
    # for the Multica borrow verdict this came from. ──

    async def assignable_agents(self) -> list[dict]:
        """Workflow-mode staff agents available to assign a task to.

        Returns [] (not an error) when staff isn't installed, so the
        frontend can hide the "assign" affordance cleanly.
        """
        data, err = await self.try_call_app("staff", "list_workflow_agents")
        if err:
            return []
        return data or []

    @web_route("GET", "/api/assignable-agents")
    async def api_assignable_agents(self, request):
        return {"agents": await self.assignable_agents()}

    async def assign_task_to_agent(self, project_id: str, line: int, agent_id: str) -> dict:
        """Assign one project task to a staff workflow agent and dispatch it now.

        Reuses staff.dispatch_workflow's existing job engine verbatim — this
        method only wires the entry point (read task text, call staff, write
        the backlink) and never invents a parallel task/run state machine.
        """
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return {"error": "agent_id required"}
        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        async with self.write_lock(f"projects:{project_id}"):
            content = target.read_text(encoding="utf-8")
            lines = content.split("\n")
            if line < 0 or line >= len(lines) or not re.match(r"\s*- \[[ xX]\] ", lines[line]):
                return {"error": "Invalid task line"}
            task_text = re.sub(r"^\s*- \[[ xX]\]\s*", "", lines[line]).strip()
            scan = scan_task_meta_block(lines, line)

            result, err = await self.try_call_app(
                "staff", "dispatch_workflow", agent_id=agent_id, input_text=task_text,
                source={"app": "projects", "project_id": project_id, "task_line": line},
            )
            if err:
                return {"error": err}
            job_id = (result or {}).get("job_id", "")
            if not job_id:
                return {"error": "dispatch failed — no job_id returned"}

            # A task carries at most one live `assigned:` line — reassigning
            # replaces it in place rather than stacking a second one.
            meta_line = f"  - assigned: {agent_id}:{job_id}"
            existing_idx = scan["by_type"].get("assigned")
            if existing_idx is not None:
                lines[existing_idx] = meta_line
            else:
                lines.insert(scan["insert_at"], meta_line)
            target.write_text("\n".join(lines), encoding="utf-8")

        await self.emit(
            "projects:task_assigned",
            {"id": project_id, "line": line, "agent_id": agent_id, "job_id": job_id},
        )
        return {"ok": True, "job_id": job_id}

    @web_route("POST", "/api/projects/{id}/tasks/{line}/assign")
    async def api_assign_task(self, request):
        project_id = request.path_params.get("id", "")
        line = int(request.path_params.get("line", -1))
        data = await self.read_json(request)
        return await self.assign_task_to_agent(project_id, line, data.get("agent_id", ""))

    async def task_assignment_status(self, project_id: str, line: int) -> dict:
        """Read a task's `assigned:` meta line and resolve live job status."""
        target = self._find_project_file(project_id)
        if not target:
            return {"assigned": False}
        content = target.read_text(encoding="utf-8")
        lines = content.split("\n")
        if line < 0 or line >= len(lines):
            return {"assigned": False}
        existing_idx = scan_task_meta_block(lines, line)["by_type"].get("assigned")
        if existing_idx is None:
            return {"assigned": False}
        raw = _META_RE.match(lines[existing_idx]).group(2).strip()
        agent_id, _, job_id = raw.partition(":")
        if not job_id:
            return {"assigned": True, "agent_id": agent_id, "status": "unknown"}
        job, err = await self.try_call_app("staff", "get_job", job_id=job_id)
        if err or not job:
            # staff absent, or the job aged out of its 200-job retention window.
            return {"assigned": True, "agent_id": agent_id, "job_id": job_id, "status": "unknown"}
        return {
            "assigned": True,
            "agent_id": agent_id,
            "job_id": job_id,
            "status": job.get("status", "unknown"),
            "result_preview": job.get("result_preview"),
            "output_path": job.get("output_path"),
            "error": job.get("error"),
        }

    @web_route("GET", "/api/projects/{id}/tasks/{line}/assignment")
    async def api_task_assignment(self, request):
        project_id = request.path_params.get("id", "")
        line = int(request.path_params.get("line", -1))
        return await self.task_assignment_status(project_id, line)

    @web_route("POST", "/api/refresh")
    async def api_refresh(self, request):
        """Rescan vault for projects."""
        projects = await self.list_projects()
        await self.emit("projects:refreshed", {"count": len(projects)})
        return {"count": len(projects)}

    # ── Mermaid Gantt visualisation (extracted to viz_scene.py) ──
    _project_scene_prompt   = _viz_scene._project_scene_prompt

    _viz_artifact_path_for  = _viz_scene._viz_artifact_path_for

    api_visualize_scene     = _viz_scene.api_visualize_scene

    @web_route("POST", "/api/projects/{id}/status")
    async def api_update_status(self, request):
        """Update project status in frontmatter."""
        project_id = request.path_params.get("id", "")
        data = await request.json()
        new_status = data.get("status", "")
        if new_status not in PROJECT_STATUSES:
            return {"error": f"Invalid status. Use: {', '.join(PROJECT_STATUSES)}"}

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        async with self.write_lock(f"projects:{project_id}"):
            content = await self.read(str(target))
            content = set_frontmatter_field(content, "status", new_status)
            await self.write(str(target), content)
        await self.emit("projects:status_changed", {"id": project_id, "status": new_status})
        return {"ok": True, "status": new_status}

    SETTABLE_FIELDS = frozenset(
        {
            "status",
            "stage",
            "deadline",
            "progress",
            "type",
            "description",
            "next_action",
            "assignees",
            "skills_required",
            "blocks",
            "blocked_by",
            # Cross-board link targets — projects accepts lists of item IDs from
            # other boards so link-record inverse maintenance can write through.
            "deliverables",
            "tasks",
            "children",
        }
    )

    async def _set_frontmatter_field(self, target: Path, field: str, value) -> bool:
        """Write one frontmatter field to a project file. Preserves other fields
        and the body. Returns True on success, False if the file can't be read.
        List-typed values render as inline YAML arrays (`[a, b, c]`)."""
        if isinstance(value, list):
            raw = "[" + ", ".join(str(v) for v in value) + "]"
        elif value is None:
            raw = ""
        else:
            raw = str(value)

        # target.stem == project_id (project main note is {id}/{id}.md), so this
        # shares the lock key with add_task_to_project + the other project-note
        # mutators — a partially-locked file is not actually serialized.
        async with self.write_lock(f"projects:{target.stem}"):
            try:
                content = await self.read(str(target))
            except OSError:
                return False
            await self.write(str(target), set_frontmatter_field(content, field, raw))
        return True

    @web_route("POST", "/api/projects/{id}/set-field")
    async def api_set_field(self, request):
        """Generic frontmatter mutator — whitelist-guarded, emits project:updated.

        Used by the boards view layer (`source = {type: "app", app: "projects"}`)
        when the user edits a project through a board. Also usable directly.
        """
        project_id = request.path_params.get("id", "")
        data = await request.json()
        field = data.get("field", "")
        value = data.get("value")

        if field not in self.SETTABLE_FIELDS:
            return {
                "error": f"field '{field}' not settable",
                "settable": sorted(self.SETTABLE_FIELDS),
            }

        if field == "status" and value not in PROJECT_STATUSES:
            return {"error": f"Invalid status. Use: {', '.join(PROJECT_STATUSES)}"}

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        ok = await self._set_frontmatter_field(target, field, value)
        if ok:
            await self.emit("project:updated", {"id": project_id, "field": field, "value": value})
            # Boards writes through this generic endpoint. Preserve the canonical
            # lifecycle event so a board move to spec-ready reaches Reactor too.
            if field == "status":
                await self.emit("projects:status_changed", {"id": project_id, "status": value})
        return {"ok": ok}

    async def list_all(self) -> list[dict]:
        """Flat list shape consumed by boards when source.type == 'app'. Thin
        wrapper over list_projects() so call_app targets a stable public method
        even if list_projects gains new filter parameters later."""
        return await self.list_projects("")

    async def list_assignments(self) -> list[dict]:
        """Return every (project, person) pair as an assignment row for the
        people app's workload index. Weight scales loosely with status —
        active projects count full; blocked/shelved a fraction; completed/archived 0."""
        _STATUS_WEIGHT = {
            "active": 5.0,
            "blocked": 2.0,
            "shelved": 1.0,
            "idea": 1.0,
            "completed": 0.0,
            "archived": 0.0,
        }
        rows: list[dict] = []
        for p in await self.list_projects(""):
            weight = _STATUS_WEIGHT.get((p.get("status") or "").lower(), 1.0)
            if weight <= 0:
                continue
            # assignees may be a list (ideal) or a csv string (legacy); tolerate both.
            raw = p.get("assignees") or p.get("assignee") or []
            if isinstance(raw, str):
                raw = [s.strip() for s in raw.split(",") if s.strip()]
            for person_id in raw:
                rows.append(
                    {
                        "person": person_id,
                        "item": {
                            "app": "projects",
                            "id": p.get("id", ""),
                            "title": p.get("name", ""),
                            "status": p.get("status", ""),
                            "deadline": p.get("deadline", ""),
                        },
                        "weight_hours": weight,
                        "role": "assignee",
                    }
                )
        return rows

    async def set_field(self, id: str, field: str, value) -> dict:
        """Plain cross-app setter — same contract as api_set_field but callable
        via call_app without constructing a fake request. Used by the boards
        view layer. Whitelist + event emission are identical."""
        if field not in self.SETTABLE_FIELDS:
            return {"error": f"field '{field}' not settable"}
        target = self._find_project_file(id)
        if not target:
            return {"error": "Project not found"}
        ok = await self._set_frontmatter_field(target, field, value)
        if ok:
            await self.emit("project:updated", {"id": id, "field": field, "value": value})
        return {"ok": ok}

    @web_route("POST", "/api/projects/{id}/tasks/toggle")
    async def api_toggle_task(self, request):
        """Toggle a task in the project file by (0-based) line number.

        Optional ``text`` in the body guards against a stale line number
        (shared with the task app's ``/task/api/toggle`` via
        ``emptyos.sdk.markdown_tasks.text_matches``) \u2014 omit it to preserve
        the historical no-guard behaviour.
        """
        project_id = request.path_params.get("id", "")
        data = await request.json()
        line_num = data.get("line", -1)
        expected_text = str(data.get("text") or "").strip()

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        async with self.write_lock(f"projects:{project_id}"):
            content = await self.read(str(target))
            lines = content.split("\n")

            if line_num < 0 or line_num >= len(lines):
                return {"error": "Invalid line number"}

            line = lines[line_num]
            if expected_text and not _mt_text_matches(line, expected_text):
                return {"error": "Task changed; refresh and try again"}

            if _mt_is_open(line):
                lines[line_num] = _mt_complete(line, date.today().isoformat())
            elif _mt_is_done(line):
                lines[line_num] = _mt_reopen(line)
            else:
                return {"error": "Line is not a task"}

            await self.write(str(target), "\n".join(lines))
        await self.emit("projects:task_toggled", {"id": project_id, "line": line_num})
        return {"ok": True, "line": line_num}

    async def add_task_to_project(
        self, project_id: str, text: str, due: str = "", done: bool = False,
        room_id: str = "",
    ) -> dict:
        """Add a task to a project file. Creates the project if it doesn't exist.

        Callable via call_app("projects", "add_task_to_project", project_id=..., text=...).
        Pass done=True to record the task as already completed (e.g. logging finished work).
        Pass room_id to attach the task back to a room - appended as the
        chat-bubble marker on the task line (see ROOM_PATTERN) so `_parse_tasks`
        and `get_all_tasks` surface it for the rooms UI to query.
        """
        text = text.strip()
        if not text:
            return {"error": "Task text required"}

        if done:
            from datetime import date as _date

            task_line = f"- [x] {text} \u2705 {_date.today().isoformat()}"
        else:
            task_line = f"- [ ] {text}"
        if due:
            task_line += f" \U0001f4c5 {due}"
        if room_id:
            task_line += f" \U0001f5e8\ufe0f {room_id}"

        # Serialize read->mutate->write on the (shared) project file -- the default
        # capture path routes every uncategorized/#dev task through the hot
        # inbox / emptyos-development files. Without this lock two concurrent
        # captures both read the pre-write content and the later write drops the
        # earlier task. emit stays OUTSIDE the lock (handlers may recurse).
        async with self.write_lock(f"projects:{project_id}"):
            target = self._find_project_file(project_id)
            if not target:
                target = self._bootstrap_project(project_id)

            content = await self.read(str(target))
            content = insert_task_line(content, task_line)
            await self.write(str(target), content)

        await self.emit("projects:task_added", {
            "id": project_id, "text": text, "done": done,
            "room_id": room_id or None,
        })
        result = {"ok": True, "task": text, "project": project_id, "done": done}
        if room_id:
            result["room_id"] = room_id
        return result

    _BOOTSTRAP = {
        "emptyos-development": {
            "name": "EmptyOS Development",
            "type": "development",
            "stage": "development",
            "tags": ["project", "emptyos", "dev"],
            "repo": "",
            "goal": "System development tasks captured from daily work",
        },
        "inbox": {
            "name": "Inbox",
            "type": "personal",
            "tags": ["project", "inbox"],
            "goal": "Uncategorized tasks — triage into projects or action directly",
        },
    }

    def _bootstrap_project(self, project_id: str) -> Path:
        """Create a standard directory project from bootstrap template or generic defaults."""
        tmpl = self._BOOTSTRAP.get(project_id, {})
        name = tmpl.get("name", project_id.replace("-", " ").title())
        ptype = tmpl.get("type", "personal")
        goal = tmpl.get("goal", "Purpose TBD")
        tags = tmpl.get("tags", ["project"])

        # Create standard directory structure
        proj_dir = self._projects_dir() / project_id
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Write main project note
        today = date.today().isoformat()
        fm_lines = ["status: active", f"created: {today}", f"type: {ptype}"]
        if tmpl.get("stage"):
            fm_lines.append(f"stage: {tmpl['stage']}")
        if tmpl.get("repo"):
            fm_lines.append(f"repo: {tmpl['repo']}")
        fm_lines.append("tags:\n  - " + "\n  - ".join(tags))

        content = (
            "---\n" + "\n".join(fm_lines) + f"\n---\n\n"
            f"# {name}\n\n> {goal}\n\n## Goal\n{goal}\n\n## Tasks\n\n## Notes\n"
        )
        target = proj_dir / f"{project_id}.md"
        target.write_text(content, encoding="utf-8")
        return target

    @web_route("POST", "/api/projects/{id}/tasks/add")
    async def api_add_task(self, request):
        """Add a task to a project file."""
        project_id = request.path_params.get("id", "")
        data = await request.json()
        return await self.add_task_to_project(project_id, data.get("text", ""), data.get("due", ""))

    @web_route("GET", "/api/projects/{id}/health")
    async def api_health(self, request):
        """AI health assessment for a project."""
        project_id = request.path_params.get("id", "")
        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        p = self._read_project(target)
        if not p:
            return {"error": "Failed to read project"}

        content = await self.read(str(target))
        # Truncate for LLM
        snippet = content[:2000]

        overdue_clause = (
            f", OVERDUE by {abs(p['days_until_deadline'])} days" if p.get("overdue") else ""
        )
        user_msg = HEALTH_USER_TMPL.format(
            name=p["name"],
            status=p["status"],
            done=p["done_tasks"],
            total=p["total_tasks"],
            progress=p["progress"],
            stale_days=p["stale_days"],
            deadline=p["deadline"] or "none",
            overdue_clause=overdue_clause,
            snippet=snippet,
        )
        try:
            result = await self.think(
                user_msg, system=HEALTH_SYSTEM, domain="text", temperature=0.3
            )
            return {"health": result, "project": p["name"], "provenance": self.last_provenance()}
        except Exception as e:
            return {"health": f"AI unavailable: {e}"}

    # Hub panels + slot contributions live in panels.py — bound below.
    panel_upcoming_deadlines = _panels.panel_upcoming_deadlines

    panel_projects_pipeline = _panels.panel_projects_pipeline

    panel_project_countdowns = _panels.panel_project_countdowns

    panel_active_projects = _panels.panel_active_projects

    slot_needs_attention = _panels.slot_needs_attention

    slot_today = _panels.slot_today

    slot_resume = _panels.slot_resume

    # ── Cross-project aggregations (extracted to aggregations.py) ──
    get_deadlines        = _aggregations.get_deadlines

    api_deadlines        = _aggregations.api_deadlines

    get_all_tasks        = _aggregations.get_all_tasks

    tasks_for_room       = _aggregations.tasks_for_room

    _iter_project_files  = _aggregations._iter_project_files

    api_all_tasks        = _aggregations.api_all_tasks

    api_tasks_for_room   = _aggregations.api_tasks_for_room

    _deadline_nudge_enabled  = _aggregations._deadline_nudge_enabled

    scheduled_deadline_nudge = _aggregations.scheduled_deadline_nudge

    @web_route("GET", "/api/type-config")
    async def api_type_config(self, request):
        """Return project type definitions and discovered tools per type."""
        tools = self._discover_tools()
        # Group tools by type
        tools_by_type: dict[str, list[dict]] = {}
        for t in tools:
            tools_by_type.setdefault(t.get("type", ""), []).append(t)
        return {
            "types": PROJECT_TYPES,
            "tools": tools_by_type,
            "features": PROJECT_FEATURES,
            "statuses": PROJECT_STATUSES,
        }

    def _discover_tools(self, project_type: str = "") -> list[dict]:
        """Find all apps that declare project-tools matching this type."""
        providers = self.kernel.apps.get_providers("project-tools")
        tools = []
        for app_id, section in providers.items():
            for tool in section.get("tools", []):
                if not project_type or tool.get("type") == project_type:
                    tools.append({**tool, "app": app_id})
        return tools

    api_update_meta = _ext.api_update_meta

    @web_route("POST", "/api/projects/{id}/stage")
    async def api_update_stage(self, request):
        """Update project stage in frontmatter."""
        project_id = request.path_params.get("id", "")
        data = await request.json()
        new_stage = data.get("stage", "")

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        # Validate stage against project type
        async with self.write_lock(f"projects:{project_id}"):
            content = await self.read(str(target))
            fm = parse_frontmatter(content)
            project_type = fm.get("type", "personal")
            type_def = PROJECT_TYPES.get(project_type, PROJECT_TYPES["personal"])
            if type_def["stages"] and new_stage not in type_def["stages"]:
                return {
                    "error": f"Invalid stage '{new_stage}' for type '{project_type}'. Valid: {type_def['stages']}"
                }

            content = set_frontmatter_field(content, "stage", new_stage)
            await self.write(str(target), content)
        await self.emit(
            "projects:stage_changed", {"id": project_id, "stage": new_stage, "type": project_type}
        )
        return {"ok": True, "stage": new_stage}

    @web_route("POST", "/api/projects/{id}/features")
    async def api_update_features(self, request):
        """Toggle a feature on/off for a project (writes to frontmatter).

        Uses flat frontmatter keys:
          features_on: sprints, code      (explicitly enable non-defaults)
          features_off: stages            (explicitly disable defaults)
        """
        project_id = request.path_params.get("id", "")
        data = await request.json()
        feature_id = data.get("feature", "")
        enabled = bool(data.get("enabled", True))

        if feature_id not in PROJECT_FEATURES:
            return {"error": f"Unknown feature: {feature_id}"}

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        # The project main note has several locked writers (add_task_to_project,
        # set_field, ...) keyed on projects:{project_id}; this read→mutate→write
        # must join the same lock or it can clobber a concurrent locked write.
        async with self.write_lock(f"projects:{project_id}"):
            content = await self.read(str(target))
            fm = parse_frontmatter(content)
            project_type = fm.get("type", "personal")
            default = project_type in PROJECT_FEATURES[feature_id].get("default_on", [])

            # Parse current on/off sets (comma-separated frontmatter strings)
            def _parse_csv_set(raw: str) -> set:
                return {s.strip() for s in str(raw or "").split(",") if s.strip()}

            on_set = _parse_csv_set(fm.get("features_on", ""))
            off_set = _parse_csv_set(fm.get("features_off", ""))

            # Apply change
            if enabled and not default:
                on_set.add(feature_id)
                off_set.discard(feature_id)
            elif not enabled and default:
                off_set.add(feature_id)
                on_set.discard(feature_id)
            else:
                # Matches default — remove any override
                on_set.discard(feature_id)
                off_set.discard(feature_id)

            # Write back to frontmatter
            new_on = ", ".join(sorted(on_set))
            new_off = ", ".join(sorted(off_set))

            if content.startswith("---"):
                fm_end = content.find("---", 3)
                if fm_end > 0:
                    fm_block = content[3:fm_end]
                    # Remove old features_on/features_off lines
                    fm_block = re.sub(r"\nfeatures_on:.*", "", fm_block)
                    fm_block = re.sub(r"\nfeatures_off:.*", "", fm_block)
                    fm_block = fm_block.rstrip() + "\n"
                    if new_on:
                        fm_block += f"features_on: {new_on}\n"
                    if new_off:
                        fm_block += f"features_off: {new_off}\n"
                    content = "---" + fm_block + "---" + content[fm_end + 3 :]
            else:
                extra = ""
                if new_on:
                    extra += f"features_on: {new_on}\n"
                if new_off:
                    extra += f"features_off: {new_off}\n"
                if extra:
                    content = f"---\n{extra}---\n" + content

            await self.write(str(target), content)
        # Re-parse to get resolved features
        fm_new = parse_frontmatter(content)
        features = self._resolve_features(project_type, fm_new)
        await self.emit(
            "projects:feature_toggled",
            {"id": project_id, "feature": feature_id, "enabled": enabled},
        )
        return {"ok": True, "feature": feature_id, "enabled": enabled, "features": features}

    api_grouped = _ext.api_grouped

    api_stats = _ext.api_stats

    api_portfolio_health = _ext.api_portfolio_health

    api_activity = _ext.api_activity

    api_timeline = _ext.api_timeline

    api_calendar = _ext.api_calendar

    api_docs = _ext.api_docs

    api_create_doc = _ext.api_create_doc

    api_calculations = _ext.api_calculations
    api_calculation_detail = _ext.api_calculation_detail

    api_dev_status = _ext.api_dev_status

    # Dev features (sprints, milestones, releases)
    api_sprints = _dev.api_sprints

    api_create_sprint = _dev.api_create_sprint

    api_close_sprint = _dev.api_close_sprint

    api_milestones = _dev.api_milestones

    api_create_milestone = _dev.api_create_milestone

    api_releases = _dev.api_releases

    api_create_release = _dev.api_create_release

    # Operations (bulk, tools, templates, structure)
    api_ready_tasks = _ops.api_ready_tasks

    api_dependency_graph = _ops.api_dependency_graph

    # Date-cascade over the dependency graph (scheduling.py).
    api_cascade_check    = _scheduling.api_cascade_check
    api_cascade_preview  = _scheduling.api_cascade_preview
    api_cascade_apply    = _scheduling.api_cascade_apply

    api_run_tool = _ops.api_run_tool

    api_bulk_tasks = _ops.api_bulk_tasks

    api_bulk_status = _ops.api_bulk_status

    api_task_meta = _ops.api_task_meta

    api_create = _ops.api_create

    api_templates = _ops.api_templates

    api_from_template = _ops.api_from_template

    api_upgrade_structure = _ops.api_upgrade_structure

    api_structure = _ops.api_structure

    # ── Workspace (standalone full-page surface — extracted to workspace.py) ──
    page_workspace      = _workspace.page_workspace
    api_overview        = _workspace.api_overview
    api_ask             = _workspace.api_ask
    api_ask_stream      = _workspace.api_ask_stream
    api_get_doc         = _workspace.api_get_doc
    api_save_doc        = _workspace.api_save_doc
    api_timeline4d      = _workspace.api_timeline4d
    api_related         = _workspace.api_related
    _project_doc_path   = _workspace._project_doc_path

    # ------------------------------------------------------------------
    # Voice Assistant contribution
    # ------------------------------------------------------------------
    async def assistant_context(self) -> str | None:
        """Contributes active project statuses to Voice Assistant."""
        try:
            projects = await self.list_projects()
            active = [p for p in projects if p.get("status") == "active"]
            if not active:
                return None

            out = "Active Projects:\n"
            for p in active[:5]:
                out += f"- {p['name']} ({p.get('done_tasks', 0)}/{p.get('total_tasks', 0)} tasks done)\n"
            return out
        except Exception:
            return None

    # ── Reading (extracted to reading.py) ──
    _resolve_features    = _reading._resolve_features
    _projects_dir        = _reading._projects_dir
    _infer_status        = _reading._infer_status
    _parse_tasks         = _reading._parse_tasks
    _days_until          = _reading._days_until
    _read_project        = _reading._read_project
    _read_dir_project    = _reading._read_dir_project
    _archive_dir         = _reading._archive_dir
    _scan_dir            = _reading._scan_dir
    _find_project_file   = _reading._find_project_file
    project_path         = _reading.project_path
    _find_project_dir    = _reading._find_project_dir
    get_project_content  = _reading.get_project_content
    load_project_context = _reading.load_project_context
    list_projects        = _reading.list_projects

