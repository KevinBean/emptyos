"""music_library — the two list-time walks must not run on the event loop,
and each must scan a parent directory exactly once.

Pins the 2026-09-06 20:54 wedge (evidence `20260906T105449Z`): the js-errors
crawl loaded /music-library/, which requests `/api/list` and `/api/covers`
in parallel; `list_songs` → `_enrich` walked every song's directory
synchronously on the loop (`_md_count` re-run per song with no cache while
the audio scan beside it was cached) and the watchdog killed :9000 after a
37 s wedge. `all_covers` had the identical shape and was left on the loop by
the first draft of the fix — a hostile review caught it.

Claims, each red-provable on its own:

1. `list_songs` and `all_covers` leave the loop FREE while their walk runs —
   measured by a concurrent 10 ms ticker that must keep advancing during a
   0.4 s fake walk (a thread the coroutine joins would run off-loop and still
   block it — `[[feedback_concurrency_test_measures_completion]]`). Nominal
   ~40 ticks; ~25 under Windows' 15.6 ms timer granularity; the bar is 10.
2. Each walk scans a parent directory EXACTLY ONCE however many songs live in
   it — counted across every scan API (`Path.iterdir`, `os.scandir`,
   `os.listdir`, `Path.glob`), since a re-scan through a different API is the
   same defect (a mutant using `os.listdir` survived a counter that watched
   only `Path.iterdir`).

Daemon-free: `LibraryMixin` is built with `object.__new__` and a stub app
carrying only `kernel.config.notes_path`; `songs.list` is a lambda, so the
loop-freedom tests cover the walk, not the index read.
"""
from __future__ import annotations

import asyncio
import collections
import os
import pathlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from emptyos.sdk.music_library import LibraryMixin


def _make_library(vault: Path, items: list[dict]):
    lib = object.__new__(LibraryMixin)
    lib.app = SimpleNamespace(kernel=SimpleNamespace(config=SimpleNamespace(notes_path=vault)))
    lib.songs = SimpleNamespace(list=lambda **filters: [dict(i) for i in items])
    lib._license_field = ""
    return lib


class _ScanCounter:
    """Count directory scans per path across every API a rewrite might use."""

    def __init__(self, monkeypatch):
        self.per_dir: collections.Counter = collections.Counter()
        real_scandir = os.scandir
        real_listdir = os.listdir
        real_glob = pathlib.Path.glob
        real_iterdir = pathlib.Path.iterdir

        def scandir(p=".", *a, **kw):
            self.per_dir[str(p)] += 1
            return real_scandir(p, *a, **kw)

        def listdir(p=".", *a, **kw):
            self.per_dir[str(p)] += 1
            return real_listdir(p, *a, **kw)

        def glob(p, *a, **kw):
            self.per_dir[str(p)] += 1
            return real_glob(p, *a, **kw)

        def iterdir(p):
            # Path.iterdir resolves `os.scandir` by attribute at call time on
            # 3.13, so the scandir hook above already counts it; this wrapper
            # only registers the path (adds 0) so a healthy walk cannot vanish
            # from the report. If pathlib ever binds scandir at import, the
            # healthy count drops to 0 and the exactly-once asserts fail loudly.
            self.per_dir[str(p)] += 0
            return real_iterdir(p)

        monkeypatch.setattr(os, "scandir", scandir)
        monkeypatch.setattr(os, "listdir", listdir)
        monkeypatch.setattr(pathlib.Path, "glob", glob)
        monkeypatch.setattr(pathlib.Path, "iterdir", iterdir)


def _seed_tree(tmp_path: Path):
    songs = tmp_path / "songs"
    d1, d2 = songs / "2026-09-06", songs / "2026-09-07"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    for name in ("one", "two", "three", "four", "five"):
        (d1 / f"{name}.md").write_text("x" * 300, encoding="utf-8")
    (d1 / "one.mp3").write_bytes(b"\0")
    (d1 / "one-cover.png").write_bytes(b"\0")
    (d1 / "two-screenshot.png").write_bytes(b"\0")
    for name in ("six", "seven"):
        (d2 / f"{name}.md").write_text("x" * 300, encoding="utf-8")
    items = [{"path": f"songs/2026-09-06/{n}.md", "file": f"{n}.md"} for n in ("one", "two", "three", "four", "five")]
    items += [{"path": "songs/2026-09-07/six.md", "file": "six.md"}, {"path": "songs/2026-09-07/seven.md", "file": "seven.md"}]
    return d1, d2, items


