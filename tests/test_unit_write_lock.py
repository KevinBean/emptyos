"""Concurrent-mutation safety (P2.1) — BaseApp.write_lock serializes
read-modify-write so a user POST racing a reactor handler can't wipe an update.

The other two P2.1 safety reflexes already have coverage in test_sys_agent.py:
  - denied-tool recovery / no-spiral  (test_denied_tool_produces_error_but_loop_continues)
  - consecutive-error + edit loop guards (test_*_loop_guard*)
The bench can't exercise these (it runs tool_consent=None / auto-approve, and
verify() sees only the scratch dir), so they live as unit tests, not bench rows.

Pure — no daemon.
"""

from __future__ import annotations

import asyncio

import pytest

from fake_kernel import FakeCapability, make_bare_app
from emptyos.runtime.vault_index import VaultIndex


@pytest.mark.asyncio
async def test_write_lock_serializes_read_modify_write():
    """5 concurrent RMW tasks under the same lock → no lost updates."""
    app = make_bare_app(FakeCapability([]))
    note = {"n": 0}

    async def rmw():
        async with app.write_lock("note:foo"):
            cur = note["n"]
            await asyncio.sleep(0)  # yield mid-critical-section — invites interleaving
            note["n"] = cur + 1

    await asyncio.gather(*[rmw() for _ in range(5)])
    assert note["n"] == 5  # every increment landed


@pytest.mark.asyncio
async def test_unlocked_read_modify_write_loses_updates():
    """Contrast: the same interleaving WITHOUT the lock loses updates — this is
    the race the lock exists to prevent (CLAUDE.md vault RMW gotcha)."""
    note = {"n": 0}

    async def rmw():
        cur = note["n"]
        await asyncio.sleep(0)
        note["n"] = cur + 1

    await asyncio.gather(*[rmw() for _ in range(5)])
    assert note["n"] < 5  # lost updates occurred


@pytest.mark.asyncio
async def test_write_lock_keys_are_independent():
    app = make_bare_app(FakeCapability([]))
    assert app.write_lock("a") is app.write_lock("a")  # same key → same lock (cached)
    assert app.write_lock("a") is not app.write_lock("b")  # different key → no contention


@pytest.mark.asyncio
async def test_write_lock_different_keys_run_concurrently():
    """Two RMW streams on DISTINCT notes don't serialize against each other."""
    app = make_bare_app(FakeCapability([]))
    order: list[str] = []

    async def hold(key: str, tag: str):
        async with app.write_lock(key):
            order.append(f"{tag}-enter")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-exit")

    await asyncio.gather(hold("note:a", "A"), hold("note:b", "B"))
    # Distinct keys → both can be inside their critical sections at once, so the
    # two "enter"s precede the two "exit"s (interleaved), not strictly A then B.
    assert order.index("A-enter") < order.index("B-exit")
    assert order.index("B-enter") < order.index("A-exit")


def test_note_lock_is_shared_across_app_instances():
    index = VaultIndex(kernel=None)
    app_a = make_bare_app(FakeCapability([]))
    app_b = make_bare_app(FakeCapability([]))
    app_a.kernel.services.get_optional = lambda name: index if name == "vault_index" else None
    app_b.kernel.services.get_optional = lambda name: index if name == "vault_index" else None

    assert app_a.note_lock("folder/note.md") is app_b.note_lock("folder\\note.md")


def test_note_lock_falls_back_to_stable_per_app_lock():
    app = make_bare_app(FakeCapability([]))
    assert app.note_lock("folder/note.md") is app.note_lock("folder\\note.md")
