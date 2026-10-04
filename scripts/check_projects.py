"""Project guard — the area/start/deadline/parent model, checked across the vault.

The model lives in the vault at `30_Resources/EmptyOS/projects/areas.md`
(decided 2026-09-26): a project has an end (`start:`, `deadline:`, a `## Goal`),
an `area:` from that file's closed vocabulary, and optionally a `parent:` whose
deadline covers its children's. The Projects app enforces the vocabulary and the
date shape only when *it* writes a note (`projects/shared.py::area_error`,
`date_error`). A hand edit, a sync, or a note that predates the model never
passes through that gate. This scanner covers that gap. It reuses the app's
validators, its area inheritance (`resolve_inherited_areas`), its note lookup
order and its status normalisation, so a value the app accepts is a value the
guard accepts, and both read the same note.

Plan `project-session-integration` P8. Advisory (`gate=False` in preflight): the
vault is a human artifact, and a stray note must not block an unrelated commit.
Exit 1 still means "at least one finding", in human and `--json` mode alike.

Findings, per project in `10_Projects/`:

  flat_note         a `10_Projects/<id>.md` file. A project is a directory
                    (CLAUDE.md § Project standard); the app still lists it.
  no_note           a folder with no `.md` at all; the app shows it from folder
                    metadata alone, with a status guessed from its mtime.
  untagged          the note lacks `tags: [project]`, so every tag-based reader
                    (`vault_query`, the session board) skips it.
  bad_status        `status:` absent or outside `PROJECT_STATUSES` (case-folded,
                    as the app reads it). The app then *infers* a status from a
                    keyword scan, so the note can land in the wrong column. The
                    open-project checks below need a known status, so they skip
                    these notes.
  bad_area          the note's own `area:` is outside the vocabulary.
  bad_date          `start:` / `deadline:` set but not a real YYYY-MM-DD date.
  start_after_deadline
  parent_missing    `parent:` names no project with a note, in `10_Projects/`
                    or the archive (`40_Archive/10_Projects/`).
  parent_ends_first a parent's deadline is before an open child's.
  plan_missing      a `plans:` entry is in neither `_plans/` nor `_plans/done/`.
  track_missing     a `tracks:` entry has no `_next/<track>.md` brief.

and, for OPEN projects only (status active / spec-ready / blocked, not
`kind: area-home`, which is an area's data home and has no end):

  no_area / no_start / no_deadline / no_goal
                    (an area inherited from the nearest ancestor counts as set;
                    `no_start` names a stray `started:`)

Global: `no_vocabulary` when projects exist but `areas.md` defines no area —
the app then accepts any area, so a guard must say so rather than pass.

Not checked: a folder `_vault-map.toml` gives an app as a data dir (the Canvas
app's `10_Projects/canvas`), and a `tracks:` that is a bare integer (an album's
song count, a different field that shares the name).

Report (never findings, never affect the exit code): open projects past their
deadline, and the inbox's orphan count. The orphan count and its day limit come
from the session board's `inbox_ages` / `ORPHAN_DAYS`, so there is one
definition of an orphan. The report says "unavailable" when that reader is
absent and "failed: <why>" when it raises.

Run: python scripts/check_projects.py [--json]
"""
from __future__ import annotations

import argparse
import datetime
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from scanner_lib import emit_json  # noqa: E402
from vault_paths import vault_root  # noqa: E402

from apps.public.standard.projects.shared import (  # noqa: E402
    PROJECT_STATUSES, area_error, date_error, is_area_home, resolve_inherited_areas,
)
from emptyos.frontmatter import parse_frontmatter  # noqa: E402
from emptyos.sdk.project_meta import load_areas, parse_project  # noqa: E402

OPEN_STATUSES = ("active", "spec-ready", "blocked")
# The folders the Projects app never reads as a project (`reading.py`).
_SKIP_DIRS = {".space", "__pycache__", "node_modules", ".git", ".firebase", ".pytest_cache"}


def _tags(fm: dict) -> list[str]:
    t = fm.get("tags") or []
    if isinstance(t, str):
        t = [t]
    return [str(x).strip() for x in t]


