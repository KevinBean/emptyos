"""Unit tests — link app full-vault scan.

Two things are pinned here. The counts, so folding the old duplicate walk in
`api_stats` into a single `_scan_links` pass can't quietly change what the UI
reports. And the *thread*, because the scan walks every note with rglob +
read_text and neither that nor `self.read()` yields — the filesystem read
provider is an `async def` wrapped around a synchronous read. Awaiting the scan
inline pins the event loop, the daemon stops answering /api/health, and the
watchdog restarts it (the 2026-07-25 wedge — same shape as the task indexer).
A functional test cannot see that regression; only thread identity can.

No daemon — the app module is loaded standalone and instantiated without
BaseApp.__init__, since the scan only needs `_notes_dir`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_LINK_APP = _ROOT / "apps/public/core/link/app.py"


@pytest.fixture(scope="module")
def link_mod():
    spec = importlib.util.spec_from_file_location("link_app_under_test", _LINK_APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    _write(v / "hub.md", "See [[alpha]] and [[beta]].\n")
    _write(v / "nested/alpha.md", "Links back to [[hub]].\n")
    _write(v / "beta.md", "No outgoing links here.\n")
    _write(v / "lonely.md", "Nobody links to me.\n")
    _write(v / ".hidden.md", "[[lonely]]\n")  # dot-files are not part of the corpus
    return v


def _app(link_mod, vault):
    app = object.__new__(link_mod.LinkApp)
    app._notes_dir = lambda: vault
    return app


class TestScanCounts:
    def test_scan_counts_notes_and_links(self, link_mod, vault):
        notes, linked, total = _app(link_mod, vault)._scan_links()
        assert set(notes) == {"hub", "alpha", "beta", "lonely"}, "dot-files excluded"
        assert linked == {"alpha", "beta", "hub"}
        assert total == 3

    def test_orphans_are_notes_nobody_links_to(self, link_mod, vault):
        orphans = asyncio.run(_app(link_mod, vault).orphans())
        assert orphans == [str(vault / "lonely.md")]

    def test_hidden_note_links_do_not_rescue_an_orphan(self, link_mod, vault):
        """`.hidden.md` links to `lonely`, but it isn't part of the corpus —
        so `lonely` must still read as an orphan."""
        _, linked, _ = _app(link_mod, vault)._scan_links()
        assert "lonely" not in linked

    def test_missing_vault_is_empty_not_an_error(self, link_mod, tmp_path):
        notes, linked, total = _app(link_mod, tmp_path / "nope")._scan_links()
        assert (notes, linked, total) == ({}, set(), 0)

    def test_stats_reports_one_pass_of_the_same_numbers(self, link_mod, vault):
        app = _app(link_mod, vault)
        emitted = {}

        async def fake_emit(event, payload):
            emitted[event] = payload

        app.emit = fake_emit
        result = asyncio.run(app.api_stats(request=None))

        assert result == {"total_notes": 4, "total_links": 3, "orphan_count": 1}
        assert emitted["link:scan_completed"] == result


class TestScanOffLoop:
    def test_scan_runs_off_the_event_loop(self, link_mod, vault):
        app = _app(link_mod, vault)
        seen: dict[str, int] = {}
        real_scan = app._scan_links

        def recording_scan():
            seen["scan"] = threading.get_ident()
            return real_scan()

        app._scan_links = recording_scan

        async def run():
            seen["loop"] = threading.get_ident()
            return await app.orphans()

        asyncio.run(run())

        assert seen["scan"] != seen["loop"], (
            "_scan_links ran on the event-loop thread — a large vault will "
            "freeze the daemon and trip the watchdog"
        )

    def test_stats_scans_off_the_event_loop(self, link_mod, vault):
        app = _app(link_mod, vault)
        seen: dict[str, int] = {}
        real_scan = app._scan_links

        def recording_scan():
            seen["scan"] = threading.get_ident()
            return real_scan()

        app._scan_links = recording_scan

        async def noop_emit(event, payload):
            return None

        app.emit = noop_emit

        async def run():
            seen["loop"] = threading.get_ident()
            return await app.api_stats(request=None)

        asyncio.run(run())

        assert seen["scan"] != seen["loop"]
