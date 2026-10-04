"""Project metadata reader — one parse of a `10_Projects/<id>/<id>.md` note.

The area/start/deadline/parent model (vault `30_Resources/EmptyOS/projects/areas.md`)
is read by the Projects app and, per plan `project-session-integration` (P2, P3,
P8), by the session board and a project guard. This module is the one reading
they share, so none of them walks the frontmatter and body its own way. Pure: no
I/O beyond the two readers, no kernel. Importing it does load the `emptyos.sdk`
package (~0.5 s), which a `scripts/` caller pays once.

Absent is a third state, never a fabricated default: a project without
`deadline:` reads `None`, not `""` — the guard's whole job is to tell those
apart. List fields (`plans`, `tracks`) read `[]` when absent or empty, because
"nothing to resolve" is the same answer either way.

The milestone format and parser moved here from
`apps/public/standard/projects/dev_features.py`, which re-exports them; the
section and key-value primitives moved from `projects/shared.py` with them.
"""

from __future__ import annotations

import re
from pathlib import Path

from emptyos.frontmatter import parse_frontmatter, strip_frontmatter

# The generic placeholders the Projects app writes into `## Goal` when no goal
# was given (`projects/operations.py`, `projects/app.py`). A goal still equal to
# one of them was never written. A type template's example goal ("Master a new
# skill", …) is NOT detected here: it reads as prose, and whether it counts is
# the project guard's call (plan P8, which measures false positives first).
GOAL_PLACEHOLDERS = frozenset({
    "tbd", "purpose tbd", "define success criteria", "define success criteria here.",
})

_NEXT_H2 = re.compile(r"^##\s+", re.MULTILINE)
KV_LINE = re.compile(r"\s*-\s+(\w+):\s*(.+)")
_MILESTONE_HEADING = re.compile(r"###\s+(v?\S+)\s*[—–-]\s*(.+)")
_AREA_HEADING = re.compile(r"^###\s+`([^`]+)`\s*$", re.MULTILINE)
_OPEN_BOX = re.compile(r"\s*- \[ \] (.+)")
_DONE_BOX = re.compile(r"\s*- \[x\] (.+)", re.IGNORECASE)
# The `milestone` case of the projects app's `_META_RE` (`projects/shared.py`).
_MILESTONE_META = re.compile(r"\s+- milestone:\s*(.+)")


def markdown_section(content: str, section_name: str) -> str:
    """Text under a `## section_name` heading, up to the next `##` or EOF."""
    pattern = re.compile(r"^##\s+" + re.escape(section_name) + r"\s*$", re.MULTILINE)
    m = pattern.search(content)
    if not m:
        return ""
    start = m.end()
    next_heading = _NEXT_H2.search(content, start)
    end = next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def milestones(content: str) -> list[dict]:
    """Parse the `## Milestones` section.

    Format (`### v0.1 — Name` then `- target: YYYY-MM-DD`, `- status: open|closed`)
    → `[{id, name, target, status}]`. Status defaults to `open`, as the Projects
    app has always read it.
    """
    section = markdown_section(content, "Milestones")
    if not section:
        return []

    found = []
    current = None

    for line in section.split("\n"):
        m = _MILESTONE_HEADING.match(line.strip())
        if m:
            if current:
                found.append(current)
            current = {
                "id": m.group(1).strip(),
                "name": m.group(2).strip(),
                "target": "",
                "status": "open",
            }
        elif current:
            kv = KV_LINE.match(line)
            if kv:
                key, val = kv.group(1), kv.group(2).strip()
                if key in ("target", "status"):
                    current[key] = val

    if current:
        found.append(current)
    return found


def checkbox_counts(body: str, milestone: str | None = None) -> dict:
    """`{"open": n, "done": n}` over every `- [ ]` / `- [x]` line in *body*.

    With *milestone*, counts only tasks carrying an indented `- milestone: <id>`
    line — the link the Projects app reads (`_link_tasks_to_milestones`), with
    the same line shapes as its `_parse_tasks`. One deliberate difference: a
    task that repeats the same milestone line counts once here, where the app
    counts it once per line.
    """
    tasks: list[dict] = []
    for line in body.split("\n"):
        if _OPEN_BOX.match(line):
            tasks.append({"done": False, "milestones": []})
        elif _DONE_BOX.match(line):
            tasks.append({"done": True, "milestones": []})
        elif tasks:
            m = _MILESTONE_META.match(line)
            if m:
                tasks[-1]["milestones"].append(m.group(1).strip())
    if milestone is not None:
        tasks = [t for t in tasks if milestone in t["milestones"]]
    done = sum(1 for t in tasks if t["done"])
    return {"open": len(tasks) - done, "done": done}


def _scalar(fm: dict, key: str) -> str | None:
    # A list where one value belongs (`area: [a, b]`) is joined, never narrowed
    # to its first item: "a, b" then fails a vocabulary check instead of
    # silently passing as "a".
    v = fm.get(key)
    if isinstance(v, list):
        v = ", ".join(s.strip() for s in v if s and s.strip())
    v = (v or "").strip()
    return v or None


def _listish(fm: dict, key: str) -> list[str]:
    v = fm.get(key)
    if isinstance(v, list):
        return [s.strip() for s in v if s and s.strip()]
    v = (v or "").strip()
    return [v] if v else []


def goal_present(body: str) -> bool:
    """A `## Goal` (or `## Goals`, which the Projects app also reads) section
    with real text — not empty, not an app placeholder."""
    goal = (markdown_section(body, "Goal") or markdown_section(body, "Goals")).strip()
    return bool(goal) and goal.lower() not in GOAL_PLACEHOLDERS


def parse_project(content: str, project_id: str) -> dict:
    """Project metadata from note *content*. See `read_project`."""
    fm = parse_frontmatter(content)
    body = strip_frontmatter(content)
    return {
        "id": project_id,
        "area": _scalar(fm, "area"),
        "kind": _scalar(fm, "kind"),
        "start": _scalar(fm, "start"),
        "deadline": _scalar(fm, "deadline"),
        "parent": _scalar(fm, "parent"),
        "plans": _listish(fm, "plans"),
        "tracks": _listish(fm, "tracks"),
        "status": _scalar(fm, "status"),
        "goal_present": goal_present(body),
    }


def read_project(path: Path | str) -> dict:
    """Read one project note → `{id, area, kind, start, deadline, parent,
    plans, tracks, status, goal_present}`.

    `id` is the file stem — the project standard puts a project at
    `10_Projects/<id>/<id>.md`. Scalars are `None` when absent or empty.
    Raises `OSError` if the file cannot be read; a caller walking many
    projects decides whether one unreadable note is fatal.
    """
    p = Path(path)
    return parse_project(p.read_text(encoding="utf-8", errors="replace"), p.stem)


def load_areas(vault: Path | str) -> list[str]:
    """The closed `area:` vocabulary: every ``### `area` `` heading in
    `{vault}/30_Resources/EmptyOS/projects/areas.md`, in file order.

    `[]` when the file is missing — a vault without the model has no
    vocabulary, which a guard must report rather than treat as "anything goes".
    """
    f = Path(vault) / "30_Resources" / "EmptyOS" / "projects" / "areas.md"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [m.group(1).strip() for m in _AREA_HEADING.finditer(text)]