def _app_data_dirs(vault: Path) -> set[str]:
    """Folder names under `10_Projects/` that `_vault-map.toml` gives to an app as
    a data dir (the Canvas app's `boards_dir = "10_Projects/canvas"`). Those hold
    an app's files, not a project, so they are not asked for a project note."""
    f = vault / "30_Resources" / "EmptyOS" / "_vault-map.toml"
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    out = set()
    for section in data.values():
        for v in section.values() if isinstance(section, dict) else []:
            parts = str(v).replace("\\", "/").strip("/").split("/")
            if len(parts) == 2 and parts[0] == "10_Projects":
                out.add(parts[1])
    return out


def _track_refs(meta: dict, fm: dict) -> list[str]:
    """`tracks:` entries that name a track brief. A bare integer is not one: an
    album note uses `tracks: 8` for its song count, a different field that
    happens to share the name."""
    raw = fm.get("tracks")
    if isinstance(raw, str) and raw.strip().isdigit():
        return []
    return meta["tracks"]


def _main_note(d: Path) -> Path | None:
    """The note the Projects app reads for folder *d* (`reading.py`): README.md,
    then `<id>.md`, then index.md, then any `.md`."""
    for candidate in (d / "README.md", d / f"{d.name}.md", d / "index.md"):
        if candidate.is_file():
            return candidate
    return next(iter(sorted(d.glob("*.md"))), None)


def _read_dir(root: Path, skip: set[str]) -> tuple[dict[str, tuple[dict, dict]], list[str], list[str]]:
    """`{id: (meta, frontmatter)}` for each project folder under *root*, plus the
    folders with no note and the flat `.md` files."""
    notes, missing, flat = {}, [], []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_file():
            if d.suffix == ".md" and not d.name.startswith("_"):
                flat.append(d.stem)
            continue
        if d.name.startswith((".", "_")) or d.name in _SKIP_DIRS or d.name in skip:
            continue
        f = _main_note(d)
        if f is None:
            missing.append(d.name)
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        notes[d.name] = (parse_project(text, d.name), parse_frontmatter(text))
    return notes, missing, flat


def _status(meta: dict) -> str:
    # The app's `_infer_status` reads `status` lower-cased and stripped.
    return (meta["status"] or "").strip().lower()


