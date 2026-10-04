"""Unit tests for the cross-app write-lock sharing in the task app (daemon-free).

`write_lock` keys are per-app-instance (BaseApp._write_locks is a cached_property),
so a task-app write and a projects-app write to the *same* project note only
mutually exclude if they acquire the **same** lock object. `TaskApp._file_lock`
borrows the projects app's own lock for files under the projects dir. These tests
pin that invariant — the fix for the read-modify-write race where toggling a
project task from the task UI could clobber a concurrent add_task_to_project /
reactor write (CLAUDE.md § Development Gotchas).
"""

import asyncio
import types

from apps.task.app import TaskApp


class _FakeProjects:
    """Stand-in projects app whose write_lock mirrors BaseApp semantics:
    one stable Lock object per key."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def write_lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())


def _task_with_projects(proj) -> TaskApp:
    t = TaskApp.__new__(TaskApp)  # bypass setup; _file_lock only needs kernel + write_lock
    instances = {"projects": proj} if proj is not None else {}
    t.kernel = types.SimpleNamespace(apps=types.SimpleNamespace(instances=instances))
    return t


def test_project_file_borrows_projects_lock():
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    lock = t._file_lock("10_Projects/foo/foo.md")
    # Identity: the exact lock object projects itself keys as projects:foo.
    assert lock is proj.write_lock("projects:foo")


def test_inbox_project_file_borrows_projects_lock():
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    assert t._file_lock("10_Projects/inbox/inbox.md") is proj.write_lock("projects:inbox")


def test_legacy_flat_project_file_keys_on_stem():
    # 10_Projects/foo.md (legacy flat, pre-upgrade) — projects keys its lock on
    # target.stem ("foo"), NOT "foo.md", so the borrowed key must match.
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    assert t._file_lock("10_Projects/foo.md") is proj.write_lock("projects:foo")


def test_windows_separators_normalized():
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    assert t._file_lock("10_Projects\\foo\\foo.md") is proj.write_lock("projects:foo")


def test_non_project_file_uses_own_lock():
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    lock = t._file_lock("00_Inbox/scratch.md")
    # Not the projects lock; a stable task-owned lock instead.
    assert lock is not proj.write_lock("projects:scratch")
    assert lock is t._file_lock("00_Inbox/scratch.md")  # stable per file


def test_missing_projects_instance_falls_back_to_own_lock():
    t = _task_with_projects(None)
    # Project-shaped path but no projects app loaded (e.g. export bundle):
    # must still return a usable lock, just the task's own.
    lock = t._file_lock("10_Projects/foo/foo.md")
    assert isinstance(lock, asyncio.Lock)
    assert lock is t._file_lock("10_Projects/foo/foo.md")


def test_same_project_two_files_share_one_lock():
    proj = _FakeProjects()
    t = _task_with_projects(proj)
    # The main note and any task line in the same project serialise together.
    a = t._file_lock("10_Projects/foo/foo.md")
    b = t._file_lock("10_Projects/foo/docs/spec.md")
    assert a is b is proj.write_lock("projects:foo")
