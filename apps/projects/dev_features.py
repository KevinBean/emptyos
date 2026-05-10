"""Projects dev features — sprints, milestones, releases.

Extracted from projects/app.py. Data stored as markdown sections
in the project note (vault = source of truth).

Sprint format:
    ## Sprints
    ### Sprint 1: Name (YYYY-MM-DD — YYYY-MM-DD)
    - status: active|closed
    - goal: Sprint goal text

    Tasks link via metadata: `- sprint: 1`

Milestone format:
    ## Milestones
    ### v0.1 — Name
    - target: YYYY-MM-DD
    - status: open|closed

    Tasks link via metadata: `- milestone: v0.1`

Release format:
    ## Releases
    ### v0.1.0 (YYYY-MM-DD)
    - Changelog entry
"""

from __future__ import annotations

import re
from datetime import date

from emptyos.sdk import web_route

# --- Parsers ---

_SPRINT_HEADING = re.compile(
    r"###\s+Sprint\s+(\d+):\s*(.+?)\s*\((\d{4}-\d{2}-\d{2})\s*[—–-]\s*(\d{4}-\d{2}-\d{2})\)"
)
_MILESTONE_HEADING = re.compile(r"###\s+(v?\S+)\s*[—–-]\s*(.+)")
_RELEASE_HEADING = re.compile(r"###\s+(v?\S+)\s*\((\d{4}-\d{2}-\d{2})\)")
_KV_LINE = re.compile(r"\s*-\s+(\w+):\s*(.+)")
_NEXT_H2 = re.compile(r"^##\s+", re.MULTILINE)


