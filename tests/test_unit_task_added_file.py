"""task.add must report the file projects actually wrote.

Projects are folders (`10_Projects/<id>/<id>.md`), a legacy flat note
(`10_Projects/<id>.md`), an archived project, or a folder whose note is
README.md / index.md; only the projects app knows which one it found. The task
app used to rebuild the flat path itself, so every add into a standard project
named a file that did not exist (found 2026-10-03 by a live markitup send into
the inbox).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_app_module

task_mod = load_app_module("task", "app")
projects_mod = load_app_module("projects", "app")


# ── projects: `path` is the file it found, not a recompute ────────────

VAULT = Path("/vault")


@pytest.mark.parametrize("found", [
    "10_Projects/site.md",                          # legacy flat note
    "40_Archive/10_Projects/site/site.md",          # archived
    "10_Projects/Site/README.md",                   # folder whose note is README
])
def test_projects_reports_the_file_it_found(found):
    app = projects_mod.ProjectsApp.__new__(projects_mod.ProjectsApp)
    files = {found: "## Tasks\n"}
    locks = {}
    app.kernel = SimpleNamespace(config=SimpleNamespace(notes_path=VAULT))   # vault_root reads this
    app.write_lock = lambda key: locks.setdefault(key, asyncio.Lock())
    app._find_project_file = lambda pid: VAULT / found

    async def read(path):
        return files[Path(path).relative_to(VAULT).as_posix()]

    async def write(path, text):
        files[Path(path).relative_to(VAULT).as_posix()] = text

    async def emit(*a, **k):
        pass

    app.read, app.write, app.emit = read, write, emit
    out = asyncio.run(app.add_task_to_project("site", "check the earth grid"))
    assert out["path"] == found
    assert "check the earth grid" in files[found]


# ── task: Task.file is taken from the projects reply ──────────────────

def _app(reply):
    app = task_mod.TaskApp.__new__(task_mod.TaskApp)

    async def call_app(app_id, method, **kw):
        return reply

    async def emit(*a, **k):
        pass

    app.call_app = call_app
    app.emit = emit
    app.vault_config = lambda key, default=None: default
    app._recent_adds = []
    app._record_recent_add = lambda **kw: app._recent_adds.append(kw)
    app._idx = SimpleNamespace(invalidate=lambda: None)
    return app


def test_the_task_names_the_file_projects_wrote():
    # A path no recompute from the id could produce.
    reply = {"ok": True, "line": "- [ ] x", "project": "inbox",
             "path": "40_Archive/10_Projects/Inbox/README.md"}
    app = _app(reply)
    task, placed = asyncio.run(app._add_to_project("x", project="inbox"))
    assert task.file == "40_Archive/10_Projects/Inbox/README.md"
    assert app._recent_adds[-1]["file"] == "40_Archive/10_Projects/Inbox/README.md"
    assert placed["line"] == "- [ ] x"


def test_an_older_projects_reply_without_a_path_falls_back():
    task, _ = asyncio.run(_app({"ok": True, "line": "- [ ] x"})._add_to_project("x", project="site"))
    assert task.file == "10_Projects/site.md"


def test_an_empty_path_falls_back_and_says_so(caplog):
    with caplog.at_level("WARNING", logger="emptyos.task"):
        task, _ = asyncio.run(_app({"ok": True, "line": "- [ ] x", "path": ""})._add_to_project("x", project="site"))
    assert task.file == "10_Projects/site.md"
    assert any("outside the vault" in r.message for r in caplog.records)


def test_the_voice_link_names_the_project_not_the_note_file():
    """A README-style project note must not turn the link into /workspace/README."""
    voice = load_app_module("task", "voice")
    app = _app({"ok": True, "line": "- [ ] milk", "project": "inbox",
                "path": "10_Projects/inbox/README.md"})
    app._recent_duplicate = lambda text: None
    app._persist_recent_adds = lambda: None
    out = asyncio.run(voice.voice_add_task(app, "milk"))
    assert out["link"]["href"] == "/projects/workspace/inbox", out
