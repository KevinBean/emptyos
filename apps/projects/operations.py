"""Projects — bulk operations, tool integration, structure management, templates.

Extracted from app.py to keep the core under 800 lines (P4 Atomic).
Methods are bound to ProjectsApp via attribute assignment.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk import set_frontmatter_field, web_route

from . import app as _core

# ------------------------------------------------------------------
# Ready Tasks & Dependency Graph
# ------------------------------------------------------------------


@web_route("GET", "/api/projects/{id}/ready-tasks")
async def api_ready_tasks(self, request):
    """Return open tasks with all dependencies satisfied."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}
    content = target.read_text(encoding="utf-8")
    _, _, task_list = self._parse_tasks(content)
    task_list = self._resolve_dependencies(task_list)
    ready = [t for t in task_list if t["ready"] and not t["done"]]
    return {"ready_tasks": ready, "total_ready": len(ready)}


@web_route("GET", "/api/projects/{id}/dependency-graph")
async def api_dependency_graph(self, request):
    """Return dependency graph as nodes + edges for visualization."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}
    content = target.read_text(encoding="utf-8")
    _, _, task_list = self._parse_tasks(content)
    task_list = self._resolve_dependencies(task_list)

    nodes = []
    edges = []
    for i, t in enumerate(task_list):
        nodes.append(
            {
                "id": i,
                "text": t["text"],
                "done": t["done"],
                "ready": t["ready"],
                "line": t["line"],
            }
        )
        for dep in t["depends_on"]:
            for j, src in enumerate(task_list):
                if src["line"] == dep["line"] and dep["line"] >= 0:
                    edges.append({"from": j, "to": i, "type": "depends_on"})
                    break
        for blk in t["blocks"]:
            for j, tgt in enumerate(task_list):
                if tgt["line"] == blk["line"]:
                    edges.append({"from": i, "to": j, "type": "blocks"})
                    break

    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------
# Tool Integration (manifest-driven discovery)
# ------------------------------------------------------------------


@web_route("POST", "/api/projects/{id}/run-tool")
async def api_run_tool(self, request):
    """Run a discovered project tool and optionally attach result to a task."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    app_id = data.get("app", "")
    method = data.get("method", "")
    params = data.get("params", {})
    task_line = data.get("task_line")

    if not app_id or not method:
        return {"error": "app and method required"}

    providers = self.kernel.apps.get_providers("project-tools")
    declared = providers.get(app_id, {}).get("tools", [])
    if not any(t["method"] == method for t in declared):
        return {"error": f"Tool method '{method}' not declared by app '{app_id}'"}

    try:
        result = await self.call_app(app_id, method, request=_core._FakeRequest(params))
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}

    calcs_dir = Path(self.data_dir) / "calcs" / project_id
    calcs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result_file = calcs_dir / f"{timestamp}-{app_id}-{method}.json"
    result_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    if task_line is not None:
        target = self._find_project_file(project_id)
        if target:
            content = target.read_text(encoding="utf-8")
            lines = content.split("\n")
            if 0 <= task_line < len(lines):
                meta_line = f"  - calc: {app_id}/{method} \u2192 {result_file.name}"
                lines.insert(task_line + 1, meta_line)
                target.write_text("\n".join(lines), encoding="utf-8")

    await self.emit(
        "projects:calc_attached",
        {
            "project": project_id,
            "app": app_id,
            "method": method,
            "result_file": result_file.name,
        },
    )
    return {"ok": True, "result": result, "saved": result_file.name}


# ------------------------------------------------------------------
# Bulk Operations
# ------------------------------------------------------------------


