"""Projects — scan 10_Projects/ for project notes, parse frontmatter status.

Tracks status lifecycle: idea -> active -> completed -> archived
Also: blocked, shelved. Provides task counts, health assessment, CRUD.
Supports typed projects (personal, engineering, development) with stages
and tool discovery via manifest [provides.project-tools].
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from emptyos.sdk import (
    DONE_PATTERN, DUE_PATTERN, BaseApp, cli_command, compute_task_decay,
    fm_list, parse_frontmatter, set_frontmatter_field, web_route,
)

from . import dev_features as _dev
from . import extended as _ext
from . import operations as _ops
from . import panels as _panels

# --- Project Folder Standard ---
# Every project is a directory. The system creates and expects this layout.
# Flat .md files in 10_Projects/ are legacy — scanned but flagged for upgrade.

PROJECT_STRUCTURE = {
    "main":   "{id}/{id}.md",       # Main project note (frontmatter + tasks + notes)
    "docs":   "{id}/docs/",         # Specs, meeting notes, research
    "assets": "{id}/assets/",       # Attachments (images, PDFs, exports)
    "log":    "{id}/log/",          # Activity logs, changelogs, decision records
}

# --- Project Type Definitions ---

PROJECT_TYPES = {
    "personal": {
        "label": "Personal",
        "stages": [],
        "labels": {},
        "templates": ["standard", "learning", "creative", "migration"],
    },
    "engineering": {
        "label": "Engineering",
        "stages": ["concept", "design", "calculation", "review", "approval", "construction"],
        "labels": {
            "concept": "Concept", "design": "Design", "calculation": "Calculation",
            "review": "Review", "approval": "Approval", "construction": "Construction",
        },
        "templates": ["engineering", "cable-design"],
    },
    "development": {
        "label": "Development",
        "stages": ["planning", "development", "testing", "review", "release"],
        "labels": {
            "planning": "Planning", "development": "Development",
            "testing": "Testing", "review": "Review", "release": "Release",
        },
        "templates": ["development"],
    },
}

# --- Feature Registry (superset architecture) ---
# Every project gets ALL features; type defines which are ON by default.
# Per-project frontmatter `features:` dict overrides defaults.

PROJECT_FEATURES = {
    "tasks":        {"label": "Tasks",        "tab": True,  "order": 10, "default_on": ["personal", "engineering", "development"]},
    "docs":         {"label": "Docs",         "tab": True,  "order": 20, "default_on": ["personal", "engineering", "development"]},
    "stages":       {"label": "Stages",       "tab": False, "order": 5,  "default_on": ["engineering", "development"]},
    "code":         {"label": "Code",         "tab": True,  "order": 30, "default_on": ["development"]},
    "sprints":      {"label": "Sprints",      "tab": True,  "order": 25, "default_on": ["development"]},
    "milestones":   {"label": "Milestones",   "tab": True,  "order": 35, "default_on": ["development"]},
    "releases":     {"label": "Releases",     "tab": True,  "order": 40, "default_on": ["development"]},
    "tools":        {"label": "Tools",        "tab": True,  "order": 50, "default_on": ["engineering"]},
    "calculations": {"label": "Calculations", "tab": True,  "order": 55, "default_on": ["engineering"]},
}

# Task metadata prefixes (indented lines under a task)
META_PREFIXES = {"info", "need", "calc", "ref", "depends_on", "blocks", "sprint", "milestone"}
_META_RE = re.compile(r"\s+- (" + "|".join(META_PREFIXES) + r"):\s*(.+)")


class ProjectsApp(BaseApp):

    def _resolve_features(self, project_type: str, fm: dict | None = None) -> dict[str, bool]:
        """Resolve which features are enabled for a project.

        Defaults come from PROJECT_FEATURES[feat].default_on per type.
        Per-project frontmatter overrides via:
          features_on: sprints, code, milestones   (enable non-defaults)
          features_off: stages, docs               (disable defaults)
        """
        fm = fm or {}
        on_set = set(fm_list(fm, "features_on"))
        off_set = set(fm_list(fm, "features_off"))

        result = {}
        for feat_id, feat_def in PROJECT_FEATURES.items():
            default = project_type in feat_def.get("default_on", [])
            if feat_id in on_set:
                result[feat_id] = True
            elif feat_id in off_set:
                result[feat_id] = False
            else:
                result[feat_id] = default
        return result

    def _projects_dir(self) -> Path:
        return self.vault_config_path("projects_dir", "10_Projects") or Path(".")

    def _infer_status(self, fm: dict, content: str, mtime_days: int) -> str:
        """Infer project status from frontmatter, content, and modification time."""
        # 1. Explicit frontmatter status
        status = fm.get("status", "").lower().strip()
        if status in ("idea", "active", "blocked", "shelved", "completed", "archived"):
            return status

        # 2. Keyword scan
        lower = content.lower()
        if any(w in lower for w in ("completed", "done", "finished")):
            return "completed"

        # 3. Stale detection
        if mtime_days > 90:
            return "shelved"

        # 4. Default
        return "active"

    def _parse_tasks(self, content: str) -> tuple[int, int, list[dict]]:
        """Parse tasks from content, including indented metadata lines.

        Metadata lines are indented lines starting with ``- key:`` where key
        is one of info/need/calc/ref.  They are attached to the preceding task.
        """
        open_tasks = 0
        done_tasks = 0
        task_list: list[dict] = []
        lines = content.split("\n")
        for i, line in enumerate(lines):
            m_open = re.match(r"\s*- \[ \] (.+)", line)
            m_done = re.match(r"\s*- \[x\] (.+)", line, re.IGNORECASE)
            if m_open:
                open_tasks += 1
                task_list.append({"text": m_open.group(1).strip(), "done": False, "line": i, "meta": []})
            elif m_done:
                done_tasks += 1
                task_list.append({"text": m_done.group(1).strip(), "done": True, "line": i, "meta": []})
            elif task_list:
                # Check for indented metadata under the last task
                m_meta = _META_RE.match(line)
                if m_meta:
                    task_list[-1]["meta"].append({
                        "type": m_meta.group(1),
                        "value": m_meta.group(2).strip(),
                        "line": i,
                    })
        return open_tasks, done_tasks, task_list

    @staticmethod
    def _resolve_dependencies(task_list: list[dict]) -> list[dict]:
        """Resolve depends_on/blocks references between tasks.

        Matches by case-insensitive substring of task text.
        Annotates each task with: depends_on, blocks, ready, blocked_by.
        """
        # Build lookup: normalized text -> task index
        text_map: dict[str, int] = {}
        for idx, t in enumerate(task_list):
            text_map[t["text"].lower().strip()] = idx

        def _find_task(ref: str) -> int | None:
            ref_lower = ref.strip().lower()
            # Exact match first
            if ref_lower in text_map:
                return text_map[ref_lower]
            # Substring match
            for text, idx in text_map.items():
                if ref_lower in text or text in ref_lower:
                    return idx
            return None

        # Parse dependency metadata into structured refs
        for t in task_list:
            t["depends_on"] = []
            t["blocks"] = []
            t["blocked_by"] = []
            for m in t.get("meta", []):
                if m["type"] == "depends_on":
                    for ref in m["value"].split(","):
                        ref = ref.strip()
                        if not ref:
                            continue
                        target = _find_task(ref)
                        if target is not None:
                            t["depends_on"].append({
                                "text": task_list[target]["text"],
                                "line": task_list[target]["line"],
                                "done": task_list[target]["done"],
                            })
                        else:
                            t["depends_on"].append({"text": ref, "line": -1, "done": False})
                elif m["type"] == "blocks":
                    for ref in m["value"].split(","):
                        ref = ref.strip()
                        if not ref:
                            continue
                        target = _find_task(ref)
                        if target is not None:
                            t["blocks"].append({
                                "text": task_list[target]["text"],
                                "line": task_list[target]["line"],
                            })

        # Compute ready/blocked_by: a task is ready if all depends_on are done
        for t in task_list:
            if t["done"]:
                t["ready"] = True
                continue
            unmet = [d for d in t["depends_on"] if not d["done"]]
            if unmet:
                t["ready"] = False
                t["blocked_by"] = [d["text"] for d in unmet]
            else:
                t["ready"] = True

        return task_list

    def _days_until(self, date_str: str) -> int | None:
        """Days until deadline. Negative = overdue."""
        if not date_str:
            return None
        try:
            d = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
            return (d - date.today()).days
        except ValueError:
            return None

    def _read_project(self, f: Path) -> dict | None:
        """Read and parse a single project file."""
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            return None
        fm = parse_frontmatter(content)

        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            mtime_days = (datetime.now() - mtime).days
        except Exception:
            mtime_days = 0

        open_tasks, done_tasks, task_list = self._parse_tasks(content)
        status = self._infer_status(fm, content, mtime_days)
        deadline = fm.get("deadline", "")
        days_until = self._days_until(deadline)

        project_type = fm.get("type", "personal")
        stage = fm.get("stage", "")
        type_def = PROJECT_TYPES.get(project_type, PROJECT_TYPES["personal"])
        stages = type_def["stages"]
        features = self._resolve_features(project_type, fm)

        return {
            "id": f.stem,
            "file": f.name,
            "name": f.stem.replace("-", " "),
            "status": status,
            "type": project_type,
            "stage": stage,
            "stage_index": stages.index(stage) if stage in stages else -1,
            "stage_total": len(stages),
            "created": fm.get("created", ""),
            "deadline": deadline,
            "tags": fm.get("tags", ""),
            "repo": fm.get("repo", ""),
            "open_tasks": open_tasks,
            "done_tasks": done_tasks,
            "total_tasks": open_tasks + done_tasks,
            "progress": round(done_tasks / (open_tasks + done_tasks) * 100) if (open_tasks + done_tasks) > 0 else 0,
            "days_until_deadline": days_until,
            "overdue": days_until is not None and days_until < 0,
            "stale_days": mtime_days,
            "is_directory": f.parent not in (self._projects_dir(), self._archive_dir()),
            "features": features,
        }

    def _read_dir_project(self, d: Path) -> dict | None:
        """Read a directory-based project (folder with README.md or index note)."""
        # Skip excluded dirs
        if d.name in (".space", "__pycache__", "node_modules", ".git", ".firebase", ".pytest_cache"):
            return None
        # Find the main note
        readme = None
        for candidate in (d / "README.md", d / f"{d.name}.md", d / "index.md"):
            if candidate.exists():
                readme = candidate
                break
        if readme is None:
            # Check if any .md file exists
            mds = list(d.glob("*.md"))
            if mds:
                readme = mds[0]
            else:
                # No markdown — use folder metadata only
                try:
                    mtime = datetime.fromtimestamp(d.stat().st_mtime)
                    mtime_days = (datetime.now() - mtime).days
                except Exception:
                    mtime_days = 0
                return {
                    "id": d.name,
                    "file": d.name + "/",
                    "name": d.name.replace("-", " ").replace("_", " "),
                    "status": "archived" if mtime_days > 180 else "shelved" if mtime_days > 90 else "active",
                    "type": "personal", "stage": "", "stage_index": -1, "stage_total": 0,
                    "created": "", "deadline": "", "tags": "", "repo": "",
                    "open_tasks": 0, "done_tasks": 0, "total_tasks": 0,
                    "progress": 0, "days_until_deadline": None, "overdue": False,
                    "stale_days": mtime_days, "is_directory": True,
                    "features": self._resolve_features("personal"),
                }
        return self._read_project(readme)

    def _archive_dir(self) -> Path:
        return self.vault_config_path("archive_dir", "40_Archive/10_Projects") or self.vault_root / "40_Archive" / "10_Projects"

    def _scan_dir(self, directory: Path, seen_ids: set, status_filter: str, force_status: str = "") -> list[dict]:
        """Scan a directory for projects (.md files and subdirectories)."""
        if not directory.exists():
            return []
        results = []
        # Scan .md files
        for f in sorted(directory.glob("*.md")):
            if f.name.startswith("_"):
                continue
            p = self._read_project(f)
            if p is None:
                continue
            if force_status:
                p["status"] = force_status
            if p["id"] in seen_ids:
                continue
            if status_filter and p["status"] != status_filter:
                continue
            results.append(p)
            seen_ids.add(p["id"])
        # Scan directories
        for d in sorted(directory.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
                continue
            if d.name in seen_ids:
                continue
            p = self._read_dir_project(d)
            if p is None:
                continue
            if force_status:
                p["status"] = force_status
            if status_filter and p["status"] != status_filter:
                continue
            results.append(p)
            seen_ids.add(p["id"])
        return results

    def _find_project_file(self, project_id: str) -> Path | None:
        """Find a project file by ID (stem), case-insensitive.

        Searches: flat files ({id}.md), directory projects ({id}/{id}.md), and
        common variants (README.md, index.md) inside project directories.
        Checks both active and archive directories.
        """
        pid_lower = project_id.lower()
        for search_dir in (self._projects_dir(), self._archive_dir()):
            if not search_dir.exists():
                continue
            # 1. Flat file
            target = search_dir / f"{project_id}.md"
            if target.exists():
                return target
            # 2. Directory project: {id}/{id}.md
            dir_target = search_dir / project_id / f"{project_id}.md"
            if dir_target.exists():
                return dir_target
            # 3. Case-insensitive flat file
            for f in search_dir.glob("*.md"):
                if f.stem.lower() == pid_lower:
                    return f
            # 4. Case-insensitive directory project
            for d in search_dir.iterdir():
                if d.is_dir() and d.name.lower() == pid_lower:
                    main = d / f"{d.name}.md"
                    if main.exists():
                        return main
                    for name in ("README.md", "index.md"):
                        alt = d / name
                        if alt.exists():
                            return alt
                    mds = list(d.glob("*.md"))
                    if mds:
                        return mds[0]
        return None

    def _find_project_dir(self, project_id: str) -> Path | None:
        """Find a project directory by ID. Returns None for file-only projects."""
        for search_dir in (self._projects_dir(), self._archive_dir()):
            if not search_dir.exists():
                continue
            d = search_dir / project_id
            if d.is_dir():
                return d
            for entry in search_dir.iterdir():
                if entry.is_dir() and entry.name.lower() == project_id.lower():
                    return entry
        return None

    async def get_project_content(self, project_id: str) -> dict:
        """Raw content of a project's main note. Returns {id, path, content}.

        Used by cross-app consumers (tracker, timeline) that need to read
        the body of specific projects without scanning the 10_Projects folder
        themselves. Honors directory and flat-file layouts.
        """
        f = self._find_project_file(project_id)
        if not f or not f.exists():
            return {"id": project_id, "path": "", "content": ""}
        try:
            content = await self.read(str(f))
        except Exception:
            content = ""
        return {"id": project_id, "path": str(f), "content": content}

    async def list_projects(self, status_filter: str = "") -> list[dict]:
        seen_ids: set = set()
        # 1. Active projects from 10_Projects/
        results = self._scan_dir(self._projects_dir(), seen_ids, status_filter)
        # 2. Archived projects from 40_Archive/10_Projects/ (force status=archived)
        results += self._scan_dir(self._archive_dir(), seen_ids, status_filter, force_status="archived")
        return results

    @cli_command("projects", help="List and filter projects")
    async def cmd_projects(self, action: str = "list", status: str = ""):
        projects = await self.list_projects(status)
        if not projects:
            print("  No projects found")
            return
        for p in projects:
            tasks = f"[{p['done_tasks']}/{p['total_tasks']}]" if p['total_tasks'] > 0 else ""
            dl = f" (due {p['deadline']})" if p.get('deadline') else ""
            print(f"  {p['status']:<12} {p['name']:<35} {tasks}{dl}")

    # --- Web API ---

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

        content = target.read_text(encoding="utf-8")
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
            reports = await self.call_app("reports", "list_for_project", project_id=project_id) or []
        except Exception:
            reports = []

        return {**p, "tasks": task_list, "goal": goal.strip(),
                "has_dependencies": has_deps, "ready_count": ready_count, "blocked_count": blocked_count,
                "reports": reports}

    @web_route("POST", "/api/refresh")
    async def api_refresh(self, request):
        """Rescan vault for projects."""
        projects = await self.list_projects()
        await self.emit("projects:refreshed", {"count": len(projects)})
        return {"count": len(projects)}

    @web_route("POST", "/api/projects/{id}/status")
    async def api_update_status(self, request):
        """Update project status in frontmatter."""
        project_id = request.path_params.get("id", "")
        data = await request.json()
        new_status = data.get("status", "")
        if new_status not in ("idea", "active", "blocked", "shelved", "completed"):
            return {"error": "Invalid status. Use: idea, active, blocked, shelved, completed"}

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        content = await self.read(str(target))
        content = set_frontmatter_field(content, "status", new_status)
        await self.write(str(target), content)
        await self.emit("projects:status_changed", {"id": project_id, "status": new_status})
        return {"ok": True, "status": new_status}

    # ── Generic frontmatter setter + flat-list API (used by boards view layer) ──

    SETTABLE_FIELDS = frozenset({
        "status", "stage", "deadline", "progress", "type", "description",
        "assignees", "skills_required", "blocks", "blocked_by",
        # Cross-board link targets — projects accepts lists of item IDs from
        # other boards so link-record inverse maintenance can write through.
        "deliverables", "tasks", "children",
    })

    async def _set_frontmatter_field(self, target: Path, field: str, value) -> bool:
        """Write one frontmatter field to a project file. Preserves other fields
        and the body. Returns True on success, False if the file can't be read.
        List-typed values render as inline YAML arrays (`[a, b, c]`)."""
        try:
            content = await self.read(str(target))
        except OSError:
            return False

        if isinstance(value, list):
            raw = "[" + ", ".join(str(v) for v in value) + "]"
        elif value is None:
            raw = ""
        else:
            raw = str(value)

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
            return {"error": f"field '{field}' not settable", "settable": sorted(self.SETTABLE_FIELDS)}

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        ok = await self._set_frontmatter_field(target, field, value)
        if ok:
            await self.emit("project:updated",
                            {"id": project_id, "field": field, "value": value})
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
            "active": 5.0, "blocked": 2.0, "shelved": 1.0,
            "idea": 1.0, "completed": 0.0, "archived": 0.0,
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
                rows.append({
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
                })
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
        """Toggle a task in the project file by line number."""
        project_id = request.path_params.get("id", "")
        data = await request.json()
        line_num = data.get("line", -1)

        target = self._find_project_file(project_id)
        if not target:
            return {"error": "Project not found"}

        content = target.read_text(encoding="utf-8")
        lines = content.split("\n")

        if line_num < 0 or line_num >= len(lines):
            return {"error": "Invalid line number"}

        line = lines[line_num]
        if "- [ ] " in line:
            today = date.today().isoformat()
            lines[line_num] = line.replace("- [ ] ", f"- [x] ", 1)
            # Add completion date if not present
            if "\u2705" not in lines[line_num]:
                lines[line_num] = lines[line_num].rstrip() + f" \u2705 {today}"
        elif "- [x] " in line.lower():
            lines[line_num] = re.sub(r"- \[[xX]\] ", "- [ ] ", line, count=1)
            # Remove completion date
            lines[line_num] = re.sub(r"\s*\u2705\s*\d{4}-\d{2}-\d{2}", "", lines[line_num])
        else:
            return {"error": "Line is not a task"}

        target.write_text("\n".join(lines), encoding="utf-8")
        await self.emit("projects:task_toggled", {"id": project_id, "line": line_num})
        return {"ok": True, "line": line_num}

    async def add_task_to_project(self, project_id: str, text: str, due: str = "", done: bool = False) -> dict:
        """Add a task to a project file. Creates the project if it doesn't exist.

        Callable via call_app("projects", "add_task_to_project", project_id=..., text=...).
        Pass done=True to record the task as already completed (e.g. logging finished work).
        """
        text = text.strip()
        if not text:
            return {"error": "Task text required"}

        target = self._find_project_file(project_id)
        if not target:
            target = self._bootstrap_project(project_id)

        if done:
            from datetime import date as _date
            task_line = f"- [x] {text} \u2705 {_date.today().isoformat()}"
        else:
            task_line = f"- [ ] {text}"
        if due:
            task_line += f" \U0001f4c5 {due}"

        content = target.read_text(encoding="utf-8")

        # Find Tasks section
        if "## Tasks" in content:
            idx = content.index("## Tasks")
            end_of_heading = content.index("\n", idx)
            # Find end of tasks section (next heading or end of file)
            next_section = content.find("\n## ", end_of_heading + 1)
            if next_section > 0:
                content = content[:next_section] + task_line + "\n" + content[next_section:]
            else:
                content = content.rstrip() + "\n" + task_line + "\n"
        else:
            content = content.rstrip() + "\n\n## Tasks\n" + task_line + "\n"

        target.write_text(content, encoding="utf-8")
        await self.emit("projects:task_added", {"id": project_id, "text": text, "done": done})
        return {"ok": True, "task": text, "project": project_id, "done": done}

    # --- Bootstrap templates for auto-created projects ---

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
        for subdir in ("docs", "assets", "log"):
            (proj_dir / subdir).mkdir(exist_ok=True)

        # Write main project note
        today = date.today().isoformat()
        fm_lines = [f"status: active", f"created: {today}", f"type: {ptype}"]
        if tmpl.get("stage"):
            fm_lines.append(f"stage: {tmpl['stage']}")
        if tmpl.get("repo"):
            fm_lines.append(f"repo: {tmpl['repo']}")
        fm_lines.append("tags:\n  - " + "\n  - ".join(tags))

        content = (
            f"---\n" + "\n".join(fm_lines) + f"\n---\n\n"
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
        return await self.add_task_to_project(
            project_id, data.get("text", ""), data.get("due", "")
        )

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

        content = target.read_text(encoding="utf-8")
        # Truncate for LLM
        snippet = content[:2000]

        prompt = (
            f"Assess this project's health. Project: {p['name']}\n"
            f"Status: {p['status']}, Tasks: {p['done_tasks']}/{p['total_tasks']} done, "
            f"Progress: {p['progress']}%, Stale: {p['stale_days']}d since edit\n"
            f"Deadline: {p['deadline'] or 'none'}"
            f"{', OVERDUE by ' + str(abs(p['days_until_deadline'])) + ' days' if p.get('overdue') else ''}\n\n"
            f"Content preview:\n{snippet}\n\n"
            "Give: 1) Health rating (healthy/at-risk/critical), 2) One-line diagnosis, "
            "3) Top recommended action. Be concise (3 lines max)."
        )
        try:
            result = await self.think(prompt, domain="text")
            return {"health": result, "project": p["name"], "provenance": self.last_provenance()}
        except Exception as e:
            return {"health": f"AI unavailable: {e}"}

    # ------------------------------------------------------------------
    # Cross-project query APIs (decoupling layer)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hub panel contributions — manifest: [[contributes.hub.panel]]
    # ------------------------------------------------------------------

    # Hub panels + slot contributions live in panels.py — bound below.
    panel_upcoming_deadlines = _panels.panel_upcoming_deadlines
    panel_projects_pipeline = _panels.panel_projects_pipeline
    panel_project_countdowns = _panels.panel_project_countdowns
    slot_needs_attention = _panels.slot_needs_attention
    slot_today = _panels.slot_today
    slot_resume = _panels.slot_resume

    async def get_deadlines(self, days: int = 90, overdue_days: int = 7) -> list[dict]:
        """Projects with deadlines in a time window."""
        projects = await self.list_projects()
        results = []
        for p in projects:
            dl = p.get("deadline")
            if not dl:
                continue
            days_left = p.get("days_until_deadline")
            if days_left is None:
                continue
            if -overdue_days <= days_left <= days:
                results.append({
                    "id": p["id"],
                    "name": p["name"],
                    "deadline": dl,
                    "days_left": days_left,
                    "overdue": days_left < 0,
                    "status": p["status"],
                    "progress": p["progress"],
                })
        results.sort(key=lambda x: x["days_left"])
        return results

    @web_route("GET", "/api/deadlines")
    async def api_deadlines(self, request):
        """Projects with deadlines in a time window. Used by briefing, hub."""
        days = int(request.query_params.get("days", "90"))
        overdue_days = int(request.query_params.get("overdue_days", "7"))
        return await self.get_deadlines(days, overdue_days)

    async def get_all_tasks(self, status_filter: str = "") -> list[dict]:
        """All tasks across all projects."""
        vault = self.vault_root
        today = date.today()
        all_tasks = []
        # Scan project files directly (single read per file, no N+1)
        for search_dir in (self._projects_dir(), self._archive_dir()):
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
                rel_path = str(f.relative_to(vault))
                for t in task_list:
                    due_m = DUE_PATTERN.search(t["text"])
                    due_str = due_m.group(1) if due_m else ""
                    done_m = DONE_PATTERN.search(t["text"])
                    done_date = done_m.group(1) if done_m else ""
                    overdue_days, tier = compute_task_decay(due_str, today) if due_str and not t["done"] else (0, "fresh")
                    all_tasks.append({
                        "text": t["text"],
                        "done": t["done"],
                        "file": rel_path,
                        "line": t["line"] + 1,
                        "due": due_str,
                        "done_date": done_date,
                        "overdue_days": overdue_days,
                        "tier": tier,
                        "project": project_id,
                    })
        return all_tasks

    def _iter_project_files(self, directory: Path):
        """Yield main .md file for each project in a directory."""
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_file() and entry.suffix == ".md":
                yield entry
            elif entry.is_dir():
                # Directory project: {id}/{id}.md or first .md
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
        return await self.get_all_tasks(status_filter)

    # ------------------------------------------------------------------
    # Type & Stage System
    # ------------------------------------------------------------------

    @web_route("GET", "/api/type-config")
    async def api_type_config(self, request):
        """Return project type definitions and discovered tools per type."""
        tools = self._discover_tools()
        # Group tools by type
        tools_by_type: dict[str, list[dict]] = {}
        for t in tools:
            tools_by_type.setdefault(t.get("type", ""), []).append(t)
        return {"types": PROJECT_TYPES, "tools": tools_by_type, "features": PROJECT_FEATURES}

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
        content = await self.read(str(target))
        fm = parse_frontmatter(content)
        project_type = fm.get("type", "personal")
        type_def = PROJECT_TYPES.get(project_type, PROJECT_TYPES["personal"])
        if type_def["stages"] and new_stage not in type_def["stages"]:
            return {"error": f"Invalid stage '{new_stage}' for type '{project_type}'. Valid: {type_def['stages']}"}

        content = set_frontmatter_field(content, "stage", new_stage)
        await self.write(str(target), content)
        await self.emit("projects:stage_changed", {"id": project_id, "stage": new_stage, "type": project_type})
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

        content = target.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        project_type = fm.get("type", "personal")
        default = project_type in PROJECT_FEATURES[feature_id].get("default_on", [])

        # Parse current on/off sets
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
                content = "---" + fm_block + "---" + content[fm_end + 3:]
        else:
            extra = ""
            if new_on:
                extra += f"features_on: {new_on}\n"
            if new_off:
                extra += f"features_off: {new_off}\n"
            if extra:
                content = f"---\n{extra}---\n" + content

        target.write_text(content, encoding="utf-8")
        # Re-parse to get resolved features
        fm_new = parse_frontmatter(content)
        features = self._resolve_features(project_type, fm_new)
        await self.emit("projects:feature_toggled", {"id": project_id, "feature": feature_id, "enabled": enabled})
        return {"ok": True, "feature": feature_id, "enabled": enabled, "features": features}

    # ------------------------------------------------------------------
    # Dependency Queries
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Extended features — extracted to modules
    # ------------------------------------------------------------------

    api_grouped = _ext.api_grouped
    api_stats = _ext.api_stats
    api_portfolio_health = _ext.api_portfolio_health
    api_activity = _ext.api_activity
    api_timeline = _ext.api_timeline
    api_calendar = _ext.api_calendar
    api_docs = _ext.api_docs
    api_create_doc = _ext.api_create_doc
    api_calculations = _ext.api_calculations
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
    api_run_tool = _ops.api_run_tool
    api_bulk_tasks = _ops.api_bulk_tasks
    api_bulk_status = _ops.api_bulk_status
    api_task_meta = _ops.api_task_meta
    api_create = _ops.api_create
    api_templates = _ops.api_templates
    api_from_template = _ops.api_from_template
    api_upgrade_structure = _ops.api_upgrade_structure
    api_structure = _ops.api_structure

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


# --- Helpers (outside class) ---

class _FakeRequest:
    """Minimal request object for call_app to web_route methods."""

    def __init__(self, data: dict):
        self._data = data
        self.query_params = data
        self.path_params = data

    async def json(self):
        return self._data
