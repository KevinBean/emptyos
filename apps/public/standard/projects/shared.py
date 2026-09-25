"""projects — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (reading) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations
import re


# Subdirectories are created LAZILY on first write (self.write / mkdir with
# parents=True at the write site) — never eagerly at project creation, so the
# vault-structure scanner's empty-dir pruning (scripts/check_vault_structure.py)
# and project scaffolding don't fight.
PROJECT_STRUCTURE = {
    "main": "{id}/{id}.md",  # Main project note (frontmatter + tasks + notes)
    "docs": "{id}/docs/",  # Specs, meeting notes, research
    "assets": "{id}/assets/",  # Attachments (images, PDFs, exports)
    "log": "{id}/log/",  # Activity logs, changelogs, decision records
}

# One lifecycle vocabulary for every project read/write surface. `spec-ready`
# is the deliberate handoff into Reactor -> App Builder; `archived` remains a
# real terminal state rather than a UI-only column.
PROJECT_STATUSES = (
    "idea",
    "active",
    "spec-ready",
    "blocked",
    "shelved",
    "completed",
    "archived",
)

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
            "concept": "Concept",
            "design": "Design",
            "calculation": "Calculation",
            "review": "Review",
            "approval": "Approval",
            "construction": "Construction",
        },
        "templates": ["engineering", "cable-design"],
    },
    "development": {
        "label": "Development",
        "stages": ["planning", "development", "testing", "review", "release"],
        "labels": {
            "planning": "Planning",
            "development": "Development",
            "testing": "Testing",
            "review": "Review",
            "release": "Release",
        },
        "templates": ["development"],
    },
}

PROJECT_FEATURES = {
    "tasks": {
        "label": "Tasks",
        "tab": True,
        "order": 10,
        "default_on": ["personal", "engineering", "development"],
    },
    "docs": {
        "label": "Docs",
        "tab": True,
        "order": 20,
        "default_on": ["personal", "engineering", "development"],
    },
    "stages": {
        "label": "Stages",
        "tab": False,
        "order": 5,
        "default_on": ["engineering", "development"],
    },
    "code": {"label": "Code", "tab": True, "order": 30, "default_on": ["development"]},
    "sprints": {"label": "Sprints", "tab": True, "order": 25, "default_on": ["development"]},
    "milestones": {"label": "Milestones", "tab": True, "order": 35, "default_on": ["development"]},
    "releases": {"label": "Releases", "tab": True, "order": 40, "default_on": ["development"]},
    "tools": {"label": "Tools", "tab": True, "order": 50, "default_on": ["engineering"]},
    "calculations": {
        "label": "Calculations",
        "tab": True,
        "order": 55,
        "default_on": ["engineering"],
    },
}

# Task metadata prefixes (indented lines under a task)
META_PREFIXES = {
    "info", "need", "calc", "ref", "depends_on", "blocks", "sprint", "milestone",
    "assigned",  # "<agent_id>:<job_id>" — set by assign_task_to_agent (dispatch bridge to staff)
}

_META_RE = re.compile(r"\s+- (" + "|".join(META_PREFIXES) + r"):\s*(.+)")


def scan_task_meta_block(lines: list[str], line_num: int) -> dict:
    """Scan the indented metadata lines directly under task `line_num`.

    Shared by every task-meta writer/reader (add-meta, task-assignee) so
    "where does this task's metadata block end" and "which line already
    holds type X" are answered once, not re-scanned per call site.

    Returns {"insert_at": <index past the last meta line — append here for
    a NEW line of any type>, "by_type": {<type>: <last line index seen for
    that type>}}. Multiple lines of the same type are legal (e.g. several
    `info:` notes); `by_type` deliberately keeps only the last, which is
    exactly what a caller enforcing "at most one live line" (task-assignee's
    `assigned:`) wants to find and replace.
    """
    insert_at = line_num + 1
    by_type: dict[str, int] = {}
    while insert_at < len(lines):
        m = _META_RE.match(lines[insert_at])
        if not m:
            break
        by_type[m.group(1)] = insert_at
        insert_at += 1
    return {"insert_at": insert_at, "by_type": by_type}


# ── Markdown section parsing (shared by dev_features + workspace) ──
# Pure helpers for reading `## Section` blocks and the `### Sprint N: …` shape.
# Live here (not in a helper module) so any helper can import them without a
# helper-to-helper dependency (.claude/rules/multi-module-apps.md §4/§6).
_NEXT_H2 = re.compile(r"^##\s+", re.MULTILINE)
_KV_LINE = re.compile(r"\s*-\s+(\w+):\s*(.+)")
_SPRINT_HEADING = re.compile(
    r"###\s+Sprint\s+(\d+):\s*(.+?)\s*\((\d{4}-\d{2}-\d{2})\s*[—–-]\s*(\d{4}-\d{2}-\d{2})\)"
)


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


def _parse_sprints(content: str) -> list[dict]:
    """Parse the ## Sprints section into structured sprint objects."""
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


def insert_task_line(content: str, task_line: str) -> str:
    """Return *content* with *task_line* placed at the end of its `## Tasks` section.

    Three shapes, and the third is the one that used to 500. The section is
    found, and:

    - another `## ` heading follows it -> insert just before that heading, so a
      new task lands inside Tasks rather than at the bottom of the note;
    - nothing follows it -> append at the end;
    - **the heading is the last line and carries no trailing newline** -> also
      append at the end. `add_task_to_project` used to reach for
      ``content.index("\\n", idx)`` here, which raises ``ValueError: substring
      not found`` on a note ending exactly `## Tasks`. That is not a
      hypothetical: `54-day-safe-projects.md` is an 8-byte note of precisely
      that shape, and being alphabetically first it is `items[0]` of
      `/projects/api/list` — so every test that grabs the first project hit a
      500 (`tests/test_sys_projects.py`, red since at least 2026-08-30).

    No `## Tasks` at all -> the section is created. Pure: no `self`, no I/O.
    """
    if "## Tasks" not in content:
        return content.rstrip() + "\n\n## Tasks\n" + task_line + "\n"

    idx = content.index("## Tasks")
    end_of_heading = content.find("\n", idx)
    # `find`, not `index` — a missing newline is a shape to handle, not an
    # error. Guard the next-section search too: with end_of_heading == -1 the
    # `+ 1` would restart the scan at 0 and could match a heading *above*
    # Tasks, splicing the task into an earlier section.
    next_section = content.find("\n## ", end_of_heading + 1) if end_of_heading >= 0 else -1
    if next_section > 0:
        return content[:next_section] + task_line + "\n" + content[next_section:]
    return content.rstrip() + "\n" + task_line + "\n"