@web_route("POST", "/api/projects/{id}/tasks/bulk")
async def api_bulk_tasks(self, request):
    """Bulk task operations: complete, delete."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    action = data.get("action", "")
    lines = sorted(data.get("lines", []), reverse=True)

    if action not in ("complete", "delete"):
        return {"error": "action must be 'complete' or 'delete'"}
    if not lines:
        return {"error": "lines required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    file_lines = content.split("\n")
    today = date.today().isoformat()
    changed = 0

    for line_num in lines:
        if line_num < 0 or line_num >= len(file_lines):
            continue
        line = file_lines[line_num]
        if action == "complete" and "- [ ] " in line:
            file_lines[line_num] = line.replace("- [ ] ", "- [x] ", 1)
            if "\u2705" not in file_lines[line_num]:
                file_lines[line_num] = file_lines[line_num].rstrip() + f" \u2705 {today}"
            changed += 1
        elif action == "delete" and re.match(r"\s*- \[[ xX]\] ", line):
            file_lines[line_num] = None
            changed += 1

    if action == "delete":
        file_lines = [l for l in file_lines if l is not None]

    target.write_text("\n".join(file_lines), encoding="utf-8")
    return {"ok": True, "action": action, "changed": changed}


@web_route("POST", "/api/bulk-status")
async def api_bulk_status(self, request):
    """Change status on multiple projects at once."""
    data = await request.json()
    ids = data.get("ids", [])
    new_status = data.get("status", "")
    if new_status not in ("idea", "active", "blocked", "shelved", "completed"):
        return {"error": "Invalid status"}
    if not ids:
        return {"error": "ids required"}

    changed = 0
    for pid in ids:
        target = self._find_project_file(pid)
        if not target:
            continue
        content = await self.read(str(target))
        content = set_frontmatter_field(content, "status", new_status)
        await self.write(str(target), content)
        changed += 1

    if changed:
        await self.emit("projects:status_changed", {"ids": ids, "status": new_status})
    return {"ok": True, "changed": changed}


# ------------------------------------------------------------------
# Task Metadata
# ------------------------------------------------------------------


@web_route("POST", "/api/projects/{id}/tasks/{line}/meta")
async def api_task_meta(self, request):
    """Add or update metadata on a task."""
    project_id = request.path_params.get("id", "")
    line_num = int(request.path_params.get("line", -1))
    data = await request.json()
    meta_type = data.get("type", "")
    value = data.get("value", "").strip()

    if meta_type not in _core.META_PREFIXES:
        return {"error": f"Invalid meta type. Use: {', '.join(_core.META_PREFIXES)}"}
    if not value:
        return {"error": "value required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    lines = content.split("\n")

    if line_num < 0 or line_num >= len(lines):
        return {"error": "Invalid line number"}

    if not re.match(r"\s*- \[[ xX]\] ", lines[line_num]):
        return {"error": "Line is not a task"}

    insert_at = line_num + 1
    while insert_at < len(lines) and _core._META_RE.match(lines[insert_at]):
        insert_at += 1

    meta_line = f"  - {meta_type}: {value}"
    lines.insert(insert_at, meta_line)
    target.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "line": insert_at, "meta": {"type": meta_type, "value": value}}


# ------------------------------------------------------------------
# Create & Templates
# ------------------------------------------------------------------


@web_route("POST", "/api/create")
async def api_create(self, request):
    """Create a new project with standard directory structure."""
    data = await request.json()
    name = data.get("name", "").strip()
    goal = data.get("goal", "").strip()
    status = data.get("status", "idea")
    deadline = data.get("deadline", "")
    project_type = data.get("type", "personal")
    repo = data.get("repo", "")

    if not name:
        return {"error": "Project name required"}

    project_id = name.replace(" ", "-")
    proj_dir = self._projects_dir() / project_id
    if proj_dir.exists() or (self._projects_dir() / f"{project_id}.md").exists():
        return {"error": "Project already exists"}

    proj_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("docs", "assets", "log"):
        (proj_dir / subdir).mkdir(exist_ok=True)

    type_def = _core.PROJECT_TYPES.get(project_type, _core.PROJECT_TYPES["personal"])
    initial_stage = type_def["stages"][0] if type_def["stages"] else ""

    today = date.today().isoformat()
    fm_lines = [f"status: {status}", f"created: {today}"]
    if project_type != "personal":
        fm_lines.append(f"type: {project_type}")
    if initial_stage:
        fm_lines.append(f"stage: {initial_stage}")
    if deadline:
        fm_lines.append(f"deadline: {deadline}")
    if repo:
        fm_lines.append(f"repo: {repo}")
    fm_lines.append("tags:\n  - project")

    fm_block = "\n".join(fm_lines)
    content = f"---\n{fm_block}\n---\n\n# {name}\n\n> {goal or 'Purpose TBD'}\n\n## Goal\n{goal or 'Define success criteria here.'}\n\n## Tasks\n- [ ] Define project scope\n\n## Notes\n"
    target = proj_dir / f"{project_id}.md"
    target.write_text(content, encoding="utf-8")
    await self.emit("projects:created", {"name": name, "status": status, "type": project_type})
    return {"ok": True, "file": target.name, "id": project_id}


@web_route("GET", "/api/templates")
async def api_templates(self, request):
    """Project templates for quick creation."""
    return [
        {
            "id": "standard",
            "name": "Standard Project",
            "type": "personal",
            "goal": "Define success criteria",
            "tasks": ["Define scope", "Research", "Implement", "Review", "Ship"],
        },
        {
            "id": "learning",
            "name": "Learning Project",
            "type": "personal",
            "goal": "Master a new skill",
            "tasks": [
                "Find resources",
                "Study fundamentals",
                "Practice exercises",
                "Build a project",
                "Teach someone",
            ],
        },
        {
            "id": "creative",
            "name": "Creative Project",
            "type": "personal",
            "goal": "Create something",
            "tasks": [
                "Brainstorm ideas",
                "Draft outline",
                "First draft",
                "Revise",
                "Publish/Share",
            ],
        },
        {
            "id": "migration",
            "name": "Migration/Transition",
            "type": "personal",
            "goal": "Move from A to B",
            "tasks": [
                "Audit current state",
                "Plan migration",
                "Build new",
                "Test",
                "Cut over",
                "Decommission old",
            ],
        },
        {
            "id": "engineering",
            "name": "Engineering Project",
            "type": "engineering",
            "goal": "Design and deliver engineering solution",
            "tasks": [
                "Define requirements",
                "Preliminary design",
                "Run calculations",
                "Peer review",
                "Submit for approval",
                "Construction support",
            ],
        },
        {
            "id": "cable-design",
            "name": "Cable Design",
            "type": "engineering",
            "goal": "Complete cable system design",
            "tasks": [
                "Cable sizing calculation",
                "Route survey",
                "Pulling tension analysis",
                "Sheath voltage study",
                "ECC design",
                "Installation specification",
            ],
        },
        {
            "id": "development",
            "name": "Software Development",
            "type": "development",
            "goal": "Build and ship feature",
            "tasks": [
                "Define requirements",
                "Design architecture",
                "Implement",
                "Write tests",
                "Code review",
                "Deploy",
            ],
        },
    ]


@web_route("POST", "/api/from-template")
async def api_from_template(self, request):
    """Create project from template."""
    data = await request.json()
    name = data.get("name", "").strip()
    template_id = data.get("template", "standard")
    if not name:
        return {"error": "name required"}

    all_templates = await self.api_templates(request)
    tmpl = next((t for t in all_templates if t["id"] == template_id), None)
    if not tmpl:
        tmpl = all_templates[0]
    tasks = tmpl["tasks"]
    project_type = data.get("type", tmpl.get("type", "personal"))

    project_id = name.replace(" ", "-")
    proj_dir = self._projects_dir() / project_id
    if proj_dir.exists() or (self._projects_dir() / f"{project_id}.md").exists():
        return {"error": "Project already exists"}

    proj_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("docs", "assets", "log"):
        (proj_dir / subdir).mkdir(exist_ok=True)

    type_def = _core.PROJECT_TYPES.get(project_type, _core.PROJECT_TYPES["personal"])
    initial_stage = type_def["stages"][0] if type_def["stages"] else ""

    today = date.today().isoformat()
    deadline = data.get("deadline", "")
    repo = data.get("repo", "")
    task_lines = "\n".join(f"- [ ] {t}" for t in tasks)

    fm_lines = ["status: active", f"created: {today}"]
    if project_type != "personal":
        fm_lines.append(f"type: {project_type}")
    if initial_stage:
        fm_lines.append(f"stage: {initial_stage}")
    if deadline:
        fm_lines.append(f"deadline: {deadline}")
    if repo:
        fm_lines.append(f"repo: {repo}")
    fm_lines.append("tags:\n  - project")
    fm_block = "\n".join(fm_lines)

    content = f"---\n{fm_block}\n---\n\n# {name}\n\n## Goal\n{data.get('goal', tmpl.get('goal', 'TBD'))}\n\n## Tasks\n{task_lines}\n\n## Notes\n"
    target = proj_dir / f"{project_id}.md"
    target.write_text(content, encoding="utf-8")
    await self.emit(
        "projects:created", {"name": name, "template": template_id, "type": project_type}
    )
    return {"ok": True, "file": target.name, "id": project_id}


# ------------------------------------------------------------------
# Structure Management
# ------------------------------------------------------------------


@web_route("POST", "/api/projects/{id}/upgrade")
async def api_upgrade_structure(self, request):
    """Upgrade a flat-file project to standard directory structure."""
    project_id = request.path_params.get("id", "")
    flat_file = self._projects_dir() / f"{project_id}.md"

    if not flat_file.exists():
        return {"error": "Flat project file not found"}

    proj_dir = self._projects_dir() / project_id
    if proj_dir.exists():
        return {"error": "Directory already exists — cannot upgrade"}

    proj_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("docs", "assets", "log"):
        (proj_dir / subdir).mkdir(exist_ok=True)

    target = proj_dir / f"{project_id}.md"
    target.write_text(flat_file.read_text(encoding="utf-8"), encoding="utf-8")
    flat_file.unlink()

    return {"ok": True, "id": project_id, "structure": list(_core.PROJECT_STRUCTURE.keys())}


@web_route("GET", "/api/structure")
async def api_structure(self, request):
    """Return the standard project folder structure and flat-file projects needing upgrade."""
    projects = await self.list_projects()
    flat = [p["id"] for p in projects if not p.get("is_directory", False)]
    return {
        "standard": _core.PROJECT_STRUCTURE,
        "flat_projects": flat,
        "total": len(projects),
        "compliant": len(projects) - len(flat),
    }
