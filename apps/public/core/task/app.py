"""Task Manager — find, add, complete, snooze tasks across notes.

Decomposed into:
- ``indexer.py``  — vault scan + delegated fetch + cache
- ``mutations.py`` — pure markdown checkbox line transforms
- ``queries.py``  — pure aggregations over task dicts
- this file      — BaseApp orchestrator: lifecycle, web routes, CLI, voice intents, hub panels
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, on_event, scheduled, web_route

from . import archive as _archive
from . import mutations, queries
from . import triage as _triage
from . import voice as _voice
from .indexer import Task, TaskIndexer

log = logging.getLogger("emptyos.task")


TASK_SUGGEST_SYSTEM = (
    "You are a calm prioritisation assistant. Given a flat list of tasks "
    "with optional due dates, recommend what the user should do today. "
    "Output a single short paragraph (2-3 sentences) that names the top "
    "two or three tasks and a one-clause reason for each.\n\n"
    "Do NOT:\n"
    "- Use bullet points, headings, or numbered lists.\n"
    "- Recommend more than three items.\n"
    "- Echo the input list back verbatim.\n"
    "- Moralise about productivity or workload."
)


class TaskApp(BaseApp):

    SETTABLE_FIELDS = frozenset({"done", "due", "text", "actionability", "priority"})

    def _recurrence_enabled(self) -> bool:
        """Live ⚙-toggle first, then emptyos.toml; default OFF (ships dark)."""
        v = self.setting("task.feature.recurrence.enabled", None)
        if v is not None:
            return bool(v)
        return bool(self.app_config("feature.recurrence.enabled", False))

    def _nl_quickadd_enabled(self) -> bool:
        """Live ⚙-toggle first, then emptyos.toml; default OFF (ships dark)."""
        v = self.setting("task.feature.nl-quickadd.enabled", None)
        if v is not None:
            return bool(v)
        return bool(self.app_config("feature.nl-quickadd.enabled", False))

    def _reminder_push_enabled(self) -> bool:
        """Daily due-task summary toggle (dark default)."""
        live = self.setting("task.feature.task-reminders-push.enabled", None)
        if live is not None:
            return bool(live)
        return bool(self.app_config("feature.task-reminders-push.enabled", False))

    def _regen_recurring(self, original_line: str) -> str | None:
        """Fresh open line for a recurring task's next occurrence, or None.

        Gated by the recurrence feature flag — returns None (no regeneration,
        byte-identical completion behaviour) when the flag is off. ``original``
        is the line as it was *before* completion.
        """
        if not self._recurrence_enabled():
            return None
        return mutations.regenerate_recurring(original_line, date.today())

    async def setup(self):
        await super().setup()
        self._idx = TaskIndexer(self)
        # Recent-adds ring — voice "list_recent" surfaces these so newly added
        # tasks without a due date are visible (the prioritised today-list
        # buries them behind overdue ones).
        self._recent_adds: deque[dict] = deque(maxlen=20)
        try:
            persisted = (self.data_dir / "recent_adds.json").read_text(encoding="utf-8")
            for item in json.loads(persisted):
                if isinstance(item, dict) and item.get("text"):
                    self._recent_adds.append(item)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _persist_recent_adds(self):
        try:
            (self.data_dir / "recent_adds.json").write_text(
                json.dumps(list(self._recent_adds)), encoding="utf-8"
            )
        except Exception:
            pass

    def _notes_dir(self) -> Path | None:
        return self.kernel.config.notes_path

    def _abs_path(self, rel: str) -> str | None:
        notes = self._notes_dir()
        return str(notes / rel) if notes else None

    async def _read_lines(self, abs_path: str) -> list[str]:
        content = await self.read(abs_path)
        return content.split("\n")

    async def _write_lines(self, abs_path: str, lines: list[str]):
        await self.write(abs_path, "\n".join(lines))
        self._idx.invalidate()

    def _file_lock(self, file_rel: str):
        """Lock for serialising a read-modify-write of ``file_rel``.

        ``write_lock`` keys are per-app-instance (``BaseApp._write_locks`` is a
        cached_property), so a task-app write and a projects-app write to the
        *same* project note only mutually exclude if they acquire the **same**
        lock object. For files under the projects dir we therefore borrow the
        projects app's own lock, keyed exactly as projects keys it
        (``projects:{id}``); otherwise we use our own per-file lock. Without
        this, toggling a project task here races ``add_task_to_project`` /
        reactor writes and one clobbers the other (CLAUDE.md § Development
        Gotchas — vault read-modify-write races)."""
        norm = (file_rel or "").replace("\\", "/")
        parts = norm.split("/")
        if len(parts) >= 2 and parts[0].lower() in ("10_projects", "projects"):
            # Directory project (10_Projects/<id>/...) → id is the dir name;
            # legacy flat project (10_Projects/<id>.md) → id is the stem, which
            # is how projects keys its lock (_find_project_file → target.stem).
            seg = parts[1]
            pid = seg[:-3] if (len(parts) == 2 and seg.endswith(".md")) else seg
            apps = getattr(getattr(self, "kernel", None), "apps", None)
            proj = apps.instances.get("projects") if apps is not None else None
            if proj is not None:
                return proj.write_lock(f"projects:{pid}")
        return self.write_lock(f"task:{norm}")

    @on_event("vault:changed")
    async def _on_vault_changed(self, event):
        """Hand-edited checkboxes must surface before the 5-min cache TTL."""
        path = event.data.get("path") or ""
        if path.endswith(".md"):
            self._idx.invalidate()

    async def companion_context(self) -> str | None:
        """Focus-mode live context — open tasks the user is looking at."""
        try:
            open_tasks, _ = await self._idx.get()
        except Exception:
            return None
        if not open_tasks:
            return "No open tasks right now."
        lines = [f"{len(open_tasks)} open task(s). Most recent:"]
        for t in open_tasks[:5]:
            txt = (t.get("text") or "").strip()
            if txt:
                lines.append(f"- {txt}")
        return "\n".join(lines)

    async def panel_pulse_stats(self) -> list[dict]:
        open_tasks, done_tasks = await self._idx.get()
        return queries.pulse_stats(open_tasks, done_tasks, date.today())

    async def panel_todays_tasks(self) -> list[dict] | None:
        open_tasks, _ = await self._idx.get()
        return queries.todays_tasks_rows(open_tasks, date.today(), limit=5)

    async def slot_needs_attention(self) -> list[dict]:
        open_tasks, _ = await self._idx.get()
        return queries.needs_attention_slot(open_tasks)

    async def slot_today(self) -> list[dict]:
        open_tasks, _ = await self._idx.get()
        return queries.due_today_slot(open_tasks, date.today())

    async def list_tasks(self, overdue_only: bool = False, done: bool = False) -> list[Task]:
        open_tasks, done_tasks = await self._idx.get()
        source = done_tasks if done else open_tasks
        out: list[Task] = []
        for t in source:
            if overdue_only and t.get("overdue_days", 0) <= 0:
                continue
            out.append(
                Task(
                    text=t["text"],
                    done=t["done"],
                    file=t["file"],
                    line=t["line"],
                    due=t.get("due", ""),
                    done_date=t.get("done_date", ""),
                    overdue_days=t.get("overdue_days", 0),
                    tier=t.get("tier", "fresh"),
                    focus_score=t.get("focus_score", 0),
                )
            )
        return out

    @scheduled("5 8 * * *", id="task-reminders-push")
    async def scheduled_task_reminders_push(self):
        """Push one bounded daily summary for due and overdue open tasks."""
        if not self._reminder_push_enabled():
            return {"enabled": False, "count": 0}
        open_tasks, _ = await self._idx.get()
        today = date.today().isoformat()
        due = []
        for task in open_tasks:
            due_day = (task.get("due") or "")[:10]
            try:
                parsed_due = date.fromisoformat(due_day)
            except ValueError:
                continue
            if parsed_due <= date.today():
                due.append(task)
        due.sort(key=lambda t: ((t.get("due") or "")[:10], -t.get("focus_score", 0)))
        if not due:
            return {"enabled": True, "count": 0}
        overdue = sum(1 for task in due if (task.get("due") or "")[:10] < today)
        top = "; ".join((task.get("text") or "").strip()[:80] for task in due[:3])
        label = f"{len(due)} task{'s' if len(due) != 1 else ''} due"
        if overdue:
            label += f" ({overdue} overdue)"
        await self.proactive_notify_or_raw(
            kind="task-reminder",
            text=f"{label}: {top}",
            dedup_key=f"task-reminders:{today}",
            priority="high" if overdue else "info",
            source="task",
        )
        return {"enabled": True, "count": len(due), "overdue": overdue}

    async def add(
        self,
        text: str,
        file: str = "",
        due: str = "",
        project: str = "",
        done: bool = False,
    ) -> Task:
        """Add a task. Routes to a project when no explicit file is given."""
        if project or not file:
            target_project = project or "inbox"
            result = await self.call_app(
                "projects",
                "add_task_to_project",
                project_id=target_project,
                text=text,
                due=due,
                done=done,
            )
            if result.get("error"):
                raise RuntimeError(result["error"])
            display_text = f"{text} 📅 {due}" if due else text
            projects_dir = self.vault_config("projects_dir", "10_Projects")
            task = Task(
                text=display_text,
                done=done,
                file=f"{projects_dir}/{target_project}.md",
                line=0,
                due=due,
            )
            self._record_recent_add(text=display_text, due=due, file=task.file)
            await self.emit("task:completed" if done else "task:added", task.to_dict())
            self._idx.invalidate()
            return task

        if due:
            text = f"{text} 📅 {due}"
        line = f"- [x] {text} ✅ {date.today().isoformat()}\n" if done else f"- [ ] {text}\n"
        # Serialize read→append→write on this (possibly shared) file so two
        # concurrent explicit-file adds can't both read the pre-write content
        # and drop the earlier line. emit stays OUTSIDE the lock.
        async with self._file_lock(file):
            existing = await self.read(file)
            body = existing.rstrip("\n")
            # 1-based line number of the task we're about to append, so the
            # emitted payload is addressable without waiting for a rescan.
            new_line_num = body.count("\n") + 2
            await self.write(file, body + "\n" + line)

        task = Task(text=text, done=done, file=file, line=new_line_num, due=due)
        self._record_recent_add(text=text, due=due, file=file)
        await self.emit("task:completed" if done else "task:added", task.to_dict())
        self._idx.invalidate()
        return task

    def _record_recent_add(self, *, text: str, due: str, file: str):
        self._recent_adds.append(
            {
                "text": text,
                "due": due or "",
                "file": file,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._persist_recent_adds()

    async def complete(self, query: str) -> Task | None:
        return await self._fuzzy_mutate(query, want_done=False, op="complete")

    async def reopen(self, query: str) -> Task | None:
        return await self._fuzzy_mutate(query, want_done=True, op="reopen")

    async def snooze(self, query: str, days: int = 7) -> Task | None:
        return await self._fuzzy_mutate(query, want_done=False, op="snooze", days=days)

    async def note(self, query: str, note: str) -> Task | None:
        """Fuzzy-match a task (open OR done) by text and attach a note.

        Mirrors the UI's line-based ``/api/complete-with-note`` semantics,
        but resolved by fuzzy text match instead of an exact (file, line) —
        the shape an agent/voice/mcp caller actually has in hand. An open
        task is completed and the note attached; an already-done task just
        gets the note appended.
        """
        note_lines = mutations.completion_note_lines(note)
        if not note_lines:
            return None
        candidates = await self.list_tasks() + await self.list_tasks(done=True)
        ql = query.lower()
        match = next((t for t in candidates if ql in t.text.lower()), None)
        if not match:
            return None

        abs_path = self._abs_path(match.file)
        if not abs_path:
            return None
        today_str = date.today().isoformat()
        status = "completed"

        async with self._file_lock(match.file):
            lines = await self._read_lines(abs_path)
            idx = next((i for i, ln in enumerate(lines) if mutations.matches(ln, match.text)), None)
            if idx is None:
                return None
            if mutations.is_open(lines[idx]):
                lines[idx] = mutations.complete(lines[idx], today_str)
            else:
                status = "noted"
            lines[idx + 1 : idx + 1] = note_lines
            await self._write_lines(abs_path, lines)

        match.done = True
        await self.emit("task:completed" if status == "completed" else "task:updated", match.to_dict())
        return match

    async def _fuzzy_mutate(self, query: str, want_done: bool, op: str, **kw) -> Task | None:
        """Find a task by fuzzy text match and apply a line transform."""
        tasks = await self.list_tasks(done=want_done)
        ql = query.lower()
        match = next((t for t in tasks if ql in t.text.lower()), None)
        if not match:
            return None

        abs_path = self._abs_path(match.file)
        if not abs_path:
            return None
        today_str = date.today().isoformat()
        new_due = ""
        regenerated = None

        async with self._file_lock(match.file):
            lines = await self._read_lines(abs_path)
            for i, ln in enumerate(lines):
                if not mutations.matches(ln, match.text, want_done=want_done):
                    continue
                if op == "complete":
                    lines[i] = mutations.complete(ln, today_str)
                    regenerated = self._regen_recurring(ln)
                    if regenerated:
                        lines.insert(i + 1, regenerated)
                elif op == "reopen":
                    lines[i] = mutations.reopen(ln)
                elif op == "snooze":
                    new_line = mutations.snooze(ln, kw["days"])
                    lines[i] = new_line
                    # Recover the new due-date for the emit payload.
                    from emptyos.sdk import DUE_PATTERN

                    m = DUE_PATTERN.search(new_line)
                    new_due = m.group(1) if m else ""
                break

            await self._write_lines(abs_path, lines)
        if op == "complete":
            match.done = True
            await self.emit("task:completed", match.to_dict())
            if regenerated:
                await self.emit("task:recurred", match.to_dict())
        elif op == "reopen":
            match.done = False
            await self.emit("task:reopened", match.to_dict())
        elif op == "snooze":
            await self.emit(
                "task:snoozed", {**match.to_dict(), "new_due": new_due, "days": kw["days"]}
            )
        return match

    @cli_command("task", help="Manage tasks")
    async def cmd_task(self, action: str = "list", text: str = "", due: str = "", file: str = ""):
        if action == "add" and text:
            t = await self.add(text, file, due)
            self.print_rich(f"[green]Added:[/green] {t.text}")
        elif action == "done" and text:
            t = await self.complete(text)
            if t:
                self.print_rich(f"[green]Done:[/green] {t.text}")
            else:
                self.print_rich(f"[red]No matching task:[/red] {text}")
        elif action == "list":
            tasks = await self.list_tasks()
            if not tasks:
                self.print_rich("[dim]No open tasks.[/dim]")
                return
            for t in tasks[:30]:
                due_str = f" [dim]📅 {t.due}[/dim]" if t.due else ""
                self.print_rich(f"  [ ] {t.text}{due_str}")
                self.print_rich(f"      [dim]{t.file}[/dim]")
        elif action == "overdue":
            tasks = await self.list_tasks(overdue_only=True)
            if not tasks:
                self.print_rich("[green]No overdue tasks.[/green]")
                return
            for t in tasks:
                self.print_rich(f"  [red][ ] {t.text} 📅 {t.due}[/red]")
        elif action == "suggest":
            tasks = await self.list_tasks()
            task_text = "\n".join(f"- {t.text} (due: {t.due or 'none'})" for t in tasks[:20])
            suggestion = await self.think(
                f"Tasks:\n{task_text}",
                system=TASK_SUGGEST_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            print(suggestion)
        else:
            self.print_rich(
                "[dim]Usage: eos task {add|done|list|overdue|suggest} [text] [--due DATE][/dim]"
            )

    def _apply_filter(self, tasks: list[dict], request) -> list[dict] | dict:
        """?filter=today|overdue|tomorrow|this_week|later|undated narrows to
        one agenda bucket. Returns the bucket, an error dict, or the input."""
        flt = (request.query_params.get("filter") or "").strip().lower()
        if not flt:
            return tasks
        buckets = queries.agenda(tasks, date.today())
        if flt in buckets:
            return buckets[flt]
        return {"error": f"unknown filter '{flt}'", "available": list(buckets)}

    @web_route("GET", "/api/tasks")
    async def api_tasks(self, request):
        status = request.query_params.get("status", "open")
        open_tasks, done_tasks = await self._idx.get()
        tasks = done_tasks[:200] if status == "done" else open_tasks
        return self._apply_filter(tasks, request)

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        items = [t.to_dict() for t in await self.list_tasks()]
        return self._apply_filter(items, request)

    @web_route("GET", "/api/focus-view")
    async def api_focus_view(self, request):
        """Open tasks grouped by actionability (next / waiting / someday),
        plus inferred life-domain counts for the wellbeing readout.

        The primary organizing axis — collapses a capture-graveyard backlog
        into a small Next list + a Waiting list + a collapsed Someday bin.
        """
        open_tasks, _ = await self._idx.get()
        groups = queries.group_by_actionability(open_tasks)
        return {
            "groups": groups,
            "counts": {k: len(v) for k, v in groups.items()},
            "balance": queries.domain_balance(open_tasks),
            "total": len(open_tasks),
        }

    @web_route("GET", "/api/today")
    async def api_today(self, request):
        open_tasks, _ = await self._idx.get()
        buckets = queries.agenda(open_tasks, date.today())
        return {
            "today": buckets.get("today", []),
            "overdue": buckets.get("overdue", []),
            "count": len(buckets.get("today", [])) + len(buckets.get("overdue", [])),
        }

    @web_route("GET", "/api/overdue")
    async def api_overdue(self, request):
        open_tasks, _ = await self._idx.get()
        buckets = queries.agenda(open_tasks, date.today())
        return {"tasks": buckets.get("overdue", []), "count": len(buckets.get("overdue", []))}

    @web_route("GET", "/api/tomorrow")
    async def api_tomorrow(self, request):
        open_tasks, _ = await self._idx.get()
        buckets = queries.agenda(open_tasks, date.today())
        return {"tasks": buckets.get("tomorrow", []), "count": len(buckets.get("tomorrow", []))}

    @web_route("POST", "/api/refresh")
    async def api_refresh(self, request):
        self._idx.invalidate()
        self._idx.drop_disk_cache()
        open_tasks, done_tasks = await self._idx.get()
        return {"open": len(open_tasks), "done": len(done_tasks), "status": "refreshed"}

    @web_route("POST", "/api/add")
    async def api_add(self, request):
        """Add a task from the web UI — body ``{text, due?, project?}``.

        Routes through ``add()`` (projects inbox by default), same as the
        CLI and cross-app callers.
        """
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "text is required"}
        due = (data.get("due") or "").strip()
        # Natural-language quick-add: extract a due date + priority from the
        # typed text (deterministic, no LLM). Dark-flagged — off ⇒ the text is
        # used verbatim, byte-identical to before. An explicit ``due`` param
        # always wins over a parsed one.
        if self._nl_quickadd_enabled():
            parsed = queries.parse_quick_add(text)
            text = parsed["text"] or text
            if not due:
                due = parsed["due"]
            if parsed["priority"]:
                text = f"{text} {mutations.PRIORITY_EMOJI[parsed['priority']]}"
        try:
            task = await self.add(
                text,
                due=due,
                project=(data.get("project") or "").strip(),
            )
        except RuntimeError as e:
            return {"error": str(e)}
        return task.to_dict()

    @web_route("GET", "/api/someday-sample")
    async def api_someday_sample(self, request):
        """A review-ritual sample of the someday backlog — ``?n=5`` (cap 20).

        Oldest-biased so the ancient tail gets seen; the client renders
        Promote / Snooze / Archive / Keep per row against existing endpoints.
        """
        try:
            n = int(request.query_params.get("n", 5) or 5)
        except ValueError:
            n = 5
        n = max(1, min(n, 20))
        open_tasks, _ = await self._idx.get()
        someday = queries.group_by_actionability(open_tasks)["someday"]
        return {"items": queries.someday_sample(someday, n), "total": len(someday)}

    @web_route("POST", "/api/snooze-batch")
    async def api_snooze_batch(self, request):
        """Bulk-reschedule open tasks — body ``{items: [{file, line}], days}``.

        Grouped per file with one locked read-modify-write each (same shape
        as ``archive_tasks``). Lines that are no longer open checkboxes are
        skipped rather than mangled.
        """
        data = await request.json()
        items = data.get("items")
        if not isinstance(items, list):
            return {"error": "items must be a list of {file, line}"}
        try:
            days = int(data.get("days", 7))
        except (TypeError, ValueError):
            return {"error": "days must be an integer"}

        by_file: dict[str, list[int]] = {}
        skipped = 0
        for it in items:
            f = (it.get("file") or "").replace("\\", "/") if isinstance(it, dict) else ""
            try:
                ln = int(it.get("line"))
            except (TypeError, AttributeError, ValueError):
                skipped += 1
                continue
            if not f or ln < 1:
                skipped += 1
                continue
            by_file.setdefault(f, []).append(ln)

        rescheduled = 0
        for f, lns in by_file.items():
            abs_src = self._abs_path(f)
            if not abs_src:
                skipped += len(lns)
                continue
            async with self._file_lock(f):
                try:
                    lines = await self._read_lines(abs_src)
                except Exception:
                    skipped += len(lns)
                    continue
                changed = False
                for ln in lns:
                    if 1 <= ln <= len(lines) and mutations.is_open(lines[ln - 1]):
                        lines[ln - 1] = mutations.snooze(lines[ln - 1], days)
                        rescheduled += 1
                        changed = True
                    else:
                        skipped += 1
                if changed:
                    await self._write_lines(abs_src, lines)
        if rescheduled:
            await self.emit(
                "task:updated", {"batch": "snooze", "count": rescheduled, "days": days}
            )
        return {"ok": True, "rescheduled": rescheduled, "skipped": skipped}

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        """AI 'what should I do today' — the CLI ``eos task suggest``, on the
        web, fed by urgency ranking instead of raw index order. Cached per
        (day, task set); ``?live=1`` forces a fresh model call.
        """
        open_tasks, _ = await self._idx.get()
        today = date.today()
        ranked = sorted(
            open_tasks, key=lambda t: queries.compute_urgency(t, today), reverse=True
        )[:20]
        if not ranked:
            return {"suggestion": "", "from_cache": False}
        task_text = "\n".join(f"- {t.get('text', '')} (due: {t.get('due') or 'none'})" for t in ranked)
        suggestion, from_cache = await self.think_cached(
            f"Tasks:\n{task_text}",
            key={"kind": "suggest", "date": today.isoformat(), "tasks": task_text},
            system=TASK_SUGGEST_SYSTEM,
            domain="text",
            temperature=0.4,
            force_live=request.query_params.get("live") == "1",
        )
        return {
            "suggestion": suggestion,
            "from_cache": from_cache,
            "provenance": self.last_provenance() if not from_cache else None,
        }

    @web_route("POST", "/api/attach-room")
    async def api_attach_room(self, request):
        """Append (or replace) a 🗨️ <room_id> marker on a task line so the
        room sees it under its attached-tasks panel. Mirrors the file+line
        addressing used by snooze/toggle. POST {file, line, room_id} or
        {file, line, room_id: ""} to detach.
        """
        import re
        data = await request.json()
        file_rel = data.get("file", "")
        line_num = int(data.get("line", 0))
        room_id = (data.get("room_id") or "").strip()

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        lines = await self._read_lines(abs_path)
        if line_num < 1 or line_num > len(lines):
            return {"error": f"Line {line_num} out of range"}

        # Strip any existing room marker first — one task, one room.
        cleaned = re.sub(r"\s*\U0001f5e8️?\s*[A-Za-z0-9_\-]+", "", lines[line_num - 1]).rstrip()
        if room_id:
            cleaned = f"{cleaned} \U0001f5e8️ {room_id}"
        lines[line_num - 1] = cleaned
        await self._write_lines(abs_path, lines)
        return {
            "status": "attached" if room_id else "detached",
            "file": file_rel, "line": line_num, "room_id": room_id,
        }

    @web_route("POST", "/api/snooze")
    async def api_snooze(self, request):
        data = await request.json()
        file_rel = data.get("file", "")
        line_num = int(data.get("line", 0))
        days = int(data.get("days", 7))

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        async with self._file_lock(file_rel):
            lines = await self._read_lines(abs_path)
            if line_num < 1 or line_num > len(lines):
                return {"error": f"Line {line_num} out of range"}

            new_line = mutations.snooze(lines[line_num - 1], days)
            lines[line_num - 1] = new_line
            await self._write_lines(abs_path, lines)

        from emptyos.sdk import DUE_PATTERN

        m = DUE_PATTERN.search(new_line)
        new_due = m.group(1) if m else ""
        return {"status": "snoozed", "file": file_rel, "line": line_num, "new_due": new_due}

    @web_route("POST", "/api/complete-with-note")
    async def api_complete_with_note(self, request):
        data = await request.json()
        file_rel = (data.get("file") or "").strip()
        expected_text = str(data.get("text") or "").strip()
        try:
            line_num = int(data.get("line", 0))
        except (TypeError, ValueError):
            line_num = 0
        note_lines = mutations.completion_note_lines(str(data.get("note") or ""))
        if not note_lines:
            return {"error": "note is required"}
        if not expected_text:
            return {"error": "task text is required for verification"}

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}

        status = "completed"
        async with self._file_lock(file_rel):
            lines = await self._read_lines(abs_path)
            if line_num < 1 or line_num > len(lines):
                return {"error": f"Line {line_num} out of range"}

            target = lines[line_num - 1]
            if not mutations.text_matches(target, expected_text):
                return {"error": "Task changed; refresh and try again"}

            if mutations.is_open(target):
                lines[line_num - 1] = mutations.complete(target, date.today().isoformat())
            elif mutations.is_done(target):
                status = "noted"
            else:
                return {"error": "No checkbox found on this line"}

            lines[line_num:line_num] = note_lines
            regenerated = self._regen_recurring(target) if status == "completed" else None
            if regenerated:
                lines.insert(line_num + len(note_lines), regenerated)  # below the note block
            await self._write_lines(abs_path, lines)

        event_payload = {"file": file_rel, "line": line_num, "has_note": True}
        await self.emit(
            "task:completed" if status == "completed" else "task:updated",
            event_payload,
        )
        if regenerated:
            await self.emit("task:recurred", {"file": file_rel, "line": line_num})
        return {
            "status": status,
            "file": file_rel,
            "line": line_num,
            "note_lines": len(note_lines),
        }

    @web_route("GET", "/api/calendar")
    async def api_calendar(self, request):
        open_tasks, done_tasks = await self._idx.get()
        return queries.group_by_date(open_tasks, done_tasks)

    @web_route("GET", "/api/agenda")
    async def api_agenda(self, request):
        """Time-bucketed open tasks: overdue / today / tomorrow / this_week / later / undated.

        Optional ?when=overdue|today|tomorrow|this_week|later|undated returns just that bucket.
        """
        open_tasks, _ = await self._idx.get()
        buckets = queries.agenda(open_tasks, date.today())
        when = (request.query_params.get("when") or "").strip().lower()
        if when:
            if when not in buckets:
                return {"error": f"unknown bucket '{when}'", "available": list(buckets)}
            return {"when": when, "tasks": buckets[when], "count": len(buckets[when])}
        return {k: {"count": len(v), "tasks": v} for k, v in buckets.items()}

    @web_route("GET", "/api/focus")
    async def api_focus(self, request):
        open_tasks, _ = await self._idx.get()
        return queries.top_focus(open_tasks, limit=3)

    @web_route("GET", "/api/read-feed")
    async def api_read_feed(self, request):
        """Hands-free read-aloud adapter — top focus tasks for eyes-off triage."""
        try:
            limit = max(1, min(20, int(request.query_params.get("limit") or "10")))
        except ValueError:
            limit = 10
        open_tasks, _ = await self._idx.get()
        scored = queries.top_focus(open_tasks, limit=limit)
        tasks = scored if scored else open_tasks[:limit]
        items = []
        for i, t in enumerate(tasks):
            text = (t.get("text") or "").strip()
            if not text:
                continue
            items.append(
                {
                    "id": f"task-{t.get('file', '')}-{t.get('line', '')}-{i}",
                    "text": text,
                    "source": "task",
                    "file": t.get("file"),
                    "line": t.get("line"),
                    "act": {
                        "label": "Complete",
                        "method": "POST",
                        "url": "/task/api/toggle",
                        "body": {"file": t.get("file"), "line": t.get("line")},
                    },
                }
            )
        return {"items": items, "source": "tasks", "count": len(items)}

    @web_route("GET", "/api/tags")
    async def api_tags(self, request):
        open_tasks, done_tasks = await self._idx.get()
        return queries.tag_counts(open_tasks, done_tasks)

    @web_route("GET", "/api/recurring")
    async def api_recurring(self, request):
        open_tasks, _ = await self._idx.get()
        return queries.recurring_tasks(open_tasks)

    @web_route("GET", "/api/by-context")
    async def api_by_context(self, request):
        open_tasks, _ = await self._idx.get()
        return queries.group_by_context(open_tasks)

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        open_tasks, done_tasks = await self._idx.get()
        return queries.stats(open_tasks, done_tasks, date.today())

    @web_route("POST", "/api/toggle")
    async def api_toggle(self, request):
        data = await request.json()
        file_rel = data.get("file", "")
        line_num = int(data.get("line", 0))
        expected_text = str(data.get("text") or "").strip()

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        today_str = date.today().isoformat()
        async with self._file_lock(file_rel):
            lines = await self._read_lines(abs_path)
            if line_num < 1 or line_num > len(lines):
                return {"error": f"Line {line_num} out of range"}

            target = lines[line_num - 1]
            if expected_text and not mutations.text_matches(target, expected_text):
                return {"error": "Task changed; refresh and try again"}

            new_line, action = mutations.toggle(target, today_str)
            if new_line is None:
                return {"error": "No checkbox found on this line"}

            lines[line_num - 1] = new_line
            regenerated = self._regen_recurring(target) if action == "completed" else None
            if regenerated:
                lines.insert(line_num, regenerated)  # fresh occurrence just below
            await self._write_lines(abs_path, lines)
        if action == "completed":
            await self.emit(
                "task:completed",
                {"file": file_rel, "line": line_num, "text": mutations.task_text(target)},
            )
            if regenerated:
                await self.emit("task:recurred", {"file": file_rel, "line": line_num})
        return {"status": action, "file": file_rel, "line": line_num, "recurred": bool(regenerated)}

    async def list_all(self) -> list[dict]:
        """Flat list shape consumed by boards. Stable id = ``{file}:{line}``."""
        open_tasks, done_tasks = await self._idx.get()
        rows: list[dict] = []
        for t in open_tasks + done_tasks[:200]:
            f = t.get("file", "") or ""
            ln = int(t.get("line", 0) or 0)
            rows.append(
                {
                    "id": f"{f}:{ln}",
                    "file": f,
                    "line": ln,
                    "text": t.get("text", ""),
                    "done": bool(t.get("done")),
                    "due": t.get("due", ""),
                    "done_date": t.get("done_date", ""),
                    "tier": t.get("tier", "fresh"),
                    "focus_score": t.get("focus_score", 0),
                    "overdue_days": t.get("overdue_days", 0),
                    "actionability": t.get("actionability", "someday"),
                    "priority": mutations.priority_level(t.get("text", "")),
                    "project": queries.project_from_path(f),
                }
            )
        return rows

    async def set_field(self, id: str, field: str, value, *, text: str = "") -> dict:
        """Cross-app setter — same contract as ``projects.set_field``.

        ``text`` is an optional staleness guard (only consulted for
        ``field == "done"``): when provided, the mutation is refused unless
        it still matches the line's indexed task text. Omit it to preserve
        the historical no-guard behaviour existing callers (e.g. boards)
        rely on.
        """
        if field not in self.SETTABLE_FIELDS:
            return {
                "error": f"field '{field}' not settable",
                "settable": sorted(self.SETTABLE_FIELDS),
            }

        file_rel, _, line_str = (id or "").rpartition(":")
        try:
            line_num = int(line_str)
        except ValueError:
            return {"error": f"bad task id '{id}' — expected '<file>:<line>'"}
        if not file_rel or line_num < 1:
            return {"error": f"bad task id '{id}'"}

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        today_str = date.today().isoformat()
        emit_completed = False
        regenerated = None
        async with self._file_lock(file_rel):
            lines = await self._read_lines(abs_path)
            if line_num > len(lines):
                return {"error": f"Line {line_num} out of range"}

            target = lines[line_num - 1]
            original_target = target

            if field == "done":
                expected_text = (text or "").strip()
                if expected_text and not mutations.text_matches(target, expected_text):
                    return {"error": "Task changed; refresh and try again"}
                want_done = (
                    bool(value)
                    if not isinstance(value, str)
                    else value.lower() in ("true", "1", "yes", "x", "done")
                )
                if want_done and mutations.is_open(target):
                    target = mutations.complete(target, today_str)
                    emit_completed = True
                    regenerated = self._regen_recurring(original_target)
                elif (not want_done) and mutations.is_done(target):
                    target = mutations.reopen(target)
                else:
                    return {"ok": True, "noop": True}

            elif field == "due":
                target = mutations.set_due(target, str(value or "").strip())

            elif field == "actionability":
                state = str(value or "").strip().lower()
                if state not in ("next", "someday", "waiting", ""):
                    return {"error": f"actionability must be next/someday/waiting (got '{value}')"}
                rewritten = mutations.set_actionability(target, state)
                if rewritten is None:
                    return {"error": "Line is not a task checkbox"}
                target = rewritten

            elif field == "priority":
                level = str(value or "").strip().lower()
                if level not in ("highest", "high", "medium", "low", "lowest", ""):
                    return {"error": f"priority must be highest/high/medium/low/lowest (got '{value}')"}
                rewritten = mutations.set_priority(target, level)
                if rewritten is None:
                    return {"error": "Line is not a task checkbox"}
                target = rewritten

            elif field == "text":
                new_text = str(value or "").strip()
                if not new_text:
                    return {"error": "text must be non-empty"}
                rewritten = mutations.rewrite_text(target, new_text)
                if rewritten is None:
                    return {"error": "Line is not a task checkbox"}
                target = rewritten

            lines[line_num - 1] = target
            if regenerated:
                lines.insert(line_num, regenerated)  # fresh occurrence just below
            await self._write_lines(abs_path, lines)
        if emit_completed:
            await self.emit(
                "task:completed",
                {"file": file_rel, "line": line_num, "text": mutations.task_text(original_target)},
            )
            if regenerated:
                await self.emit("task:recurred", {"file": file_rel, "line": line_num})
        await self.emit(
            "task:updated", {"file": file_rel, "line": line_num, "field": field, "value": value}
        )
        return {"ok": True, "id": id, "field": field, "value": value}

    @web_route("POST", "/api/set-field")
    async def api_set_field(self, request):
        data = await request.json()
        return await self.set_field(
            id=data.get("id", ""),
            field=data.get("field", ""),
            value=data.get("value"),
            text=str(data.get("text") or ""),
        )

    @staticmethod
    def _resolve_task_id(data: dict) -> str:
        """Accept either {id: '<file>:<line>'} or {file, line}; return '<file>:<line>'."""
        tid = (data.get("id") or "").strip()
        if tid:
            return tid
        file_rel = (data.get("file") or "").strip()
        line = data.get("line")
        if file_rel and line:
            try:
                return f"{file_rel}:{int(line)}"
            except (TypeError, ValueError):
                return ""
        return ""

    @web_route("POST", "/api/set-due")
    async def api_set_due(self, request):
        """Set a task's due date (absolute). Accepts {id} or {file,line} + {due}.
        Common verb users reach for; thin alias over set_field(field='due')."""
        data = await request.json()
        tid = self._resolve_task_id(data)
        if not tid:
            return {"error": "provide 'id' as '<file>:<line>', or 'file' + 'line'"}
        return await self.set_field(id=tid, field="due", value=str(data.get("due", "")).strip())

    @web_route("POST", "/api/reschedule")
    async def api_reschedule(self, request):
        """Reschedule a task: {due} sets an absolute date, {days} snoozes by N days.
        Thin alias unifying the two verbs users guess for 'move this task'."""
        data = await request.json()
        tid = self._resolve_task_id(data)
        if not tid:
            return {"error": "provide 'id' as '<file>:<line>', or 'file' + 'line'"}
        due = str(data.get("due", "")).strip()
        if due:
            return await self.set_field(id=tid, field="due", value=due)
        days = int(data.get("days", 7))
        file_rel, _, line_str = tid.rpartition(":")
        line_num = int(line_str) if line_str.isdigit() else 0
        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        async with self._file_lock(file_rel):
            lines = await self._read_lines(abs_path)
            if line_num < 1 or line_num > len(lines):
                return {"error": f"Line {line_num} out of range"}
            lines[line_num - 1] = mutations.snooze(lines[line_num - 1], days)
            await self._write_lines(abs_path, lines)
            new_line = lines[line_num - 1]
        from emptyos.sdk import DUE_PATTERN

        m = DUE_PATTERN.search(new_line)
        new_due = m.group(1) if m else ""
        await self.emit(
            "task:updated",
            {"file": file_rel, "line": line_num, "field": "due", "value": new_due},
        )
        return {"ok": True, "id": tid, "new_due": new_due}

    async def assistant_context(self) -> str | None:
        try:
            open_tasks, _ = await self._idx.get()
            if not open_tasks:
                return None
            return "Top Open Tasks:\n" + "\n".join(f"- {t['text']}" for t in open_tasks[:5])
        except Exception:
            return None

    # ── Archive (extracted to archive.py) ──
    ARCHIVE_DIR       = _archive.ARCHIVE_DIR
    archive_tasks     = _archive.archive_tasks
    api_archive       = _archive.api_archive
    api_archive_batch = _archive.api_archive_batch

    # ── Triage (extracted to triage.py) ──
    TRIAGE_OLD_SOMEDAY_DAYS  = _triage.TRIAGE_OLD_SOMEDAY_DAYS
    triage_scan              = _triage.triage_scan
    api_triage_scan          = _triage.api_triage_scan
    api_triage_dedup         = _triage.api_triage_dedup
    _ai_triage_enabled       = _triage._ai_triage_enabled
    classify_someday_batch   = _triage.classify_someday_batch
    api_triage_ai_status     = _triage.api_triage_ai_status
    api_triage_classify      = _triage.api_triage_classify
    api_triage_promote_batch = _triage.api_triage_promote_batch

    # ── Voice (extracted to voice.py) ──
    voice_add_task    = _voice.voice_add_task
    _normalize_due    = _voice._normalize_due
    _recent_duplicate_add = _voice._recent_duplicate_add
    narrate_after_add = _voice.narrate_after_add
    voice_list_recent = _voice.voice_list_recent
    voice_list_today  = _voice.voice_list_today
    voice_list_due    = _voice.voice_list_due
