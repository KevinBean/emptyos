"""System tests for SembleSearchProvider — code-domain semantic search.

Covers gating (out-of-scope path raises so chain falls through), graceful
degradation (semble missing → available()=False), and a real end-to-end
search against this very repo (skipped if semble isn't installed). Avoids
booting the kernel — provider tested in isolation, same pattern as
the (now retired) test_sys_headroom.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from emptyos.capabilities.providers.semble_search import SembleSearchProvider

SEMBLE_AVAILABLE = importlib.util.find_spec("semble") is not None

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- Gating / availability (no semble import required) -------------------


@pytest.mark.asyncio
async def test_provider_available_matches_dep_state():
    p = SembleSearchProvider(base_path=str(REPO_ROOT))
    avail = await p.available()
    assert avail == SEMBLE_AVAILABLE


@pytest.mark.asyncio
async def test_out_of_scope_path_raises():
    """A path outside the index root must raise so the capability chain falls
    through to grep cleanly. Empty list would be ambiguous (no match vs wrong
    index)."""
    p = SembleSearchProvider(base_path=str(REPO_ROOT))
    with pytest.raises(RuntimeError, match="outside index root"):
        # An absolute path that definitely isn't inside the repo root.
        await p.execute(query="x", path="C:/Windows/System32" if Path("C:/").exists() else "/tmp")


@pytest.mark.asyncio
async def test_in_scope_path_does_not_raise_before_indexing():
    """A path inside the index root passes the scope check (the test does NOT
    actually run search because indexing is expensive — covered in the e2e
    test). Just verifies the scope guard doesn't false-positive on a child."""
    p = SembleSearchProvider(base_path=str(REPO_ROOT))
    # Don't actually call execute (would build index). Just test the path
    # resolution logic by replicating it.
    target = (REPO_ROOT / "emptyos" / "capabilities").resolve()
    target.relative_to(p.base_path)  # must not raise


# --- End-to-end with real semble (skipped if dep missing) ----------------


@pytest.fixture(scope="module")
def _real_provider():
    """Module-scoped to avoid rebuilding the index per test."""
    if not SEMBLE_AVAILABLE:
        pytest.skip("semble not installed")
    # Use a small subdirectory to keep the test fast (the full repo would
    # take 30s+ to index on first run).
    return SembleSearchProvider(base_path=str(REPO_ROOT / "emptyos" / "capabilities"))


@pytest.mark.skipif(not SEMBLE_AVAILABLE, reason="semble not installed")
@pytest.mark.asyncio
async def test_search_returns_file_paths_in_files_mode(_real_provider):
    results = await _real_provider.execute(
        query="middleware chain for capability execution",
        mode="files_with_matches",
        limit=5,
    )
    assert isinstance(results, list)
    assert results, "expected at least one match"
    for r in results:
        assert "path" in r
    # The query is about middleware — top result should be the middleware module.
    paths = [r["path"] for r in results]
    assert any("middleware" in p for p in paths), f"expected middleware/ in {paths}"


@pytest.mark.skipif(not SEMBLE_AVAILABLE, reason="semble not installed")
@pytest.mark.asyncio
async def test_search_returns_chunks_in_content_mode(_real_provider):
    results = await _real_provider.execute(
        query="middleware",
        mode="content",
        limit=3,
    )
    assert results, "expected matches"
    for r in results:
        assert "path" in r
        assert "line_number" in r
        assert "text" in r
        assert isinstance(r["line_number"], int)
        # The extras semble provides:
        assert "end_line" in r
        assert "score" in r


@pytest.mark.skipif(not SEMBLE_AVAILABLE, reason="semble not installed")
@pytest.mark.asyncio
async def test_search_honors_type_filter(_real_provider):
    """type='py' should restrict to Python files."""
    results = await _real_provider.execute(
        query="provider",
        mode="files_with_matches",
        type="py",
        limit=10,
    )
    for r in results:
        assert r["path"].endswith(".py"), f"non-py path leaked: {r['path']}"


@pytest.mark.skipif(not SEMBLE_AVAILABLE, reason="semble not installed")
@pytest.mark.asyncio
async def test_reindex_returns_timing(_real_provider):
    info = await _real_provider.reindex()
    assert info["ok"] is True
    assert "elapsed_s" in info
    assert info["base_path"]


# --- Shape parity with GrepSearchProvider --------------------------------


def test_signature_matches_grep_provider():
    """SembleSearchProvider.execute and GrepSearchProvider.execute must
    share the same keyword-only parameters so callers can swap providers
    transparently."""
    import inspect

    from emptyos.capabilities.providers.grep_search import GrepSearchProvider

    grep_params = set(inspect.signature(GrepSearchProvider.execute).parameters)
    semble_params = set(inspect.signature(SembleSearchProvider.execute).parameters)
    # Semble must accept every kwarg grep accepts (drop-in compatible).
    missing = grep_params - semble_params
    assert not missing, f"semble missing kwargs grep accepts: {missing}"
