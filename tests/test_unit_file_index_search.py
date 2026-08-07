"""System tests for the files-domain index search providers (3 OS backends).

All CI-safe: every test exercises pure gating / scoping / argv-building /
post-filter logic without needing es.exe, mdfind, or fd. The providers are
opt-in and dark by default, so end-to-end search against a live index isn't
part of the suite — the behaviour that matters for the capability chain
(fail-closed, scope guard, content-mode fall-through, per-OS argv, shape
parity) is fully testable in isolation.

Mirrors tests/test_unit_semble.py — providers tested without booting the kernel.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from emptyos.capabilities.providers.file_index_search import (
    EverythingSearchProvider,
    FdSearchProvider,
    IndexedFileSearchProvider,
    MdfindSearchProvider,
)
from emptyos.capabilities.providers.grep_search import GrepSearchProvider

REPO_ROOT = Path(__file__).resolve().parent.parent

ALL_BACKENDS = [EverythingSearchProvider, MdfindSearchProvider, FdSearchProvider]


# --- Shared base: availability gating -----------------------------------


@pytest.mark.parametrize("cls", ALL_BACKENDS)
@pytest.mark.asyncio
async def test_unavailable_without_roots(cls):
    """No allowed_roots → fail-closed (never search the whole machine),
    regardless of OS or binary presence."""
    p = cls(allowed_roots=[])
    assert await p.available() is False


@pytest.mark.parametrize("cls", ALL_BACKENDS)
@pytest.mark.asyncio
async def test_unavailable_on_wrong_platform(cls, monkeypatch):
    """Each backend reports unavailable when sys.platform doesn't match."""
    import emptyos.capabilities.providers.file_index_search as mod

    monkeypatch.setattr(mod.sys, "platform", "plan9")  # matches no backend
    p = cls(allowed_roots=[str(REPO_ROOT)])
    assert p._supported_platform() is False
    assert await p.available() is False


@pytest.mark.asyncio
async def test_available_when_platform_and_binary_ok(monkeypatch):
    """Happy path: matching platform + resolvable binary + reachable → True.

    Probe is stubbed (no real es.exe/mdfind/fd needed)."""
    import emptyos.capabilities.providers.file_index_search as mod

    monkeypatch.setattr(mod.sys, "platform", "darwin")
    p = MdfindSearchProvider(allowed_roots=[str(REPO_ROOT)])
    monkeypatch.setattr(p, "_resolve_binary", lambda: "/usr/bin/mdfind")

    async def _ok(_binary):
        return True

    monkeypatch.setattr(p, "_probe_reachable", _ok)
    assert await p.available() is True


@pytest.mark.asyncio
async def test_everything_probe_failure_makes_unavailable(monkeypatch):
    """Everything's IPC probe failing (service down) → unavailable even with
    es.exe present on Windows."""
    import emptyos.capabilities.providers.file_index_search as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT)])
    monkeypatch.setattr(p, "_resolve_binary", lambda: "C:/Tools/es.exe")

    async def _down(_binary):
        return False

    monkeypatch.setattr(p, "_probe_reachable", _down)
    assert await p.available() is False
    h = await p.health()
    assert "not reachable" in h["reason"].lower()


@pytest.mark.parametrize("cls", ALL_BACKENDS)
@pytest.mark.asyncio
async def test_health_reports_reason_and_recovery(cls):
    p = cls(allowed_roots=[])
    h = await p.health()
    assert h["available"] is False
    assert h["reason"]
    assert h["recovery"]["section"] == "[capabilities.search.files]"


# --- Shared base: scope guard -------------------------------------------


def test_within_roots_precise():
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT / "emptyos")])
    assert p._within_roots(REPO_ROOT / "emptyos" / "capabilities") is True
    # A sibling that merely shares a path prefix is NOT a child.
    assert p._within_roots(REPO_ROOT / "emptyos-backup") is False
    assert p._within_roots(Path("C:/Windows") if Path("C:/").exists() else Path("/tmp")) is False


def test_within_roots_fails_closed_with_no_roots():
    assert EverythingSearchProvider(allowed_roots=[])._within_roots(REPO_ROOT) is False


# --- Shared base: execute() input validation (raises → grep fallthrough) -


@pytest.mark.asyncio
async def test_content_mode_raises():
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT)])
    with pytest.raises(RuntimeError, match="content search unsupported"):
        await p.execute(query="x", mode="content")


@pytest.mark.asyncio
async def test_empty_query_raises():
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT)])
    with pytest.raises(RuntimeError, match="empty query"):
        await p.execute(query="   ")


@pytest.mark.asyncio
async def test_out_of_scope_path_raises():
    """Out-of-scope path raises before any binary call so the chain falls
    through to grep cleanly."""
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT)])
    outside = "C:/Windows/System32" if Path("C:/").exists() else "/tmp"
    with pytest.raises(RuntimeError, match="outside allowed roots"):
        await p.execute(query="x", path=outside)


@pytest.mark.parametrize("cls", ALL_BACKENDS)
@pytest.mark.parametrize("bad", ["-x", "--exec", "--ignore-case", "-live", "-"])
@pytest.mark.asyncio
async def test_dash_prefixed_query_raises(cls, bad):
    """A query starting with '-' is rejected before any backend call — it could
    be smuggled into argv as an option (fd's --exec is RCE-capable)."""
    p = cls(allowed_roots=[str(REPO_ROOT)])
    with pytest.raises(RuntimeError, match="flag-injection guard"):
        await p.execute(query=bad)


# --- Shared base: post-filter (canned lines, no live binary) -------------


