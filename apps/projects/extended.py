"""Projects extended features — analytics, timeline, calendar, docs.

Extracted from projects/app.py to keep the core project management atomic.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from emptyos.sdk import parse_frontmatter, web_route

# ------------------------------------------------------------------
# Grouped / Stats / Portfolio Health
# ------------------------------------------------------------------


@web_route("GET", "/api/grouped")
async def api_grouped(self, request):
    """Projects grouped by status: active, idea, blocked, shelved, completed, archived."""
    projects = await self.list_projects()
    groups: dict[str, list[dict]] = {}
    for p in projects:
        s = p.get("status", "active")
        groups.setdefault(s, []).append(p)
    # Sort active by deadline urgency, others by name
    for s, items in groups.items():
        if s == "active":
            items.sort(key=lambda p: (p.get("days_until_deadline") or 9999, p["name"]))
        else:
            items.sort(key=lambda p: p["name"].lower())
    # Ensure all categories exist
    for s in ("active", "idea", "blocked", "shelved", "completed", "archived"):
        groups.setdefault(s, [])
    return groups


@web_route("GET", "/api/stats")
async def api_stats(self, request):
    """Project portfolio statistics with type breakdown and velocity."""
    projects = await self.list_projects()
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_tasks = 0
    done_tasks = 0
    with_deadline = 0
    overdue = 0
    at_risk = 0
    progress_sum = 0
    active_count = 0
    today_str = date.today().isoformat()

    # Count recently completed tasks (last 7 days) for velocity
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    velocity = 0

    for p in projects:
        s = p.get("status", "idea")
        by_status[s] = by_status.get(s, 0) + 1
        t = p.get("type", "personal")
        by_type[t] = by_type.get(t, 0) + 1
        total_tasks += p.get("open_tasks", 0) + p.get("done_tasks", 0)
        done_tasks += p.get("done_tasks", 0)
        if s == "active":
            active_count += 1
            progress_sum += p.get("progress", 0)
            if p.get("stale_days", 0) > 30 and p.get("progress", 0) < 50:
                at_risk += 1
        if p.get("deadline"):
            with_deadline += 1
            if p["deadline"] < today_str and s not in ("completed", "archived"):
                overdue += 1

    # Velocity: scan project files for recently completed tasks
    for f in self._projects_dir().glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            for line in content.split("\n"):
                m = re.search(r"\u2705\s*(\d{4}-\d{2}-\d{2})", line)
                if m and m.group(1) >= week_ago:
                    velocity += 1
        except Exception:
            continue

    avg_progress = round(progress_sum / active_count) if active_count else 0

    return {
        "total": len(projects),
        "by_status": by_status,
        "by_type": by_type,
        "total_tasks": total_tasks,
        "done_tasks": done_tasks,
        "completion_rate": round(done_tasks / total_tasks * 100) if total_tasks else 0,
        "with_deadline": with_deadline,
        "overdue": overdue,
        "at_risk": at_risk,
        "avg_progress": avg_progress,
        "velocity_7d": velocity,
    }


@web_route("GET", "/api/portfolio-health")
async def api_portfolio_health(self, request):
    """Portfolio health score (0-100) for ring visualization."""
    projects = await self.list_projects()
    if not projects:
        return {"score": 0, "max": 100}
    active = [p for p in projects if p["status"] == "active"]
    if not active:
        return {"score": 50, "max": 100}
    # Scoring: penalize overdue, stale, low progress
    total_penalty = 0
    for p in active:
        if p.get("overdue"):
            total_penalty += 20
        if p.get("stale_days", 0) > 60:
            total_penalty += 15
        elif p.get("stale_days", 0) > 30:
            total_penalty += 5
        if p.get("progress", 0) < 20 and p.get("stale_days", 0) > 14:
            total_penalty += 10
    avg_penalty = total_penalty / len(active)
    score = max(0, min(100, round(100 - avg_penalty)))
    return {"score": score, "max": 100}


# ------------------------------------------------------------------
# Activity / Timeline / Calendar
# ------------------------------------------------------------------


@web_route("GET", "/api/projects/{id}/activity")
async def api_activity(self, request):
    """90-day edit activity heatmap data for a project."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}
    # Build activity from file modification time + git log if available
    activity: dict[str, int] = {}
    today = date.today()
    # Check the project file itself
    try:
        mtime = datetime.fromtimestamp(target.stat().st_mtime).date()
        activity[mtime.isoformat()] = activity.get(mtime.isoformat(), 0) + 1
    except Exception:
        pass
    # Check completed task dates in the file
    try:
        content = target.read_text(encoding="utf-8")
        for line in content.split("\n"):
            m = re.search(r"\u2705\s*(\d{4}-\d{2}-\d{2})", line)
            if m:
                activity[m.group(1)] = activity.get(m.group(1), 0) + 1
    except Exception:
        pass
    # For directory projects, check sub-file mtimes
    proj_dir = self._find_project_dir(project_id)
    if proj_dir and proj_dir.is_dir():
        for f in proj_dir.rglob("*.md"):
            try:
                d = datetime.fromtimestamp(f.stat().st_mtime).date()
                if (today - d).days <= 90:
                    ds = d.isoformat()
                    activity[ds] = activity.get(ds, 0) + 1
            except Exception:
                continue
    return {"activity": activity, "days": 90}


