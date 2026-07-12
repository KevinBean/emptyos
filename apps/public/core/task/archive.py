"""task — reversible task archive (move stale/someday tasks out of the active set).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The archive-first->source-removal move to 40_Archive/task-archive/<year>.md and its two HTTP entry points. Source of truth for archive_tasks + the archive/archive-batch endpoints.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._abs_path/_read_lines/_write_lines/write_lock/_file_lock (spine), mutations.task_text.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from . import mutations

if TYPE_CHECKING:
    from .app import TaskApp  # noqa: F401 — for type hints only


# ─── Bind to TaskApp class as ────────────────────────────────
#   ARCHIVE_DIR        = _archive.ARCHIVE_DIR
#   archive_tasks      = _archive.archive_tasks
#   api_archive        = _archive.api_archive
#   api_archive_batch  = _archive.api_archive_batch
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


ARCHIVE_DIR = "40_Archive/task-archive"


async def archive_tasks(self, items: list[dict]) -> dict:
    """Move tasks to ``40_Archive/task-archive/<year>.md`` (reversible).

    Each item is ``{file, line, text?}``. ``text``, when given, guards a
    stale line (the source changed since the caller read it). Archived
    lines are appended under a dated heading with provenance, then removed
    from their source files under a per-file write lock. The archive folder
    is not scanned by the indexer, so archived tasks leave the active list;
    the source-file diff stays in git for restore.

    Order is archive-first → source-removal, so a crash leaves a
    recoverable duplicate, never a lost task.
    """
    today = date.today().isoformat()
    archive_rel = f"{self.ARCHIVE_DIR}/{today[:4]}.md"
    archive_abs = self._abs_path(archive_rel)
    if not archive_abs:
        return {"error": "No notes path configured"}

    # Pass 1 — resolve + verify (read-only). Build the archive block and
    # the per-file deletion plan.
    by_file: dict[str, list[int]] = {}
    guards: dict[tuple, str] = {}
    archive_lines: list[str] = []
    skipped = 0
    for it in items or []:
        f = (it.get("file") or "").replace("\\", "/")
        try:
            ln = int(it.get("line"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        abs_src = self._abs_path(f) if f else None
        if not abs_src or ln < 1:
            skipped += 1
            continue
        try:
            lines = await self._read_lines(abs_src)
        except Exception:
            skipped += 1
            continue
        if ln > len(lines) or "- [" not in lines[ln - 1]:
            skipped += 1
            continue
        raw = lines[ln - 1]
        want_text = (it.get("text") or "").strip()
        if want_text and mutations.task_text(raw) != want_text:
            skipped += 1  # stale line — refuse
            continue
        archive_lines.append(raw.strip())
        archive_lines.append(f"  - archived {today} from {f}:{ln}")
        by_file.setdefault(f, []).append(ln)
        guards[(f, ln)] = mutations.task_text(raw)

    if not archive_lines:
        return {"ok": True, "archived": 0, "skipped": skipped}

    # Pass 2 — append to the archive note FIRST (duplicate-over-loss).
    async with self.write_lock(archive_rel):
        try:
            existing = await self.read(archive_abs)
        except Exception:
            existing = ""
        head = "" if existing.endswith("\n") or not existing else "\n"
        block = f"{head}\n## Archived {today}\n\n" + "\n".join(archive_lines) + "\n"
        await self.write(archive_abs, (existing + block) if existing else
                         ("---\nauthor: both\ntags:\n  - task-archive\n---\n" + block))

    # Pass 3 — remove from sources under a per-file lock, re-verifying the
    # line inside the lock (a concurrent reactor write may have shifted it).
    archived = 0
    for f, lns in by_file.items():
        abs_src = self._abs_path(f)
        async with self._file_lock(f):
            try:
                src = await self._read_lines(abs_src)
            except Exception:
                continue
            for ln in sorted(lns, reverse=True):  # desc keeps indices valid
                if ln <= len(src) and "- [" in src[ln - 1] \
                        and mutations.task_text(src[ln - 1]) == guards.get((f, ln), ""):
                    del src[ln - 1]
                    archived += 1
            await self._write_lines(abs_src, src)
        await self.emit("task:archived", {"file": f, "count": len(lns)})

    return {"ok": True, "archived": archived, "skipped": skipped, "archive_note": archive_rel}


@web_route("POST", "/api/archive")
async def api_archive(self, request):
    """Archive one task — body ``{file, line, text?}``."""
    data = await request.json()
    return await self.archive_tasks([{
        "file": data.get("file", ""),
        "line": data.get("line"),
        "text": data.get("text", ""),
    }])


@web_route("POST", "/api/archive-batch")
async def api_archive_batch(self, request):
    """Archive many tasks — body ``{items: [{file, line, text?}, ...]}``."""
    data = await request.json()
    items = data.get("items")
    if not isinstance(items, list):
        return {"error": "items must be a list of {file, line, text?}"}
    return await self.archive_tasks(items)
