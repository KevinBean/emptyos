"""Task scanning + cache.

Scans local folders directly and pulls delegated tasks from authority
apps (projects, journal). Caches the merged index in memory + on disk
(``data/apps/task/task-index.json``).

The ``Task`` dataclass is the wire shape consumed by ``list_tasks``;
the cache stores plain dicts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from emptyos.sdk import DUE_INLINE_PATTERN, TASK_RE, task_tier

from .queries import CONTEXT_KEYWORDS, classify_actionability, compute_urgency

log = logging.getLogger("emptyos.task.indexer")

CACHE_TTL = 300  # 5 minutes


@dataclass
class Task:
    text: str
    done: bool
    file: str
    line: int
    due: str = ""
    done_date: str = ""
    overdue_days: int = 0
    tier: str = "fresh"
    focus_score: int = 0
    mtime: float = 0.0
    actionability: str = "someday"

    def to_dict(self):
        return {
            "text": self.text,
            "done": self.done,
            "file": self.file,
            "line": self.line,
            "due": self.due,
            "done_date": self.done_date,
            "overdue_days": self.overdue_days,
            "tier": self.tier,
            "focus_score": self.focus_score,
            "mtime": self.mtime,
            "actionability": self.actionability,
        }


def focus_score(text: str, due: str, today: date) -> int:
    score = 0
    if not due:
        return 0
    try:
        due_date = date.fromisoformat(due)
    except (ValueError, TypeError):
        return 0
    delta = (due_date - today).days

    if delta == 0:
        score = 50
    elif 0 < delta <= 7:
        score = 30
    elif delta < 0:
        days_overdue = abs(delta)
        if days_overdue > 90:
            score = 1
        elif days_overdue > 30:
            score = 5
        else:
            score = 20 + min(days_overdue, 30)

    text_lower = text.lower()
    for ctx, keywords in CONTEXT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            score += 10
            break

    return score


def attach_mtimes(tasks: list[dict], notes_dir: Path | None) -> None:
    """Stamp each task's source-file mtime (epoch secs) onto ``t["mtime"]``.

    The undated ("No Due Date") group on the task page sorts by this so a
    freshly captured task surfaces at the top instead of being buried below
    the row cap by alphabetical file order. Mutates ``tasks`` in place; stats
    each unique file once (the caller caches the whole result for CACHE_TTL).
    A missing/unreadable file yields ``0.0`` (sorts to the bottom).
    """
    if not notes_dir:
        for t in tasks:
            t.setdefault("mtime", 0.0)
        return
    mtime_cache: dict[str, float] = {}
    for t in tasks:
        rel = t.get("file", "")
        if rel not in mtime_cache:
            try:
                mtime_cache[rel] = (notes_dir / rel).stat().st_mtime
            except OSError:
                mtime_cache[rel] = 0.0
        t["mtime"] = mtime_cache[rel]


class TaskIndexer:
    """Owns the in-memory + on-disk task cache. App holds one instance."""

    def __init__(self, app):
        self.app = app
        self._cache: list[dict] | None = None
        self._cache_time: float = 0

    # ── config-derived paths ──

    def _notes_dir(self) -> Path | None:
        return self.app.kernel.config.notes_path

    def _local_folders(self) -> list[str]:
        raw = self.app.vault_config("scan_folders", "00_Inbox,20_Areas")
        return [f.strip() for f in raw.split(",") if f.strip()]

    def _scan_exclude(self) -> set[str]:
        """Directory names whose subtree is skipped by the local scan.

        Template/skill notes carry `- [ ]` EXAMPLE lines that are not real
        to-dos — before this exclusion they polluted the index badly enough
        that "312 overdue" was mostly boilerplate. User-tunable via the
        vault-map key `scan_exclude` (comma-separated directory names).
        """
        raw = self.app.vault_config(
            "scan_exclude",
            ".git,.space,.claude,.obsidian,__pycache__,node_modules,"
            "templates,_templates,Templates,skills,_archive",
        )
        return {f.strip() for f in raw.split(",") if f.strip()}

    def _index_path(self) -> Path:
        return self.app.data_dir / "task-index.json"

    # ── scan ──

    def _scan_local_folders(self) -> list[dict]:
        notes = self._notes_dir()
        if not notes or not notes.exists():
            return []

        today = date.today()
        tasks: list[dict] = []
        exclude = self._scan_exclude()
        for folder in self._local_folders():
            folder_path = notes / folder
            if not folder_path.exists():
                continue
            for md_file in folder_path.rglob("*.md"):
                # Skip template/skill/system subtrees — their `- [ ]` lines
                # are examples, not tasks.
                if exclude and exclude.intersection(md_file.relative_to(notes).parts[:-1]):
                    continue
                try:
                    content = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                rel_path = str(md_file.relative_to(notes))
                for i, raw_line in enumerate(content.split("\n"), 1):
                    m = TASK_RE.match(raw_line.strip())
                    if not m:
                        continue
                    is_done = m.group(1) in ("x", "X")
                    text = m.group(2).strip()
                    due_str = m.group(3) or ""
                    done_date = m.group(4) or ""
                    if not due_str:
                        m2 = DUE_INLINE_PATTERN.search(text)
                        if m2:
                            due_str = m2.group(1)

                    overdue_days = 0
                    tier = "fresh"
                    fscore = 0
                    if due_str and not is_done:
                        try:
                            due_date = date.fromisoformat(due_str[:10])
                            overdue_days = (today - due_date).days
                            if overdue_days < 0:
                                overdue_days = 0
                            tier = task_tier(overdue_days)
                            fscore = focus_score(text, due_str[:10], today)
                        except (ValueError, TypeError):
                            pass

                    tasks.append(
                        {
                            "text": text,
                            "done": is_done,
                            "file": rel_path,
                            "line": i,
                            "due": due_str,
                            "done_date": done_date,
                            "overdue_days": overdue_days,
                            "tier": tier,
                            "focus_score": fscore,
                        }
                    )
        return tasks

    async def _fetch_delegated(self) -> list[dict]:
        """Fetch tasks from authority apps (projects, journal)."""
        today = date.today()

        async def _from_projects():
            try:
                return await self.app.call_app("projects", "get_all_tasks")
            except Exception as e:
                log.warning("Failed to fetch project tasks: %s", e)
                return []

        async def _from_journal():
            try:
                return await self.app.call_app("journal", "get_tasks", days=90)
            except Exception as e:
                log.warning("Failed to fetch journal tasks: %s", e)
                return []

        project_tasks, journal_tasks = await asyncio.gather(_from_projects(), _from_journal())
        tasks: list[dict] = []
        for source in (project_tasks, journal_tasks):
            if not isinstance(source, list):
                continue
            for t in source:
                t["focus_score"] = (
                    focus_score(t.get("text", ""), t.get("due", ""), today)
                    if t.get("due") and not t.get("done")
                    else 0
                )
            tasks.extend(source)
        return tasks

    async def _scan_vault(self) -> tuple[list[dict], list[dict]]:
        # OFF-LOOP: _scan_local_folders is a synchronous rglob + read_text over
        # every markdown file in the scanned folders. On a large vault that is
        # seconds-to-minutes of blocking I/O, and awaiting it inline pins the
        # event loop — the daemon stops answering /api/health, the watchdog
        # calls it wedged and restarts it. Caught by py-spy on 2026-07-25
        # (MainThread parked in read_text under api_stats). See
        # .claude/rules/debugging.md § sync call in async context.
        local = await asyncio.to_thread(self._scan_local_folders)
        delegated = await self._fetch_delegated()

        # Dedup by (file, line) — a task can only exist once at a given line.
        # Normalize separators first: delegated sources (projects, journal)
        # return raw ``relative_to`` paths, which are backslashed on Windows.
        # Backslashes break the page's inline onclick JS (octal escapes), so
        # forward slashes are the wire format everywhere.
        seen: dict[tuple, dict] = {}
        for t in local + delegated:
            f = t.get("file", "")
            if isinstance(f, str) and "\\" in f:
                t["file"] = f.replace("\\", "/")
            key = (t.get("file", ""), t.get("line", 0))
            if key not in seen:
                seen[key] = t
        all_tasks = list(seen.values())

        attach_mtimes(all_tasks, self._notes_dir())

        # Stamp the actionability axis on every task (pure, works on local +
        # delegated dicts alike; mtime is already attached for someday sort),
        # then compute the urgency score centrally — now that each task has
        # due + actionability, this supersedes the per-scan-site focus_score
        # and scores dateless #next tasks too (the old formula returned 0).
        today = date.today()
        for t in all_tasks:
            t["actionability"] = classify_actionability(t)
            t["focus_score"] = compute_urgency(t, today)

        open_tasks = [t for t in all_tasks if not t["done"]]
        done_tasks = [t for t in all_tasks if t["done"]]
        open_tasks.sort(key=lambda t: (t["due"] or "9999", t["file"]))
        done_tasks.sort(key=lambda t: t["done_date"] or "0000", reverse=True)
        return open_tasks, done_tasks

    # ── cache ──

    async def get(self) -> tuple[list[dict], list[dict]]:
        """Return cached results or re-scan if stale."""
        now = time.time()
        index_path = self._index_path()

        if now - self._cache_time < CACHE_TTL and self._cache is not None:
            idx = self._cache
            return [t for t in idx if not t["done"]], [t for t in idx if t["done"]]

        if index_path.exists() and now - index_path.stat().st_mtime < CACHE_TTL:
            try:
                idx = json.loads(index_path.read_text(encoding="utf-8"))
                self._cache = idx
                self._cache_time = now
                return [t for t in idx if not t["done"]], [t for t in idx if t["done"]]
            except Exception:
                pass

        open_tasks, done_tasks = await self._scan_vault()
        all_tasks = open_tasks + done_tasks
        try:
            new_text = json.dumps(all_tasks, ensure_ascii=False, indent=2)
            old_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
            if new_text != old_text:
                index_path.write_text(new_text, encoding="utf-8")
        except Exception:
            pass
        self._cache = all_tasks
        self._cache_time = now
        return open_tasks, done_tasks

    def invalidate(self):
        # Drop BOTH caches. Memory-only invalidation is a trap: get() falls
        # back to the disk cache whenever its mtime is < CACHE_TTL, so a
        # freshly written task-index.json would resurrect the stale data we
        # just invalidated (completed tasks reappearing after toggle).
        self._cache = None
        self._cache_time = 0
        self.drop_disk_cache()

    def drop_disk_cache(self):
        try:
            self._index_path().unlink(missing_ok=True)
        except OSError:
            pass