@web_route("GET", "/api/timeline")
async def api_timeline(self, request):
    """Projects formatted for timeline/Gantt visualization."""
    projects = await self.list_projects()
    today = date.today().isoformat()
    items = []
    for p in projects:
        if p["status"] in ("archived",):
            continue
        start = p.get("created", "") or today
        end = p.get("deadline", "")
        if not end:
            # Default: 90 days from start for projects without deadline
            try:
                s = datetime.strptime(start, "%Y-%m-%d").date()
                end = (s + timedelta(days=90)).isoformat()
            except Exception:
                end = today
        items.append(
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "type": p.get("type", "personal"),
                "stage": p.get("stage", ""),
                "start": start,
                "end": end,
                "progress": p.get("progress", 0),
                "open_tasks": p.get("open_tasks", 0),
                "done_tasks": p.get("done_tasks", 0),
                "overdue": p.get("overdue", False),
            }
        )
    # Compute date range
    all_dates = [i["start"] for i in items] + [i["end"] for i in items]
    all_dates = [d for d in all_dates if d]
    range_min = min(all_dates) if all_dates else today
    range_max = max(all_dates) if all_dates else today
    return {"projects": items, "range": {"min": range_min, "max": range_max}, "today": today}


@web_route("GET", "/api/calendar")
async def api_calendar(self, request):
    """Tasks with due dates grouped by date across all projects."""
    month = request.query_params.get("month", date.today().strftime("%Y-%m"))
    projects = await self.list_projects()
    calendar: dict[str, list[dict]] = {}
    for p in projects:
        # YAML frontmatter without a value parses to None, so .get(key, "") still
        # returns None — coerce explicitly. Same pattern across every field.
        status = p.get("status") or ""
        pid = p.get("id") or ""
        name = p.get("name") or pid
        ptype = p.get("type") or "personal"
        deadline = p.get("deadline") or ""

        if status in ("archived", "completed"):
            continue
        if not pid:
            continue
        target = self._find_project_file(pid)
        if not target:
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in content.split("\n"):
            m = re.search(r"- \[ \] (.+?)\s*\U0001f4c5\s*(\d{4}-\d{2}-\d{2})", line)
            if m and m.group(2).startswith(month):
                calendar.setdefault(m.group(2), []).append(
                    {
                        "project": name,
                        "project_id": pid,
                        "task": m.group(1).strip(),
                        "type": ptype,
                    }
                )
        # Also add project deadline
        if deadline.startswith(month):
            calendar.setdefault(deadline, []).append(
                {
                    "project": name,
                    "project_id": pid,
                    "task": "Deadline: " + name,
                    "type": ptype,
                    "is_deadline": True,
                }
            )
    return {"calendar": calendar, "month": month}


# ------------------------------------------------------------------
# Project Documents
# ------------------------------------------------------------------

