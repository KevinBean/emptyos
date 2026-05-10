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

from emptyos.sdk import TASK_RE, task_tier

from .queries import CONTEXT_KEYWORDS

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

    def _index_path(self) -> Path:
        return self.app.data_dir / "task-index.json"

    # ── scan ──

    def _scan_local_folders(self) -> list[dict]:
        notes = self._notes_dir()
        if not notes or not notes.exists():
            return []

        today = date.today()
        tasks: list[dict] = []
        for folder in self._local_folders():
            folder_path = notes / folder
            if not folder_path.exists():
                continue
            for md_file in folder_path.rglob("*.md"):
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
        local = self._scan_local_folders()
        delegated = await self._fetch_delegated()

        # Dedup by (file, line) — a task can only exist once at a given line.
        seen: dict[tuple, dict] = {}
        for t in local + delegated:
            key = (t.get("file", ""), t.get("line", 0))
            if key not in seen:
                seen[key] = t
        all_tasks = list(seen.values())

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
        self._cache = None
        self._cache_time = 0

    def drop_disk_cache(self):
        idx = self._index_path()
        if idx.exists():
            idx.unlink()
