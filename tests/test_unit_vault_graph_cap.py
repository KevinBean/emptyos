"""vault-graph — the cap-honesty stats (offline, no daemon).

A graph that draws 800 of 27,163 notes and says only "800 nodes" reads as the
whole vault. These pin the three numbers the stat line needs to say otherwise,
and specifically the two ways the obvious implementation gets the denominator
wrong:

  * `total_indexed` is the wrong denominator whenever a filter is active — the
    pool is the matching notes, not the vault.
  * `in_slice` is the wrong numerator whenever folder edges are on — it counts
    folder pseudo-nodes, so it overstates how much of the vault is drawn.

`_build_graph` needs a `vault_index` service and `self.read`, both stubbed here;
nothing else on the app is touched.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import load_app_module  # noqa: E402

VG = load_app_module("vault-graph", "app")


class _VaultIndex:
    def __init__(self, entries):
        self._files = {e["path"]: e for e in entries}

    def file_count(self):
        return len(self._files)

    def find(self, tags=None):
        want = set(tags or [])
        return [e for e in self._files.values() if want & set(e.get("tags") or [])]


class _Services:
    def __init__(self, vi):
        self._vi = vi

    def get_optional(self, name):
        return self._vi if name == "vault_index" else None


class _Config:
    notes_path = None  # -> vault_root falls back to Path("."), reads then fail soft


class _Kernel:
    def __init__(self, vi):
        self.services = _Services(vi)
        self.config = _Config()


def _entries(n, *, tag="note", folder="10_Projects"):
    return [
        {
            "path": f"{folder}/{tag}-{i:04d}.md",
            "name": f"{tag}-{i:04d}",
            "folder": folder,
            "tags": [tag],
            "modified": i,
            "properties": {},
        }
        for i in range(n)
    ]


def _app(entries):
    """A bare instance with just the two collaborators `_build_graph` touches.

    No `read` is provided: the body-read loop catches its own exceptions, so
    every note reads as empty. Edges are not under test here — node selection is.
    """
    app = object.__new__(VG.VaultGraphApp)
    app.kernel = _Kernel(_VaultIndex(entries))
    return app


def _build(entries, **kw):
    return asyncio.run(_app(entries)._build_graph(**kw))["stats"]


def test_uncapped_slice_reports_no_cap():
    s = _build(_entries(50), limit=800)
    assert (s["notes_drawn"], s["matched"], s["capped"]) == (50, 50, False)
    assert s["total_indexed"] == 50


def test_capped_slice_reports_the_pool_it_was_drawn_from():
    s = _build(_entries(2000), limit=800)
    assert s["notes_drawn"] == 800
    assert s["matched"] == 2000
    assert s["capped"] is True
    assert s["limit"] == 800


def test_matched_is_the_filtered_pool_not_the_whole_vault():
    """The denominator under a filter is the matching notes.

    With `kinds=kb` the graph draws 800 of the KB notes; quoting the vault
    total there is a different wrong number, not a fix.
    """
    entries = _entries(1500, tag="kb") + _entries(900, tag="note")
    s = _build(entries, kinds=["kb"], limit=800)
    assert s["matched"] == 1500          # the kb pool
    assert s["total_indexed"] == 2400    # the vault, carried as context
    assert s["filtered"] is True
    assert s["capped"] is True


def test_folder_pseudo_nodes_do_not_inflate_notes_drawn():
    """`in_slice` counts nodes, and folder edges add non-note nodes — so it
    cannot be the numerator in a sentence about vault coverage."""
    s = _build(_entries(40), limit=800, include_folder_edges=True)
    assert s["notes_drawn"] == 40
    assert s["in_slice"] > 40, "folder pseudo-nodes should be in the node count"
    assert s["capped"] is False


def test_unfiltered_pool_is_the_whole_index():
    s = _build(_entries(120), limit=800)
    assert s["filtered"] is False
    assert s["matched"] == s["total_indexed"] == 120