DOC_TEMPLATES = {
    "meeting": "---\ntype: meeting-note\ndate: {date}\n---\n\n# Meeting: {title}\n\n## Attendees\n- \n\n## Agenda\n1. \n\n## Notes\n\n\n## Action Items\n- [ ] \n",
    "spec": "---\ntype: spec\ncreated: {date}\nstatus: draft\n---\n\n# {title}\n\n## Overview\n\n\n## Requirements\n\n\n## Design\n\n\n## Open Questions\n- \n",
    "research": "---\ntype: research\ncreated: {date}\n---\n\n# {title}\n\n## Background\n\n\n## Findings\n\n\n## References\n- \n\n## Next Steps\n- [ ] \n",
    "blank": "---\ncreated: {date}\n---\n\n# {title}\n\n",
}


@web_route("GET", "/api/projects/{id}/docs")
async def api_docs(self, request):
    """List all documents (.md files) in a project."""
    project_id = request.path_params.get("id", "")
    vault = self.vault_root

    proj_dir = self._find_project_dir(project_id)
    if proj_dir and proj_dir.is_dir():
        # Directory project: list all .md files recursively
        main_names = {"readme.md", f"{proj_dir.name.lower()}.md", "index.md"}
        docs = []
        for f in sorted(proj_dir.rglob("*.md")):
            # Skip hidden dirs
            if any(
                p.startswith(".") or p.startswith("_") for p in f.relative_to(proj_dir).parts[:-1]
            ):
                continue
            try:
                stat = f.stat()
                rel = str(f.relative_to(vault)).replace("\\", "/")
                docs.append(
                    {
                        "name": f.name,
                        "path": rel,
                        "rel_path": str(f.relative_to(proj_dir)).replace("\\", "/"),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "is_main": f.name.lower() in main_names,
                    }
                )
            except Exception:
                continue
        # Sort: main file first, then by modified desc
        main = [d for d in docs if d["is_main"]]
        rest = sorted(
            [d for d in docs if not d["is_main"]], key=lambda d: d["modified"], reverse=True
        )
        return {
            "docs": main + rest,
            "is_directory": True,
            "project_dir": str(proj_dir.relative_to(vault)).replace("\\", "/"),
        }

    # File-based project: just the single project file
    f = self._find_project_file(project_id)
    if f and f.exists():
        try:
            stat = f.stat()
            try:
                rel = str(f.relative_to(vault)).replace("\\", "/")
            except ValueError:
                rel = f.name  # fallback if not under vault
            return {
                "docs": [
                    {
                        "name": f.name,
                        "path": rel,
                        "rel_path": f.name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "is_main": True,
                    }
                ],
                "is_directory": False,
            }
        except Exception as e:
            return {"docs": [], "is_directory": False, "error": str(e)}

    return {"docs": [], "is_directory": False}


@web_route("POST", "/api/projects/{id}/docs/create")
async def api_create_doc(self, request):
    """Create a new document within a project directory."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    title = (data.get("title") or "").strip()
    template = data.get("template", "blank")

    if not title:
        return {"error": "title required"}

    proj_dir = self._find_project_dir(project_id)
    if not proj_dir:
        return {
            "error": "Only directory-based projects support creating docs. This is a single-file project."
        }

    # Sanitize filename
    safe_name = re.sub(r'[<>:"/\\|?*]', "", title).strip()
    if not safe_name:
        return {"error": "Invalid title"}
    filename = safe_name + ".md"
    filepath = proj_dir / filename

    if filepath.exists():
        return {"error": f"Document already exists: {filename}"}

    # Generate content from template
    today = date.today().isoformat()
    tmpl = DOC_TEMPLATES.get(template, DOC_TEMPLATES["blank"])
    content = tmpl.replace("{title}", title).replace("{date}", today)

    await self.write(str(filepath), content)
    rel = str(filepath.relative_to(self.vault_root)).replace("\\", "/")

    await self.emit("projects:doc_created", {"project": project_id, "doc": filename})
    return {"ok": True, "name": filename, "path": rel}


# ------------------------------------------------------------------
# Calculations / Dev Status
# ------------------------------------------------------------------


@web_route("GET", "/api/projects/{id}/calculations")
async def api_calculations(self, request):
    """List saved calculation results for a project."""
    project_id = request.path_params.get("id", "")
    calcs_dir = Path(self.data_dir) / "calcs" / project_id
    if not calcs_dir.exists():
        return {"calculations": []}
    results = []
    for f in sorted(calcs_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            parts = f.stem.split("-", 2)  # timestamp-app-method
            results.append(
                {
                    "file": f.name,
                    "timestamp": parts[0] if parts else "",
                    "app": parts[1] if len(parts) > 1 else "",
                    "method": parts[2] if len(parts) > 2 else "",
                    "summary": _summarize_calc(data),
                }
            )
        except Exception:
            continue
    return {"calculations": results}


@web_route("GET", "/api/projects/{id}/dev-status")
async def api_dev_status(self, request):
    """Git integration data for development projects."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    fm = parse_frontmatter(target.read_text(encoding="utf-8"))
    if fm.get("type") != "development":
        return {"error": "Not a development project"}

    repo_path = fm.get("repo", "")
    if not repo_path:
        return {"error": "No repo path in frontmatter"}

    try:
        git_status = await self.call_app("git", "status_at", repo_path=repo_path)
        git_log = await self.call_app("git", "log_at", repo_path=repo_path, count=10)
        return {"repo": repo_path, "status": git_status, "log": git_log}
    except Exception as e:
        return {"error": f"Git integration failed: {e}"}


