"""Unit tests for the VaultIndex rescan split (async-wedge fix).

_periodic_rescan now runs _walk_mtimes (pure IO) on a thread and _apply_scan
(self._files mutation) on the loop. These tests pin that the split detects
new/changed/deleted files identically and that _walk_mtimes never mutates state.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from emptyos.runtime.vault_index import VaultIndex


def _vi(tmp_path):
    vi = VaultIndex(kernel=None)
    vi._vault = tmp_path
    return vi


def test_walk_mtimes_is_pure(tmp_path):
    (tmp_path / "a.md").write_text("---\nkind: doc\n---\nbody", encoding="utf-8")
    vi = _vi(tmp_path)
    walk = vi._walk_mtimes()
    assert "a.md" in walk
    abs_path, mtime = walk["a.md"]
    assert abs_path.name == "a.md" and isinstance(mtime, float)
    # pure: did not populate the index
    assert vi._files == {}


def test_incremental_scan_detects_new_changed_deleted(tmp_path):
    (tmp_path / "a.md").write_text("---\nstatus: open\n---\nx", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("y", encoding="utf-8")

    vi = _vi(tmp_path)
    assert vi._incremental_scan() == 2
    assert set(vi._files) == {"a.md", "sub/b.md"}
    assert vi._files["a.md"]["properties"]["status"] == "open"

    # unchanged → 0 updates
    assert vi._incremental_scan() == 0

    # change a.md with a forced-future mtime so the mtime check fires
    (tmp_path / "a.md").write_text("---\nstatus: closed\n---\nx", encoding="utf-8")
    future = os.path.getmtime(tmp_path / "a.md") + 100
    os.utime(tmp_path / "a.md", (future, future))
    assert vi._incremental_scan() == 1
    assert vi._files["a.md"]["properties"]["status"] == "closed"

    # delete b.md → removed
    (sub / "b.md").unlink()
    assert vi._incremental_scan() == 1
    assert set(vi._files) == {"a.md"}


def test_apply_scan_runs_on_prebuilt_walk(tmp_path):
    (tmp_path / "a.md").write_text("z", encoding="utf-8")
    vi = _vi(tmp_path)
    walk = vi._walk_mtimes()
    assert vi._apply_scan(walk) == 1
    assert "a.md" in vi._files


@pytest.mark.asyncio
async def test_full_scan_builds_off_loop_then_swaps(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("---\ntags: [one]\n---\nz", encoding="utf-8")
    vi = _vi(tmp_path)
    calls = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(fn, *args, **kwargs):
        calls.append(fn.__name__)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)
    assert await vi._full_scan() == 1
    assert calls == ["_scan_all"]
    assert vi._files["a.md"]["tags"] == ["one"]
    assert vi._by_tag == {"one": {"a.md"}}