def _parse_section(content: str, section_name: str) -> str:
    """Extract text under a ## section heading (up to next ## or EOF)."""
    pattern = re.compile(r"^##\s+" + re.escape(section_name) + r"\s*$", re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return ""
    start = m.end()
    next_heading = _NEXT_H2.search(content, start)
    end = next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def _append_to_section(content: str, section_name: str, block: str) -> str:
    """Append a block to a ## section, creating the section if missing."""
    header = f"## {section_name}"
    if header in content:
        section_start = content.index(header)
        after = content[section_start + len(header) :]
        next_h2 = _NEXT_H2.search(after)
        if next_h2:
            insert_pos = section_start + len(header) + next_h2.start()
            return content[:insert_pos] + block + "\n" + content[insert_pos:]
        return content.rstrip() + "\n" + block
    return content.rstrip() + f"\n\n{header}\n" + block


def _parse_sprints(content: str) -> list[dict]:
    """Parse ## Sprints section into structured sprint objects."""
    section = _parse_section(content, "Sprints")
    if not section:
        return []

    sprints = []
    current = None

    for line in section.split("\n"):
        m = _SPRINT_HEADING.match(line.strip())
        if m:
            if current:
                sprints.append(current)
            current = {
                "num": int(m.group(1)),
                "name": m.group(2).strip(),
                "start": m.group(3),
                "end": m.group(4),
                "status": "active",
                "goal": "",
            }
        elif current:
            kv = _KV_LINE.match(line)
            if kv:
                key, val = kv.group(1), kv.group(2).strip()
                if key in ("status", "goal"):
                    current[key] = val

    if current:
        sprints.append(current)
    return sprints


def _parse_milestones(content: str) -> list[dict]:
    """Parse ## Milestones section into structured milestone objects."""
    section = _parse_section(content, "Milestones")
    if not section:
        return []

    milestones = []
    current = None

    for line in section.split("\n"):
        m = _MILESTONE_HEADING.match(line.strip())
        if m:
            if current:
                milestones.append(current)
            current = {
                "id": m.group(1).strip(),
                "name": m.group(2).strip(),
                "target": "",
                "status": "open",
            }
        elif current:
            kv = _KV_LINE.match(line)
            if kv:
                key, val = kv.group(1), kv.group(2).strip()
                if key in ("target", "status"):
                    current[key] = val

    if current:
        milestones.append(current)
    return milestones


def _parse_releases(content: str) -> list[dict]:
    """Parse ## Releases section into structured release objects."""
    section = _parse_section(content, "Releases")
    if not section:
        return []

    releases = []
    current = None

    for line in section.split("\n"):
        m = _RELEASE_HEADING.match(line.strip())
        if m:
            if current:
                releases.append(current)
            current = {
                "version": m.group(1).strip(),
                "date": m.group(2),
                "notes": [],
            }
        elif current:
            stripped = line.strip()
            if stripped.startswith("- ") and not _KV_LINE.match(line):
                current["notes"].append(stripped[2:])

    if current:
        releases.append(current)
    return releases


def _link_tasks_to_sprints(task_list: list[dict], sprints: list[dict]) -> list[dict]:
    """Annotate sprints with their linked tasks (via - sprint: N metadata)."""
    sprint_map = {s["num"]: s for s in sprints}
    for s in sprints:
        s["tasks"] = []
        s["open"] = 0
        s["done"] = 0

    for t in task_list:
        for m in t.get("meta", []):
            if m["type"] == "sprint":
                try:
                    num = int(m["value"])
                except ValueError:
                    continue
                if num in sprint_map:
                    sprint_map[num]["tasks"].append(t)
                    if t["done"]:
                        sprint_map[num]["done"] += 1
                    else:
                        sprint_map[num]["open"] += 1
    return sprints


def _link_tasks_to_milestones(task_list: list[dict], milestones: list[dict]) -> list[dict]:
    """Annotate milestones with their linked tasks (via - milestone: id metadata)."""
    ms_map = {ms["id"]: ms for ms in milestones}
    for ms in milestones:
        ms["tasks"] = []
        ms["open"] = 0
        ms["done"] = 0

    for t in task_list:
        for m in t.get("meta", []):
            if m["type"] == "milestone":
                ms_id = m["value"].strip()
                if ms_id in ms_map:
                    ms_map[ms_id]["tasks"].append(t)
                    if t["done"]:
                        ms_map[ms_id]["done"] += 1
                    else:
                        ms_map[ms_id]["open"] += 1
    return milestones


# --- API Endpoints ---


@web_route("GET", "/api/projects/{id}/sprints")
async def api_sprints(self, request):
    """List sprints with linked tasks and velocity stats."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    _, _, task_list = self._parse_tasks(content)
    sprints = _parse_sprints(content)
    sprints = _link_tasks_to_sprints(task_list, sprints)

    # Velocity: done tasks per closed sprint
    velocity = []
    for s in sprints:
        if s["status"] == "closed":
            velocity.append({"sprint": s["num"], "done": s["done"], "total": s["open"] + s["done"]})

    # Find active sprint
    active = next((s for s in sprints if s["status"] == "active"), None)

    return {
        "sprints": sprints,
        "active": active,
        "velocity": velocity,
        "avg_velocity": round(sum(v["done"] for v in velocity) / len(velocity), 1)
        if velocity
        else 0,
    }


@web_route("POST", "/api/projects/{id}/sprints")
async def api_create_sprint(self, request):
    """Create a new sprint (appends to ## Sprints section)."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    name = data.get("name", "").strip()
    start_date = data.get("start", date.today().isoformat())
    end_date = data.get("end", "")
    goal = data.get("goal", "")

    if not name:
        return {"error": "Sprint name required"}
    if not end_date:
        return {"error": "Sprint end date required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")

    # Determine next sprint number
    existing = _parse_sprints(content)
    next_num = max((s["num"] for s in existing), default=0) + 1

    sprint_block = (
        f"\n### Sprint {next_num}: {name} ({start_date} — {end_date})\n- status: active\n"
    )
    if goal:
        sprint_block += f"- goal: {goal}\n"

    content = _append_to_section(content, "Sprints", sprint_block)

    target.write_text(content, encoding="utf-8")
    await self.emit(
        "projects:sprint_created", {"project": project_id, "sprint": next_num, "name": name}
    )
    return {"ok": True, "num": next_num, "name": name, "start": start_date, "end": end_date}


@web_route("POST", "/api/projects/{id}/sprints/{num}/close")
async def api_close_sprint(self, request):
    """Close a sprint (set status to closed)."""
    project_id = request.path_params.get("id", "")
    sprint_num = request.path_params.get("num", "")

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    sprints = _parse_sprints(content)

    try:
        num = int(sprint_num)
    except ValueError:
        return {"error": "Invalid sprint number"}

    sprint = next((s for s in sprints if s["num"] == num), None)
    if not sprint:
        return {"error": f"Sprint {num} not found"}

    # Replace status: active with status: closed in the sprint block
    # Find the sprint heading and its status line
    pattern = re.compile(
        r"(###\s+Sprint\s+" + str(num) + r":.*?\n)"
        r"((?:\s*-\s+\w+:.*\n)*)",
        re.MULTILINE,
    )
    m = pattern.search(content)
    if not m:
        return {"error": f"Could not locate Sprint {num} block in file"}

    block = m.group(0)
    new_block = re.sub(r"(-\s+status:\s*).*", r"\g<1>closed", block)
    content = content.replace(block, new_block)
    target.write_text(content, encoding="utf-8")

    await self.emit("projects:sprint_closed", {"project": project_id, "sprint": num})
    return {"ok": True, "sprint": num, "status": "closed"}


@web_route("GET", "/api/projects/{id}/milestones")
async def api_milestones(self, request):
    """List milestones with linked tasks and progress."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    _, _, task_list = self._parse_tasks(content)
    milestones = _parse_milestones(content)
    milestones = _link_tasks_to_milestones(task_list, milestones)

    for ms in milestones:
        total = ms["open"] + ms["done"]
        ms["progress"] = round(ms["done"] / total * 100) if total > 0 else 0

    return {"milestones": milestones}


@web_route("POST", "/api/projects/{id}/milestones")
async def api_create_milestone(self, request):
    """Create a new milestone (appends to ## Milestones section)."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    ms_id = data.get("id", "").strip()
    name = data.get("name", "").strip()
    target_date = data.get("target", "")

    if not ms_id or not name:
        return {"error": "Milestone id and name required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")

    ms_block = f"\n### {ms_id} — {name}\n- status: open\n"
    if target_date:
        ms_block += f"- target: {target_date}\n"

    content = _append_to_section(content, "Milestones", ms_block)

    target.write_text(content, encoding="utf-8")
    await self.emit(
        "projects:milestone_created", {"project": project_id, "milestone": ms_id, "name": name}
    )
    return {"ok": True, "id": ms_id, "name": name, "target": target_date}


@web_route("GET", "/api/projects/{id}/releases")
async def api_releases(self, request):
    """List releases."""
    project_id = request.path_params.get("id", "")
    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")
    releases = _parse_releases(content)
    return {"releases": releases}


@web_route("POST", "/api/projects/{id}/releases")
async def api_create_release(self, request):
    """Create a new release (appends to ## Releases section)."""
    project_id = request.path_params.get("id", "")
    data = await request.json()
    version = data.get("version", "").strip()
    release_date = data.get("date", date.today().isoformat())
    notes = data.get("notes", [])

    if not version:
        return {"error": "Version required"}

    target = self._find_project_file(project_id)
    if not target:
        return {"error": "Project not found"}

    content = target.read_text(encoding="utf-8")

    # If no notes provided, auto-generate from done tasks since last release
    if not notes:
        _, _, task_list = self._parse_tasks(content)
        notes = [t["text"] for t in task_list if t["done"]]

    notes_lines = "\n".join(f"- {n}" for n in notes) if notes else "- Initial release"
    release_block = f"\n### {version} ({release_date})\n{notes_lines}\n"

    content = _append_to_section(content, "Releases", release_block)

    target.write_text(content, encoding="utf-8")
    await self.emit("projects:release_created", {"project": project_id, "version": version})
    return {"ok": True, "version": version, "date": release_date, "notes": notes}