def test_postfilter_scopes_types_dedupes_and_caps():
    root = REPO_ROOT / "emptyos"
    p = EverythingSearchProvider(allowed_roots=[str(root)])
    lines = [
        str(root / "a.py"),
        str(root / "a.py"),                       # duplicate → collapsed
        str(root / "b.md"),
        str(REPO_ROOT / "outside.py"),            # outside root → dropped
        str(root / "sub" / "c.py"),
        "   ",                                     # blank → skipped
    ]
    # No type filter: 4 unique in-scope paths.
    out = p._postfilter(lines, type="", glob="", limit=10)
    assert [r["path"] for r in out] == [
        str(root / "a.py"), str(root / "b.md"), str(root / "sub" / "c.py"),
    ]
    # type filter → only .py.
    py = p._postfilter(lines, type="py", glob="", limit=10)
    assert all(r["path"].endswith(".py") for r in py)
    assert len(py) == 2
    # cap honoured.
    assert len(p._postfilter(lines, type="", glob="", limit=1)) == 1


def test_passes_glob():
    assert IndexedFileSearchProvider._passes_glob(Path("/x/y/z.py"), "*.py") is True
    assert IndexedFileSearchProvider._passes_glob(Path("/x/y/z.md"), "*.py") is False


# --- Per-OS argv construction (the platform-specific logic) --------------


def test_everything_build_runs():
    p = EverythingSearchProvider(allowed_roots=[str(REPO_ROOT)])
    roots = [Path("D:/a"), Path("D:/b")]
    runs = p._build_runs("es", "foo", roots, case_insensitive=True, fetch_limit=50)
    assert len(runs) == 2  # one `-path` run per root (es -path takes a single dir)
    for argv, root in zip(runs, roots):
        assert argv[0] == "es"
        assert "-n" in argv and "50" in argv
        assert "-path" in argv and str(root) in argv
        assert argv[-1] == "foo"      # query is the trailing positional
        assert "-case" not in argv    # case-insensitive default
    # case-sensitive adds -case.
    cs = p._build_runs("es", "foo", roots, case_insensitive=False, fetch_limit=50)[0]
    assert "-case" in cs


def test_mdfind_build_runs_one_per_root():
    p = MdfindSearchProvider(allowed_roots=[str(REPO_ROOT)])
    roots = [Path("/vault"), Path("/repo")]
    runs = p._build_runs("mdfind", "foo", roots, case_insensitive=True, fetch_limit=50)
    assert len(runs) == 2  # one -onlyin run per root
    for argv, root in zip(runs, roots):
        assert argv[:2] == ["mdfind", "-onlyin"]
        assert "-name" in argv and "foo" in argv
        assert str(root) in argv


def test_fd_build_runs_multi_root_single_call():
    p = FdSearchProvider(allowed_roots=[str(REPO_ROOT)])
    roots = [Path("/vault"), Path("/repo")]
    runs = p._build_runs("fd", "foo", roots, case_insensitive=True, fetch_limit=50)
    assert len(runs) == 1  # fd accepts multiple search paths in one call
    argv = runs[0]
    assert argv[0] == "fd"
    assert "--fixed-strings" in argv  # literal substring, not regex
    assert "--no-ignore" in argv and "--hidden" in argv  # coverage parity
    assert "--ignore-case" in argv
    assert "--max-results" in argv and "50" in argv
    # `--` separator stops option parsing, immediately before the query.
    assert "--" in argv
    assert argv[argv.index("--") + 1] == "foo"
    # roots are the trailing args.
    assert argv[-2:] == [str(roots[0]), str(roots[1])]
    # case-sensitive switches the flag.
    cs = p._build_runs("fd", "foo", roots, case_insensitive=False, fetch_limit=50)[0]
    assert "--case-sensitive" in cs and "--ignore-case" not in cs


# --- Real-binary integration (skips unless es.exe + Everything present) ---


def _find_es():
    """Resolve es.exe from PATH or the conventional user-home tools dir."""
    p = shutil.which("es") or shutil.which("es.exe")
    if p:
        return p
    cand = os.path.join(os.environ.get("LOCALAPPDATA", ""), "eos", "tools", "es.exe")
    return cand if os.path.isfile(cand) else None


_ES_BIN = _find_es()


@pytest.mark.skipif(
    sys.platform != "win32" or not _ES_BIN,
    reason="es.exe / Everything not available on this machine",
)
@pytest.mark.asyncio
async def test_everything_e2e_against_real_index():
    """Exercise the REAL es.exe — catches query-syntax bugs the argv-shape unit
    tests can't (the `<path:..>` grouping passed those but returned nothing live;
    `-path` is the working form). Scoped to the repo, searching for a file we
    know is there."""
    p = EverythingSearchProvider(binary_path=_ES_BIN, allowed_roots=[str(REPO_ROOT)])
    if not await p.available():
        pytest.skip("Everything service not running")
    res = await p.execute(query="manifest.toml", limit=5)
    assert res, "expected manifest.toml hits under the repo root"
    assert all(r["path"].lower().endswith("manifest.toml") for r in res)
    # Every hit must be inside the allowed root (scope enforcement is live).
    rr = REPO_ROOT.resolve()
    assert all(Path(r["path"]).resolve().is_relative_to(rr) for r in res)


# --- Shape parity with GrepSearchProvider --------------------------------


@pytest.mark.parametrize("cls", ALL_BACKENDS)
def test_signature_matches_grep_provider(cls):
    """execute() must accept every kwarg grep accepts so callers can swap
    providers transparently within the search chain."""
    import inspect

    grep_params = set(inspect.signature(GrepSearchProvider.execute).parameters)
    ev_params = set(inspect.signature(cls.execute).parameters)
    missing = grep_params - ev_params
    assert not missing, f"{cls.__name__} missing kwargs grep accepts: {missing}"
