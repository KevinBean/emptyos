"""Unit tests for the VaultIndex sync-mutator race detector.

VaultIndex.update_properties / append_to_section / create_note / enrich are
synchronous, so they cannot await the note lock. Instead they *observe* it and
warn when a write lands while another task holds it.

The subtlety these tests pin: `asyncio.Lock.locked()` reports that *a* task
holds the lock, not *which* one. A naive `if lock.locked(): warn` fires on the
correct pattern — a writer calling a sync mutator from inside its own
`async with self.note_lock(path)` block — flagging exactly the apps that were
already fixed while staying silent on foreign-task interleaving. `_NoteLock`
records the owning task so the detector can tell the two apart.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.runtime.vault_index import VaultIndex


class _FakeSyslog:
    def __init__(self):
        self.warns: list[tuple[str, dict]] = []

    def warn(self, source, message, **kwargs):
        self.warns.append((message, kwargs.get("data") or {}))


class _FakeConfig:
    def __init__(self, notes_path):
        self.notes_path = notes_path


class _FakeKernel:
    def __init__(self, notes_path):
        self.config = _FakeConfig(notes_path)
        self.syslog = _FakeSyslog()


@pytest.fixture
def vi(tmp_path):
    (tmp_path / "note.md").write_text(
        "---\ntags:\n  - x\n---\n\n## Log\n", encoding="utf-8"
    )
    index = VaultIndex(kernel=_FakeKernel(tmp_path))
    index._vault = tmp_path
    index._index_one("note.md", tmp_path / "note.md")
    return index


def test_self_held_lock_does_not_warn(vi):
    """The documented-correct pattern must be silent.

    learn/app.py holds note_lock(path) and then calls the sync mutators; the
    old detector logged a 'race candidate' for each one.
    """
    async def run():
        async with vi.note_lock("note.md"):
            vi.append_to_section("note.md", "Log", "- entry")
            vi.update_properties("note.md", {"author": "both"})

    asyncio.run(run())
    assert vi.kernel.syslog.warns == []


def test_foreign_held_lock_warns(vi):
    """The real hazard: a sync write lands while another task holds the lock."""
    async def run():
        started = asyncio.Event()

        async def holder():
            async with vi.note_lock("note.md"):
                started.set()
                await asyncio.sleep(0.05)

        task = asyncio.create_task(holder())
        await started.wait()
        vi.update_properties("note.md", {"active": "true"})
        await task

    asyncio.run(run())
    assert len(vi.kernel.syslog.warns) == 1
    message, data = vi.kernel.syslog.warns[0]
    assert "race candidate" in message
    assert data["path"] == "note.md"


def test_unlocked_write_does_not_warn(vi):
    """Nothing holds the lock, so there is no interleaving to report.

    (Unlocked RMW is caught statically by scripts/check-vault-rmw-race.py —
    the runtime cannot know a lock *should* have been taken.)
    """
    vi.update_properties("note.md", {"active": "false"})
    assert vi.kernel.syslog.warns == []


def test_detector_does_not_allocate_locks(vi):
    """`_warn_if_note_locked` must peek, not call note_lock().

    Creating on read would retain one asyncio.Lock per note ever written,
    growing without bound over a long uptime.
    """
    assert vi._note_locks == {}
    vi.update_properties("note.md", {"a": "1"})
    vi.append_to_section("note.md", "Log", "- x")
    assert vi._note_locks == {}


def test_note_lock_records_and_clears_owner(vi):
    async def run():
        lock = vi.note_lock("note.md")
        assert lock.owner is None
        async with lock:
            assert lock.owner is asyncio.current_task()
        assert lock.owner is None

    asyncio.run(run())


def test_warn_path_is_normalized(vi):
    """A backslash-spelled write must resolve to the same lock key."""
    async def run():
        started = asyncio.Event()

        async def holder():
            async with vi.note_lock("note.md"):
                started.set()
                await asyncio.sleep(0.05)

        task = asyncio.create_task(holder())
        await started.wait()
        vi.update_properties("note.md", {"active": "true"})
        await task

    asyncio.run(run())
    assert vi.kernel.syslog.warns
    assert vi.kernel.syslog.warns[0][1]["path"] == "note.md"
