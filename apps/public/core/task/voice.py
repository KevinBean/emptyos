"""task — Aura voice intents — add task, list recent/today/due, post-add narration.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: Every voice_* intent handler plus the due-date normalizer and the post-add narrator. Source of truth for the task voice surface.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.add/_idx/panel_todays_tasks (spine), queries.agenda for the due buckets.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from emptyos.sdk import normalize_relative_date

from . import queries

# A voice re-send of the same add within this window is a double-submit
# (the 2026-06-09 log added "Call mom" twice a minute apart), not a second task.
ADD_DEDUPE_WINDOW_S = 600

if TYPE_CHECKING:
    from .app import TaskApp  # noqa: F401 — for type hints only


# ─── Bind to TaskApp class as ────────────────────────────────
#   voice_add_task     = _voice.voice_add_task
#   voice_undo_add     = _voice.voice_undo_add
#   _normalize_due     = _voice._normalize_due  # @staticmethod
#   _recent_duplicate_add = _voice._recent_duplicate_add
#   narrate_after_add  = _voice.narrate_after_add
#   voice_list_recent  = _voice.voice_list_recent
#   voice_list_today   = _voice.voice_list_today
#   voice_list_due     = _voice.voice_list_due
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def voice_add_task(self, text: str, due: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        return {"say": "I didn't catch the task text."}
    dup = _recent_duplicate_add(self, text)
    if dup:
        return {"say": f"Already added “{dup['text']}” a few minutes ago, so I skipped the duplicate — reword it if you meant a second task."}
    due = self._normalize_due(due)
    try:
        task, placed = await self._add_to_project(text, project="inbox", due=due)
    except Exception as e:
        return {"say": f"Couldn't add that — {e}"}
    say = f"Added: {text} (due {due})" if due else f"Added: {text}"
    # The project id projects reported placing the task in. Not the file's
    # stem: a project note may be README.md / index.md inside its folder, and
    # the stem would then be "README".
    link: dict | None = None
    try:
        stem = placed.get("project") or "inbox"
        if stem:
            link = {"text": f"Open {stem}", "href": f"/projects/workspace/{stem}"}
    except Exception:
        link = None
    out: dict = {"say": say}
    if link:
        out["link"] = link
    if placed.get("line"):
        # The exact line projects wrote: what voice_undo_add removes (the
        # registry declares it as this wrapper's inverse).
        out["undo_args"] = {"project_id": placed.get("project") or "inbox",
                            "task_line": placed["line"]}
    return out


async def voice_undo_add(self, project_id: str, task_line: str) -> dict:
    """Undo a `voice_add_task`: remove the exact line it wrote.

    Refuses (``{"error": ...}``) when the task has since been ticked or edited.
    Also forgets the add in the double-submit guard, so saying the same task
    again right after an undo adds it rather than reporting a duplicate.
    """
    res = await self.call_app("projects", "remove_task_line",
                              project_id=project_id, task_line=task_line)
    if not isinstance(res, dict) or res.get("error"):
        return {"error": (res or {}).get("error") if isinstance(res, dict) else "remove failed"}
    text = task_line.split("] ", 1)[-1] if "] " in task_line else task_line
    norm = queries._norm_task_text(text)
    self._recent_adds = [it for it in (self._recent_adds or [])
                         if queries._norm_task_text(it.get("text", "")) != norm]
    self._persist_recent_adds()
    self._idx.invalidate()
    return {"say": f"Removed: {text}"}


@staticmethod
def _normalize_due(due: str) -> str:
    """Accept ISO date or relative tokens (today/tomorrow). Empty if invalid."""
    return normalize_relative_date(due)


def _recent_duplicate_add(self, text: str) -> dict | None:
    """A same-normalized-text entry in _recent_adds younger than the dedupe
    window. Voice-only guard — programmatic add() callers may legitimately
    re-add identical text, so this deliberately does NOT live in add()."""
    norm = queries._norm_task_text(text)
    if not norm:
        return None
    now = datetime.now()
    for it in reversed(list(self._recent_adds or [])):
        try:
            if queries._norm_task_text(it.get("text", "")) != norm:
                continue
            age = (now - datetime.fromisoformat(it.get("ts", ""))).total_seconds()
            if 0 <= age < ADD_DEDUPE_WINDOW_S:
                return it
        except Exception:
            continue
    return None


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
    # Honest split: the old "{n} tasks for today" counted the capped top-5
    # (overdue-first), so the same "5 tasks... Check immiaccount" sentence
    # repeated verbatim for months. Name overdue as overdue, with real
    # index-wide counts, so the user hears what's actually piling up.
    say = ""
    try:
        open_tasks, _ = await self._idx.get()
        buckets = queries.agenda(open_tasks, date.today())
        n_over, n_today = len(buckets.get("overdue", [])), len(buckets.get("today", []))
        oldest = (buckets.get("overdue") or [{}])[0].get("text", "")
        top_today = (buckets.get("today") or [{}])[0].get("text", "")
        if n_over and n_today:
            say = (f"{n_over} overdue — oldest: {oldest} — plus "
                   f"{n_today} due today, starting with {top_today}.")
        elif n_over:
            say = f"Nothing due today, but {n_over} overdue. Oldest: {oldest}."
        elif n_today:
            say = (f"One task due today: {top_today}." if n_today == 1
                   else f"{n_today} due today. Top one: {top_today}.")
    except Exception:
        say = ""
    if not say:
        n = len(rows)
        say = (f"One task today: {rows[0]['text']}." if n == 1
               else f"{n} tasks for today. Top one: {rows[0]['text']}.")
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


async def voice_list_due(self, when: str = "today", limit: int = 5) -> dict:
    when_key = (when or "today").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "now": "today", "due_today": "today",
        "tmr": "tomorrow", "next_day": "tomorrow",
        "week": "this_week", "this_wk": "this_week", "next_week": "this_week",
        "past_due": "overdue", "late": "overdue",
    }
    when_key = aliases.get(when_key, when_key)
    labels = {
        "today": "Today", "tomorrow": "Tomorrow",
        "this_week": "This week", "overdue": "Overdue",
        "later": "Later", "undated": "Undated",
    }
    if when_key not in labels:
        return await self.voice_list_today()

    open_tasks, _ = await self._idx.get()
    buckets = queries.agenda(open_tasks, date.today())
    rows = buckets.get(when_key, [])
    label = labels[when_key]

    if not rows:
        say_empty = {
            "today": "Nothing on the list for today.",
            "tomorrow": "Nothing scheduled for tomorrow.",
            "this_week": "Nothing else due this week.",
            "overdue": "No overdue tasks — you're clear.",
            "later": "Nothing scheduled further out.",
            "undated": "No undated tasks.",
        }[when_key]
        return {"say": say_empty}

    rows = rows[: int(limit or 5)]
    n = len(rows)
    first_text = rows[0].get("text", "")
    if when_key == "overdue":
        say = f"One overdue: {first_text}." if n == 1 else f"{n} overdue. Top: {first_text}."
    elif n == 1:
        say = f"One task {label.lower()}: {first_text}."
    else:
        say = f"{n} tasks {label.lower()}. Top one: {first_text}."

    today_s = date.today().isoformat()
    card_data = []
    for t in rows:
        od = t.get("overdue_days", 0)
        due = (t.get("due") or "")[:10]
        if od > 0:
            tag, tone = f"overdue {od}d", "overdue"
        elif due == today_s:
            tag, tone = "today", "today"
        elif due:
            tag, tone = due[5:], "week"
        else:
            tag, tone = "", ""
        card_data.append({
            "text": t.get("text", ""),
            "done": bool(t.get("done")),
            "tag": tag,
            "tone": tone,
        })
    return {
        "say": say,
        "card": {"renderer": "task-list", "title": label, "data": card_data},
    }
