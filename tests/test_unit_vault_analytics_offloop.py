"""Unit tests — app-analytics vault scanners stay off the event loop.

Every public method on VaultAnalyticsMixin walks the whole vault with rglob +
stat. That is blocking I/O measured in seconds on a large vault, so each one is
an async wrapper over a synchronous `_`-prefixed scanner dispatched with
asyncio.to_thread. Running any of them inline pins the event loop, the daemon
stops answering /api/health, and the watchdog restarts it (the 2026-07-25 wedge
class — first found in the task indexer, then link, then here).

The results are identical whether threaded or not, so thread identity is the
only assertion that can catch a regression. Parametrised over every scanner so
a newly added one that forgets the wrapper is caught by adding its name here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_MIXIN = _ROOT / "apps/public/standard/app-analytics/vault_mixin.py"

# (public async method, backing synchronous scanner)
SCANNERS = [
    ("stats", "_stats"),
    ("scan_uncovered", "_scan_uncovered"),
    ("recent", "_recent"),
    ("largest", "_largest"),
    ("stale", "_stale"),
    ("growth", "_growth"),
]


@pytest.fixture(scope="module")
def mixin_mod():
    spec = importlib.util.spec_from_file_location("vault_mixin_under_test", _MIXIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StubHost:
    """Minimal stand-in for the AppAnalyticsApp host BaseApp."""

    def __init__(self, vault: Path):
        self.vault_root = vault

    def vault_config(self, key, default=""):
        return default


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    for rel in ("00_Inbox/a.md", "10_Projects/p/b.md", "20_Areas/c.md", "loose.md"):
        p = v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# note\n", encoding="utf-8")
    return v


@pytest.mark.parametrize("method,scanner", SCANNERS, ids=[m for m, _ in SCANNERS])
def test_scanner_runs_off_the_event_loop(mixin_mod, vault, method, scanner):
    mixin = mixin_mod.VaultAnalyticsMixin(StubHost(vault))
    seen: dict[str, int] = {}
    real = getattr(mixin, scanner)

    def recording(*args, **kwargs):
        seen["scan"] = threading.get_ident()
        return real(*args, **kwargs)

    setattr(mixin, scanner, recording)

    async def run():
        seen["loop"] = threading.get_ident()
        return await getattr(mixin, method)()

    asyncio.run(run())

    assert seen["scan"] != seen["loop"], (
        f"{method}() ran {scanner} on the event-loop thread — a large vault "
        "will freeze the daemon and trip the watchdog"
    )


def test_stats_still_counts_the_vault(mixin_mod, vault):
    """Guard the wrapper split against a behaviour change."""
    result = asyncio.run(mixin_mod.VaultAnalyticsMixin(StubHost(vault)).stats())
    assert result["total_files"] == 4
    assert result["para"]["Inbox"]["count"] == 1
    assert result["para"]["Projects"]["count"] == 1
    assert result["other_files"] == 1  # loose.md sits outside every PARA folder


def test_missing_vault_reports_an_error(mixin_mod, tmp_path):
    result = asyncio.run(mixin_mod.VaultAnalyticsMixin(StubHost(tmp_path / "nope")).stats())
    assert result == {"error": "vault not found"}