def scan(vault: Path, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    projects_dir = vault / "10_Projects"
    log = projects_dir / "emptyos" / "log"
    plans_dir, next_dir = log / "_plans", log / "_next"
    areas = load_areas(vault)
    notes, missing, flat = _read_dir(projects_dir, _app_data_dirs(vault))
    archived, _, _ = _read_dir(vault / "40_Archive" / "10_Projects", set())
    findings: list[dict] = []

    def add(pid: str, code: str, detail: str) -> None:
        findings.append({"project": pid, "code": code, "detail": detail})

    if notes and not areas:
        add("-", "no_vocabulary",
            "no `### `area`` headings in 30_Resources/EmptyOS/projects/areas.md")
    for pid in flat:
        add(pid, "flat_note", f"10_Projects/{pid}.md is a flat file; move it to {pid}/{pid}.md")
    for pid in missing:
        add(pid, "no_note", f"10_Projects/{pid}/ has no .md note")

    # Area as the app shows it: inherited from the nearest ancestor, archive
    # included. `bad_area` is judged on each note's OWN value, so a bad parent
    # area is reported once, on the parent.
    rows = [{"id": pid, "area": m["area"], "parent": m["parent"]}
            for pid, (m, _) in {**archived, **notes}.items()]
    resolve_inherited_areas(rows)
    shown_area = {r["id"]: r["area"] for r in rows}
    known = {**archived, **notes}

    def valid_date(meta: dict, field: str) -> datetime.date | None:
        v = meta[field]
        return datetime.date.fromisoformat(v) if v and not date_error(field, v) else None

    overdue = []
    for pid, (meta, fm) in notes.items():
        status = _status(meta)
        if "project" not in _tags(fm):
            add(pid, "untagged", "frontmatter `tags:` has no `project`")
        if status not in PROJECT_STATUSES:
            add(pid, "bad_status",
                f"status {meta['status']!r} is not one of {', '.join(PROJECT_STATUSES)}")
        if meta["area"] and (err := area_error(meta["area"], areas)):
            add(pid, "bad_area", err)
        for field in ("start", "deadline"):
            if meta[field] and (err := date_error(field, meta[field])):
                add(pid, "bad_date", err)
        start, deadline = valid_date(meta, "start"), valid_date(meta, "deadline")
        if start and deadline and start > deadline:
            add(pid, "start_after_deadline", f"start {start} is after deadline {deadline}")

        parent = meta["parent"]
        if parent and parent not in known:
            add(pid, "parent_missing",
                f"parent {parent!r} has no project note in 10_Projects/ or the archive")
        elif parent and status in OPEN_STATUSES and deadline:
            pdl = valid_date(known[parent][0], "deadline")
            if pdl and pdl < deadline:
                add(parent, "parent_ends_first",
                    f"deadline {pdl} is before child {pid}'s {deadline}")

        for plan in meta["plans"]:
            if not ((plans_dir / f"{plan}.md").is_file()
                    or (plans_dir / "done" / f"{plan}.md").is_file()):
                add(pid, "plan_missing", f"plan {plan!r} is in neither _plans/ nor _plans/done/")
        for track in _track_refs(meta, fm):
            if not (next_dir / f"{track}.md").is_file():
                add(pid, "track_missing", f"track {track!r} has no _next/{track}.md")

        if status not in OPEN_STATUSES or is_area_home(meta):
            continue
        if not shown_area.get(pid):
            add(pid, "no_area", "open project with no `area:`, own or inherited")
        if not meta["start"]:
            hint = f" (found `started: {fm['started']}` — rename it)" if fm.get("started") else ""
            add(pid, "no_start", "open project with no `start:`" + hint)
        if not meta["deadline"]:
            add(pid, "no_deadline", "open project with no `deadline:`")
        if not meta["goal_present"]:
            add(pid, "no_goal", "open project with no `## Goal` saying what done means")
        if deadline and deadline < today:
            overdue.append({"project": pid, "deadline": str(deadline),
                            "days": (today - deadline).days})

    return {
        "projects": len(notes),
        "areas": areas,
        "findings": findings,
        "report": {"overdue": sorted(overdue, key=lambda o: -o["days"]),
                   "inbox": _inbox(vault, today)},
    }


def _inbox(vault: Path, today: datetime.date) -> dict | None:
    """The session board's inbox ages plus its `orphan_days`; None when that
    reader does not exist; `{"error": ...}` when it exists and fails. The last
    two are kept apart so a bug in the reader never reads as "not built"."""
    try:
        import session_board
    except Exception as e:  # noqa: BLE001 — a report line must not crash the guard
        return {"error": f"session_board import: {type(e).__name__}: {e}"}
    if not hasattr(session_board, "inbox_ages"):
        return None
    try:
        out = dict(session_board.inbox_ages(vault, today))
    except Exception as e:  # noqa: BLE001
        return {"error": f"inbox_ages: {type(e).__name__}: {e}"}
    out["orphan_days"] = getattr(session_board, "ORPHAN_DAYS", None)
    return out


def render(res: dict) -> str:
    f = res["findings"]
    lines: list[str] = []
    by_code: dict[str, list[dict]] = {}
    for x in f:
        by_code.setdefault(x["code"], []).append(x)
    for code, xs in sorted(by_code.items()):
        lines.append(f"\n{code} ({len(xs)})")
        lines += [f"  {x['project']}: {x['detail']}" for x in xs]
    od = res["report"]["overdue"]
    if od:
        lines.append(f"\nReport — overdue ({len(od)})")
        lines += [f"  {o['project']}: deadline {o['deadline']}, {o['days']}d ago" for o in od]
    ib = res["report"]["inbox"]
    if ib is None:
        lines.append("\nReport — inbox: unavailable (no session_board.inbox_ages)")
    elif "error" in ib:
        lines.append(f"\nReport — inbox: failed: {ib['error']}")
    else:
        orphans = "unknown" if ib.get("orphans") is None else ib["orphans"]
        lines.append(f"\nReport — inbox: {ib.get('human_open')} open human tasks, "
                     f"{orphans} older than {ib.get('orphan_days')} days; "
                     f"{ib.get('machine_open')} machine lines")
    # Last, because preflight shows a check's last line as its summary.
    lines.append(f"\n{res['projects']} projects · {len(f)} findings")
    return "\n".join(lines).lstrip("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    # `vault_root()`, not `require_vault_root()`: a fresh clone with no vault is healthy.
    root = vault_root()
    if not root or not (root / "10_Projects").is_dir():
        msg = "no vault configured" if not root else f"no 10_Projects in {root}"
        if args.json:
            return emit_json(True, "ok", msg, {"projects": 0, "findings": []})
        print(msg)
        return 0
    res = scan(root)
    n = len(res["findings"])
    if args.json:
        return emit_json(not n, "projects", f"{res['projects']} projects · {n} findings", res)
    print(render(res))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