async def _ticks_during(coro_factory):
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    out = await coro_factory()
    stop = True
    await t
    return ticks, out


# ── 1. loop stays free ───────────────────────────────────────────────


def test_list_songs_leaves_the_loop_free_while_enriching(tmp_path):
    lib = _make_library(tmp_path, [{"path": "a.md"}])
    threads: list[str] = []

    def slow_enrich(items):
        threads.append(threading.current_thread().name)
        time.sleep(0.4)
        return items

    lib._enrich = slow_enrich  # type: ignore[method-assign]

    ticks, out = asyncio.run(_ticks_during(lambda: lib.list_songs()))

    assert out == [{"path": "a.md"}]
    assert threads and threads[0] != "MainThread"
    assert ticks >= 10, f"list_songs blocked the loop during _enrich (ticker advanced {ticks}x in 0.4 s)"


def test_all_covers_leaves_the_loop_free_while_walking(tmp_path):
    lib = _make_library(tmp_path, [{"path": "a.md", "file": "a.md"}])
    threads: list[str] = []

    def slow_walk(vault, songs):
        threads.append(threading.current_thread().name)
        time.sleep(0.4)
        return {"a.md": None}

    lib._all_covers_sync = slow_walk  # type: ignore[method-assign]

    ticks, out = asyncio.run(_ticks_during(lambda: lib.all_covers()))

    assert out == {"a.md": None}
    assert threads and threads[0] != "MainThread"
    assert ticks >= 10, f"all_covers blocked the loop during the walk (ticker advanced {ticks}x in 0.4 s)"


# ── 2. one scan per parent, whatever API a rewrite uses ──────────────


def test_enrich_scans_each_parent_exactly_once(tmp_path, monkeypatch):
    d1, d2, items = _seed_tree(tmp_path)
    lib = _make_library(tmp_path, items)
    counter = _ScanCounter(monkeypatch)

    rows = lib._enrich(items)

    by = {r["path"]: r for r in rows}
    assert by["songs/2026-09-06/one.md"]["has_audio"] is True
    assert by["songs/2026-09-06/two.md"]["has_audio"] is False
    assert by["songs/2026-09-07/six.md"]["has_audio"] is False
    scans = {k: v for k, v in counter.per_dir.items() if v}
    assert scans == {str(d1): 1, str(d2): 1}, f"expected one scan per parent, saw {scans}"


def test_all_covers_scans_each_parent_exactly_once(tmp_path, monkeypatch):
    d1, d2, items = _seed_tree(tmp_path)
    lib = _make_library(tmp_path, items)
    counter = _ScanCounter(monkeypatch)

    covers = asyncio.run(lib.all_covers())

    # Shared dir (5 notes): each song only gets an image carrying its own stem.
    assert covers["one.md"].endswith("one-cover.png")
    assert covers["two.md"].endswith("two-screenshot.png")
    assert covers["three.md"] is None
    assert covers["six.md"] is None
    scans = {k: v for k, v in counter.per_dir.items() if v}
    assert scans == {str(d1): 1, str(d2): 1}, f"expected one scan per parent, saw {scans}"


@pytest.mark.parametrize("api", ["listdir", "scandir", "glob", "iterdir"])
def test_scan_counter_sees_every_api(tmp_path, monkeypatch, api):
    """The counter itself must not be blind — this is the mutant class that
    survived the first draft (a per-song `os.listdir` under a Path.iterdir
    counter). `iterdir` must count exactly once, not twice, or the
    exactly-once asserts above would be reading a doubled number."""
    d = tmp_path / "d"
    d.mkdir()
    counter = _ScanCounter(monkeypatch)
    if api == "listdir":
        os.listdir(d)
    elif api == "scandir":
        list(os.scandir(d))
    elif api == "glob":
        list(Path(d).glob("*.md"))
    else:
        list(Path(d).iterdir())
    assert counter.per_dir[str(d)] == 1
