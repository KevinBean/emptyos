"""Undo of a phone task add — the real `voice_add_task` ↔ `voice_undo_add` ↔
`projects.remove_task_line` contract (tg-life-surface T3), daemon-free.

The rooms gate test fakes `undo_args`; these run the actual wrappers, so a key
renamed on one side (``project_id`` → ``project``) or a dropped purge of the
double-submit guard goes red here instead of as a TypeError on a phone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from helpers import load_app_module

TaskApp = load_app_module("task", "app").TaskApp
ProjectsApp = load_app_module("projects", "app").ProjectsApp


def _task(projects_answers):
    """A TaskApp whose call_app answers from `projects_answers[method]`."""
    t = TaskApp.__new__(TaskApp)
    t.calls = []
    t._recent_adds = []
    t._persist_recent_adds = lambda: None
    t._idx = SimpleNamespace(invalidate=lambda: None)
    t.vault_config = lambda key, default="": default

    async def call_app(app, method, **kw):
        t.calls.append((app, method, kw))
        answer = projects_answers[method]
        return answer(kw) if callable(answer) else answer

    async def emit(*a, **k):
        return None

    t.call_app = call_app
    t.emit = emit
    return t


def _placed(kw):
    line = f"- [ ] {kw['text']}" + (f" 📅 {kw['due']}" if kw.get("due") else "")
    return {"ok": True, "task": kw["text"], "project": kw["project_id"], "line": line}


def test_add_hands_back_exactly_what_the_undo_needs():
    t = _task({"add_task_to_project": _placed, "remove_task_line": {"ok": True}})
    out = asyncio.run(t.voice_add_task("buy milk", due="2026-10-01"))
    assert out["undo_args"] == {"project_id": "inbox", "task_line": "- [ ] buy milk 📅 2026-10-01"}
    # The undo method takes exactly those keys.
    undone = asyncio.run(t.voice_undo_add(**out["undo_args"]))
    assert undone == {"say": "Removed: buy milk 📅 2026-10-01"}
    assert t.calls[-1] == ("projects", "remove_task_line",
                           {"project_id": "inbox", "task_line": "- [ ] buy milk 📅 2026-10-01"})


def test_an_add_that_added_nothing_offers_no_undo():
    t = _task({"add_task_to_project": _placed})
    first = asyncio.run(t.voice_add_task("buy milk"))
    dup = asyncio.run(t.voice_add_task("buy milk"))
    empty = asyncio.run(t.voice_add_task("   "))
    assert "undo_args" in first
    assert "Already added" in dup["say"] and "undo_args" not in dup
    assert "undo_args" not in empty


def test_undo_forgets_the_add_so_saying_it_again_adds_it():
    t = _task({"add_task_to_project": _placed, "remove_task_line": {"ok": True}})
    out = asyncio.run(t.voice_add_task("buy milk"))
    asyncio.run(t.voice_undo_add(**out["undo_args"]))
    again = asyncio.run(t.voice_add_task("buy milk"))
    assert "Already added" not in again["say"] and "undo_args" in again


def test_a_refused_remove_is_reported_and_keeps_the_guard():
    t = _task({"add_task_to_project": _placed,
               "remove_task_line": {"error": "that task changed or is gone, so it wasn't removed"}})
    out = asyncio.run(t.voice_add_task("buy milk"))
    res = asyncio.run(t.voice_undo_add(**out["undo_args"]))
    assert "changed" in res["error"]
    # Nothing was removed, so a repeat is still a duplicate.
    assert "Already added" in asyncio.run(t.voice_add_task("buy milk"))["say"]


# ── projects.remove_task_line ────────────────────────────────────────────


def _projects(content: str):
    p = ProjectsApp.__new__(ProjectsApp)
    p.files = {"10_Projects/inbox/inbox.md": content}
    p.locks: dict = {}
    p.lock_keys: list = []

    def write_lock(key):
        p.lock_keys.append(key)
        return p.locks.setdefault(key, asyncio.Lock())

    async def read(path):
        return p.files[path.replace("\\", "/")]

    async def write(path, text):
        p.files[path.replace("\\", "/")] = text

    p.write_lock = write_lock
    p.read = read
    p.write = write
    p._find_project_file = lambda pid: Path("10_Projects/inbox/inbox.md") if pid == "inbox" else None
    return p


def test_remove_task_line_writes_the_note_without_that_line_under_the_add_lock():
    p = _projects("## Tasks\n- [ ] keep\n- [ ] buy milk\n")
    out = asyncio.run(p.remove_task_line("inbox", "- [ ] buy milk"))
    assert out["ok"] is True
    assert p.files["10_Projects/inbox/inbox.md"] == "## Tasks\n- [ ] keep\n"
    assert p.lock_keys == ["projects:inbox"]   # the key add_task_to_project takes


def test_remove_task_line_refuses_a_changed_task_and_writes_nothing():
    p = _projects("## Tasks\n- [x] buy milk ✅ 2026-09-30\n")
    out = asyncio.run(p.remove_task_line("inbox", "- [ ] buy milk"))
    assert "changed" in out["error"]
    assert p.files["10_Projects/inbox/inbox.md"] == "## Tasks\n- [x] buy milk ✅ 2026-09-30\n"
    assert "error" in asyncio.run(p.remove_task_line("nope", "- [ ] x"))