# --- Helpers ---


def _summarize_calc(data: dict) -> str:
    """One-line summary of a calculation result."""
    if "max_tension_kN" in data:
        return (
            f"Tension: {data['max_tension_kN']} kN, SWP: {'OK' if data.get('swp_ok') else 'FAIL'}"
        )
    if "algorithms" in data:
        return f"{len(data['algorithms'])} algorithms compared"
    if "error" in data:
        return f"Error: {data['error']}"
    return f"{len(data)} fields"


# ------------------------------------------------------------------
# Metadata updates — tags / deadline / description frontmatter edit
# ------------------------------------------------------------------


@web_route("POST", "/api/projects/{id}/meta")
async def api_update_meta(self, request):
    """Update project metadata (tags, deadline, description) in frontmatter."""
    project_id = request.path_params.get("id", "")
    data = await request.json()

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    if not content.startswith("---"):
        content = "---\n---\n" + content
    fm_end = content.find("---", 3)
    if fm_end <= 0:
        return {"error": "Malformed frontmatter"}
    fm_block = content[3:fm_end]

    def _set_scalar(block: str, key: str, value: str) -> str:
        pattern = rf"(?m)^{re.escape(key)}:.*(?:\n[ \t]+.*)*"
        if re.search(pattern, block):
            if value == "":
                return re.sub(pattern + r"\n?", "", block)
            return re.sub(pattern, f"{key}: {value}", block)
        if value == "":
            return block
        return block.rstrip() + f"\n{key}: {value}\n"

    def _set_list(block: str, key: str, items: list[str]) -> str:
        pattern = rf"(?m)^{re.escape(key)}:.*(?:\n[ \t]+-.*)*"
        items = [i.strip() for i in items if i.strip()]
        if not items:
            if re.search(pattern, block):
                return re.sub(pattern + r"\n?", "", block)
            return block
        replacement = f"{key}:\n  - " + "\n  - ".join(items)
        if re.search(pattern, block):
            return re.sub(pattern, replacement, block)
        return block.rstrip() + f"\n{replacement}\n"

    updated = {}
    if "tags" in data:
        raw = data["tags"]
        items = [
            t.strip().lstrip("#") for t in (raw if isinstance(raw, list) else str(raw).split(","))
        ]
        fm_block = _set_list(fm_block, "tags", items)
        updated["tags"] = [i for i in items if i]
    if "deadline" in data:
        v = (data.get("deadline") or "").strip()
        fm_block = _set_scalar(fm_block, "deadline", v)
        updated["deadline"] = v
    if "description" in data:
        v = (data.get("description") or "").strip()
        fm_block = _set_scalar(fm_block, "description", v)
        updated["description"] = v

    content = "---" + fm_block.rstrip() + "\n---" + content[fm_end + 3 :]
    target.write_text(content, encoding="utf-8")
    await self.emit("projects:refreshed", {"id": project_id})
    return {"ok": True, "updated": updated}
