"""Unit tests — link app vault scan, the cached index, and the orphan report.

Three things are pinned here.

The **corpus and the counts**, so a change to what the scan collects can't
quietly change what the UI reports.

The **thread**, because `_read_all` walks every note with rglob + read_text and
neither that nor `self.read()` yields — the filesystem read provider is an
`async def` wrapped around a synchronous read. Awaiting the scan inline pins the
event loop, the daemon stops answering /api/health, and the watchdog restarts it
(the 2026-07-25 wedge — same shape as the task indexer). A functional test
cannot see that regression; only thread identity can.

The **report's cap**, because `orphan_report` is now a cross-app entry point
(`vault-graph` calls it) as well as a route body, and on this vault the
uncapped orphan list is ~9,200 paths.

No daemon — the app module is loaded standalone and instantiated without
BaseApp.__init__, since these paths only need `_notes_dir` and a lock table.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from helpers import load_app_module  # noqa: E402


@pytest.fixture(scope="module")
def link_mod():
    # Via the package-registering loader: app.py does `from . import linkindex`,
    # so a bare spec_from_file_location can no longer import it.
    return load_app_module("link", "app", preload=("linkindex",))


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    _write(v / "hub.md", "See [[nested/alpha]] and [[beta]].\n")
    _write(v / "nested/alpha.md", "Links back to [[hub]].\n")
    _write(v / "beta.md", "No outgoing links here.\n")
    _write(v / "lonely.md", "Nobody links to me, and I link to nobody.\n")
    _write(v / "dangling.md", "Points at [[nothing-at-all]].\n")
    # Neither a dot-file nor anything under a dot-directory is part of the
    # corpus. `.stversions` is the case that motivated the second rule: it is
    # Syncthing's version store, so its copies of real notes would both inflate
    # the corpus and link to the notes they are copies of.
    _write(v / ".hidden.md", "[[lonely]]\n")
    _write(v / ".claude/rules/some-rule.md", "[[lonely]]\n")
    _write(v / ".stversions/hub~20260817.md", "See [[beta]].\n")
    return v


def _app(link_mod, vault):
    app = object.__new__(link_mod.LinkApp)
    app._notes_dir = lambda: vault
    app._index = None
    app._write_locks = {}
    return app


class TestCorpusAndCounts:
    def test_scan_collects_every_note_but_no_dot_files(self, link_mod, vault):
        notes = _app(link_mod, vault)._read_all()
        assert set(notes) == {
            "hub.md", "nested/alpha.md", "beta.md", "lonely.md", "dangling.md",
        }

    def test_degree_zero_is_the_orphan(self, link_mod, vault):
        """`lonely.md` has nothing in and nothing out. `dangling.md` also draws
        no edges, but it *tried* — that's `broken_only`, a different fix."""
        assert asyncio.run(_app(link_mod, vault).orphans()) == ["lonely.md"]

    def test_dot_directories_are_excluded_like_vault_index_does(self, link_mod, vault):
        """The rule is any dot-*segment*, not just a dot filename.

        `p.name.startswith(".")` alone admitted 609 notes on the live vault —
        `.claude/`, `.agent-bus/`, `.stversions/` — which `VaultIndex` has
        always excluded. Two components on one page disagreeing about what the
        vault contains is the bug; matching `VaultIndex` is the fix.
        """
        notes = _app(link_mod, vault)._read_all()
        assert not [p for p in notes if p.startswith(".")]

    def test_notes_hidden_from_the_corpus_do_not_rescue_an_orphan(self, link_mod, vault):
        """`.hidden.md` and `.claude/rules/some-rule.md` both link to `lonely`,
        and `.stversions/hub~20260817.md` links to `beta` — none are part of
        the corpus, so neither target may gain an incoming edge from them."""
        ix = asyncio.run(_app(link_mod, vault).index())
        assert ix["inn"]["lonely.md"] == set()
        assert ix["inn"]["beta.md"] == {"hub.md"}

    def test_missing_vault_is_empty_not_an_error(self, link_mod, tmp_path):
        assert _app(link_mod, tmp_path / "nope")._read_all() == {}

    def test_stats_reports_the_three_populations_and_emits(self, link_mod, vault):
        app = _app(link_mod, vault)
        emitted = {}

        async def fake_emit(event, payload):
            emitted[event] = payload

        app.emit = fake_emit
        result = asyncio.run(app.api_stats(request=None))

        assert result["total_notes"] == 5
        assert result["total_links"] == 4          # 3 resolvable + 1 dangling
        assert result["orphan_count"] == 1         # lonely
        assert result["broken_only_count"] == 1    # dangling
        assert result["broken_target_count"] == 1  # nothing-at-all
        assert emitted["link:scan_completed"] == result


class TestOrphanReport:
    """The cross-app entry point `vault-graph` calls, not just a route body."""

    @pytest.fixture
    def crowd(self, tmp_path):
        v = tmp_path / "crowd"
        for i in range(12):
            _write(v / f"alone-{i:02d}.md", "no links either way\n")
        return v

    def test_counts_are_exact_while_the_list_is_capped(self, link_mod, crowd):
        rep = asyncio.run(_app(link_mod, crowd).orphan_report(limit=3))
        assert len(rep["orphans"]) == 3
        assert rep["counts"]["orphans"] == 12
        assert rep["truncated"] is True
        assert rep["total_notes"] == 12

    def test_limit_zero_means_unbounded(self, link_mod, crowd):
        rep = asyncio.run(_app(link_mod, crowd).orphan_report(limit=0))
        assert len(rep["orphans"]) == 12
        assert rep["truncated"] is False

    def test_route_still_defaults_to_500(self, link_mod, crowd):
        class _Req:
            query_params: dict = {}

        rep = asyncio.run(_app(link_mod, crowd).api_orphan_report(_Req()))
        assert rep["limit"] == 500


class TestScanOffLoop:
    """The wedge regression: neither the walk nor the index build may run on
    the event-loop thread."""

    def _record(self, app, attr, seen):
        real = getattr(app, attr)

        def recording(*a, **kw):
            seen[attr] = threading.get_ident()
            return real(*a, **kw)

        setattr(app, attr, recording)

    def test_read_and_build_both_run_off_the_event_loop(self, link_mod, vault):
        app = _app(link_mod, vault)
        seen: dict[str, int] = {}
        self._record(app, "_read_all", seen)

        async def run():
            seen["loop"] = threading.get_ident()
            return await app.orphans()

        asyncio.run(run())

        assert seen["_read_all"] != seen["loop"], (
            "_read_all ran on the event-loop thread — a large vault will "
            "freeze the daemon and trip the watchdog"
        )

    def test_the_index_is_built_once_and_reused(self, link_mod, vault):
        """The scan is ~7.5s cold on the real vault; every endpoint calling it
        per request is the defect the cache exists to remove."""
        app = _app(link_mod, vault)
        calls = {"n": 0}
        real = app._read_all

        def counting():
            calls["n"] += 1
            return real()

        app._read_all = counting

        async def run():
            await app.orphans()
            await app.backlinks("hub")
            await app.orphan_report(limit=5)

        asyncio.run(run())
        assert calls["n"] == 1

    def test_vault_changed_drops_the_cache(self, link_mod, vault):
        app = _app(link_mod, vault)

        async def run():
            await app.orphans()
            assert app._index is not None
            await app.on_vault_changed({})
            return app._index

        assert asyncio.run(run()) is None
