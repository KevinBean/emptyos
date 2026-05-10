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
from datetime import date, datetime, timedelta
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route

from . import mutations, queries
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
    SETTABLE_FIELDS = frozenset({"done", "due", "text"})

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

    # ── helpers ──────────────────────────────────────────────────────────

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

    # ── hub panel contributions ──────────────────────────────────────────

    async def panel_pulse_stats(self) -> list[dict]:
        open_tasks, done_tasks = await self._idx.get()
        return queries.pulse_stats(open_tasks, done_tasks, date.today())

    async def panel_todays_tasks(self) -> list[dict] | None:
        open_tasks, _ = await self._idx.get()
        return queries.todays_tasks_rows(open_tasks, date.today(), limit=5)

    # ── hub slot contributions ───────────────────────────────────────────

    async def slot_needs_attention(self) -> list[dict]:
        open_tasks, _ = await self._idx.get()
        return queries.needs_attention_slot(open_tasks)

    async def slot_today(self) -> list[dict]:
        open_tasks, _ = await self._idx.get()
        return queries.due_today_slot(open_tasks, date.today())

    # ── public methods (CLI + cross-app callers) ─────────────────────────

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
        existing = await self.read(file)
        await self.write(file, existing.rstrip("\n") + "\n" + line)

        task = Task(text=text, done=done, file=file, line=0, due=due)
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
        lines = await self._read_lines(abs_path)
        today_str = date.today().isoformat()
        new_due = ""

        for i, ln in enumerate(lines):
            if not mutations.matches(ln, match.text, want_done=want_done):
                continue
            if op == "complete":
                lines[i] = mutations.complete(ln, today_str)
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
        elif op == "reopen":
            match.done = False
            await self.emit("task:reopened", match.to_dict())
        elif op == "snooze":
            await self.emit(
                "task:snoozed", {**match.to_dict(), "new_due": new_due, "days": kw["days"]}
            )
        return match

    # ── voice intents ────────────────────────────────────────────────────

    async def voice_add_task(self, text: str, due: str = "") -> dict:
        text = (text or "").strip()
        if not text:
            return {"say": "I didn't catch the task text."}
        due = self._normalize_due(due)
        try:
            await self.add(text, due=due)
        except Exception as e:
            return {"say": f"Couldn't add that — {e}"}
        if due:
            return {"say": f"Added: {text} (due {due})"}
        return {"say": f"Added: {text}"}

    @staticmethod
    def _normalize_due(due: str) -> str:
        """Accept ISO date or relative tokens (today/tomorrow). Empty if invalid."""
        s = (due or "").strip().lower()
        if not s:
            return ""
        today = date.today()
        if s in ("today", "tonight"):
            return today.isoformat()
        if s == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        try:
            return date.fromisoformat(s[:10]).isoformat()
        except (ValueError, TypeError):
            return ""

    async def narrate_after_add(self, *, args: dict, result: dict) -> str | None:
        # The prioritised top-5 from list_today often doesn't visibly change
        # when a no-due task is added; quoting the new total gives the user
        # independent confirmation the add landed.
        try:
            open_tasks, _ = await self._idx.get()
        except Exception:
            return None
        n = len(open_tasks)
        if n == 0:
            return None
        return f"That's {n} open total."

    async def voice_list_recent(self, limit: int = 5) -> dict:
        if not self._recent_adds:
            return {"say": "No recently added tasks in this session."}
        items = list(self._recent_adds)[-int(limit or 5) :][::-1]
        n = len(items)
        if n == 1:
            say = f"Most recent: {items[0]['text']}."
        else:
            say = f"{n} recent. Newest: {items[0]['text']}."
        card_data = [
            {"text": it["text"], "done": False, "tag": it.get("ts", "")[11:16], "tone": ""}
            for it in items
        ]
        return {
            "say": say,
            "card": {"renderer": "task-list", "title": "Recently added", "data": card_data},
        }

    async def voice_list_today(self) -> dict:
        rows = await self.panel_todays_tasks() or []
        if not rows:
            return {"say": "Nothing on the list for today."}
        n = len(rows)
        if n == 1:
            say = f"One task today: {rows[0]['text']}."
        else:
            say = f"{n} tasks for today. Top one: {rows[0]['text']}."
        card_data = [
            {
                "text": r.get("text", ""),
                "done": bool(r.get("done")),
                "tag": r.get("tag") or "",
                "tone": r.get("tag_tone") or "",
            }
            for r in rows
        ]
        return {
            "say": say,
            "card": {"renderer": "task-list", "title": "Today", "data": card_data},
        }

    # ── CLI ──────────────────────────────────────────────────────────────

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

    # ── web API ──────────────────────────────────────────────────────────

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

        abs_path = self._abs_path(file_rel)
        if not abs_path:
            return {"error": "No notes path configured"}
        lines = await self._read_lines(abs_path)
        if line_num < 1 or line_num > len(lines):
            return {"error": f"Line {line_num} out of range"}

        today_str = date.today().isoformat()
        new_line, action = mutations.toggle(lines[line_num - 1], today_str)
        if new_line is None:
            return {"error": "No checkbox found on this line"}

        lines[line_num - 1] = new_line
        await self._write_lines(abs_path, lines)
        if action == "completed":
            await self.emit("task:completed", {"file": file_rel, "line": line_num})
        return {"status": action, "file": file_rel, "line": line_num}

    # ── flat list + generic field setter (boards view layer) ─────────────

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
                    "project": queries.project_from_path(f),
                }
            )
        return rows

    async def set_field(self, id: str, field: str, value) -> dict:
        """Cross-app setter — same contract as ``projects.set_field``."""
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
        lines = await self._read_lines(abs_path)
        if line_num > len(lines):
            return {"error": f"Line {line_num} out of range"}

        target = lines[line_num - 1]
        today_str = date.today().isoformat()
        emit_completed = False

        if field == "done":
            want_done = (
                bool(value)
                if not isinstance(value, str)
                else value.lower() in ("true", "1", "yes", "x", "done")
            )
            if want_done and mutations.is_open(target):
                target = mutations.complete(target, today_str)
                emit_completed = True
            elif (not want_done) and mutations.is_done(target):
                target = mutations.reopen(target)
            else:
                return {"ok": True, "noop": True}

        elif field == "due":
            target = mutations.set_due(target, str(value or "").strip())

        elif field == "text":
            new_text = str(value or "").strip()
            if not new_text:
                return {"error": "text must be non-empty"}
            rewritten = mutations.rewrite_text(target, new_text)
            if rewritten is None:
                return {"error": "Line is not a task checkbox"}
            target = rewritten

        lines[line_num - 1] = target
        await self._write_lines(abs_path, lines)
        if emit_completed:
            await self.emit("task:completed", {"file": file_rel, "line": line_num})
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
        )

    # ── voice-assistant context contribution ─────────────────────────────

    async def assistant_context(self) -> str | None:
        try:
            open_tasks, _ = await self._idx.get()
            if not open_tasks:
                return None
            return "Top Open Tasks:\n" + "\n".join(f"- {t['text']}" for t in open_tasks[:5])
        except Exception:
            return None
