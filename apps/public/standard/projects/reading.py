"""projects — vault read/scan layer — parse project notes + dirs into model dicts.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The disk-read side of the projects app: scanning 10_Projects/ (flat + dir-form), parsing frontmatter/status/tasks, resolving a project id to its file/dir, feature resolution, and the read-only cross-app context loaders (list_projects, load_project_context, get_project_content, project_path). Source of truth for how a project note becomes a model dict; every write/panel/operations helper reads through these via self..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: PROJECT_TYPES / PROJECT_FEATURES / _META_RE from .shared.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations
import re
from datetime import date, datetime
from pathlib import Path
from emptyos.sdk import parse_frontmatter, fm_list
from emptyos.sdk.project_meta import load_areas, parse_project
from .shared import (
    PROJECT_TYPES, PROJECT_FEATURES, PROJECT_STATUSES, _META_RE, area_error,
    resolve_inherited_areas,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ProjectsApp  # noqa: F401 — for type hints only

# The area-model keys `project_meta.parse_project` reads, passed through as-is:
# absent stays None (lists stay []), never a fabricated "".
_MODEL_KEYS = ("area", "kind", "start", "parent", "plans", "tracks", "goal_present")


def _absent_model() -> dict:
    return {"area": None, "kind": None, "start": None, "parent": None,
            "plans": [], "tracks": [], "goal_present": False}


# ─── Bind to ProjectsApp class as ────────────────────────────────
#   _resolve_features     = _reading._resolve_features
#   _projects_dir         = _reading._projects_dir
#   _infer_status         = _reading._infer_status
#   _parse_tasks          = _reading._parse_tasks
#   _days_until           = _reading._days_until
#   _read_project         = _reading._read_project
#   _read_dir_project     = _reading._read_dir_project
#   _archive_dir          = _reading._archive_dir
#   _scan_dir             = _reading._scan_dir
#   _find_project_file    = _reading._find_project_file
#   project_path          = _reading.project_path
#   _find_project_dir     = _reading._find_project_dir
#   get_project_content   = _reading.get_project_content
#   load_project_context  = _reading.load_project_context
#   list_projects         = _reading.list_projects
#   _areas                = _reading._areas
#   _area_error           = _reading._area_error
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


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
    # 1. Explicit frontmatter status — `status:` (empty) in YAML parses to None,
    #    so .get(..., "") still returns None. Coerce defensively.
    status = (fm.get("status") or "").lower().strip()
    if status in PROJECT_STATUSES:
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
            task_list.append(
                {"text": m_open.group(1).strip(), "done": False, "line": i, "meta": []}
            )
        elif m_done:
            done_tasks += 1
            task_list.append(
                {"text": m_done.group(1).strip(), "done": True, "line": i, "meta": []}
            )
        elif task_list:
            # Check for indented metadata under the last task
            m_meta = _META_RE.match(line)
            if m_meta:
                task_list[-1]["meta"].append(
                    {
                        "type": m_meta.group(1),
                        "value": m_meta.group(2).strip(),
                        "line": i,
                    }
                )
    return open_tasks, done_tasks, task_list


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
    model = parse_project(content, f.stem)

    return {
        **{k: model[k] for k in _MODEL_KEYS},
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
        "employer": (fm.get("employer") or fm.get("company") or "").strip(),
        "repo": fm.get("repo", ""),
        "next_action": (fm.get("next_action") or "").strip(),
        "open_tasks": open_tasks,
        "done_tasks": done_tasks,
        "total_tasks": open_tasks + done_tasks,
        "progress": round(done_tasks / (open_tasks + done_tasks) * 100)
        if (open_tasks + done_tasks) > 0
        else 0,
        "days_until_deadline": days_until,
        "overdue": days_until is not None and days_until < 0,
        "stale_days": mtime_days,
        "is_directory": f.parent not in (self._projects_dir(), self._archive_dir()),
        "features": features,
    }


def _read_dir_project(self, d: Path) -> dict | None:
    """Read a directory-based project (folder with README.md or index note)."""
    # Skip excluded dirs
    if d.name in (
        ".space",
        "__pycache__",
        "node_modules",
        ".git",
        ".firebase",
        ".pytest_cache",
    ):
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
                **_absent_model(),
                "id": d.name,
                "file": d.name + "/",
                "name": d.name.replace("-", " ").replace("_", " "),
                "status": "archived"
                if mtime_days > 180
                else "shelved"
                if mtime_days > 90
                else "active",
                "type": "personal",
                "stage": "",
                "stage_index": -1,
                "stage_total": 0,
                "created": "",
                "deadline": "",
                "tags": "",
                "employer": "",
                "repo": "",
                "open_tasks": 0,
                "done_tasks": 0,
                "total_tasks": 0,
                "progress": 0,
                "days_until_deadline": None,
                "overdue": False,
                "stale_days": mtime_days,
                "is_directory": True,
                "features": self._resolve_features("personal"),
            }
    return self._read_project(readme)


def _archive_dir(self) -> Path:
    return (
        self.vault_config_path("archive_dir", "40_Archive/10_Projects")
        or self.vault_root / "40_Archive" / "10_Projects"
    )


def _scan_dir(
    self, directory: Path, seen_ids: set, status_filter: str, force_status: str = ""
) -> list[dict]:
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


async def project_path(self, id: str = "") -> str:
    """Vault-relative path of a project's main note.

    Wired into the 4D timeline contract — see [provides.timeline]
    entity_source in manifest.toml. Returns "" when id doesn't
    resolve so the timeline endpoint can short-circuit.
    """
    f = self._find_project_file(id)
    if not f:
        return ""
    return self.vault_rel(f)


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


async def load_project_context(
    self,
    project_id: str,
    *,
    max_chars: int = 30_000,
    max_files: int = 10,
) -> str:
    """Concatenated context bundle for chat-pinned projects.

    Returns the project's main note followed by up to ``max_files``
    markdown files from its ``docs/`` folder, each with a header showing
    the file name. Returned text is capped at ``max_chars`` overall — a
    ``…[truncated]`` marker tells the model what it didn't see. Returns
    an empty string when the project doesn't resolve so callers can
    no-op without branching.
    """
    if not project_id:
        return ""
    parts: list[str] = []
    main = await self.get_project_content(project_id)
    if main.get("content"):
        parts.append(f"# Project: {project_id}\n\n{main['content']}")

    proj_dir = self._find_project_dir(project_id)
    if proj_dir:
        docs_dir = proj_dir / "docs"
        if docs_dir.is_dir():
            doc_files = sorted(docs_dir.glob("*.md"))[:max_files]
            for f in doc_files:
                try:
                    body = await self.read(str(f))
                except Exception:
                    continue
                parts.append(f"# docs/{f.name}\n\n{body}")

    bundle = "\n\n---\n\n".join(p for p in parts if p)
    if len(bundle) > max_chars:
        bundle = bundle[:max_chars] + "\n\n…[truncated]"
    return bundle


async def list_projects(self, status_filter: str = "") -> list[dict]:
    seen_ids: set = set()
    # 1. Active projects from 10_Projects/
    results = self._scan_dir(self._projects_dir(), seen_ids, "")
    # 2. Archived projects from 40_Archive/10_Projects/ (force status=archived)
    results += self._scan_dir(
        self._archive_dir(), seen_ids, "", force_status="archived"
    )
    # Inherit before filtering: a subproject's parent may not match the filter,
    # and it must still lend its area.
    resolve_inherited_areas(results)
    if status_filter:
        results = [p for p in results if p["status"] == status_filter]
    return results


def _areas(self) -> list[str]:
    """The closed `area:` vocabulary for this vault (`[]` when it has none)."""
    return load_areas(self.vault_root)


def _area_error(self, value) -> str | None:
    return area_error(value, self._areas())
