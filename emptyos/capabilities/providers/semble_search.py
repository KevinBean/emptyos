"""Semble search provider — Model2Vec embeddings + BM25 + RRF hybrid code search.

CPU-only, no API keys, no GPU. Returns code chunks (file_path + line range +
content) for a natural-language or keyword query in ~3ms after the index
is built. Designed for the `search` capability's `"code"` domain — does NOT
participate in the default chain so vault search behavior is unchanged.

Registration (in setup.py):
    search.add_domain("code", [
        SembleSearchProvider(base_path=repo_root),
        GrepSearchProvider(base_path=repo_root),  # fallback
    ])

Apps invoke via:
    await self.search("middleware chain", domain="code")

The index is built lazily on first call. Building takes ~5-30s depending on
codebase size; it lives in memory for the daemon's lifetime. Currently no
auto-invalidation on file changes — apps that need fresh results can call
the plugin's reindex service directly (future work).

When `semble` isn't installed, `available()` returns False and the chain
falls through to grep — same graceful-enhancement pattern as Playwright /
Headroom.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from emptyos.capabilities import Provider


class SembleSearchProvider(Provider):
    """Hybrid semantic + BM25 code search via the `semble` library.

    Same kwargs shape as ``GrepSearchProvider.execute`` so callers don't
    branch. Filters that semble doesn't natively support (case_insensitive,
    context) are accepted-but-ignored; semble's reranking subsumes them.
    The ``glob`` filter is approximated via ``filter_paths`` (post-search
    filter on path prefix); apps needing precise glob semantics should
    use ``domain="code"``'s grep fallback by passing a very-specific query.
    """

    name = "semble"

    def __init__(self, base_path: str = ""):
        self.base_path = Path(base_path).resolve() if base_path else Path.cwd().resolve()
        self._index = None  # lazy: semble.SembleIndex
        self._index_lock = asyncio.Lock()
        self._available_cached: bool | None = None

    async def available(self) -> bool:
        if self._available_cached is not None:
            return self._available_cached
        try:
            import semble  # noqa: F401
        except ImportError:
            self._available_cached = False
            return False
        self._available_cached = True
        return True

    async def _ensure_index(self):
        """Build the in-memory index on first call. Holds for daemon lifetime."""
        if self._index is not None:
            return
        async with self._index_lock:
            if self._index is not None:
                return  # racer won
            import semble  # noqa: PLC0415

            # SembleIndex.from_path is synchronous + CPU-bound; run in a
            # thread so we don't block the event loop during the ~5-30s
            # initial index build.
            self._index = await asyncio.to_thread(
                semble.SembleIndex.from_path,
                self.base_path,
            )

    async def execute(
        self,
        *,
        query: str,
        path: str = "",
        mode: str = "files_with_matches",
        case_insensitive: bool = True,  # accepted, ignored (semantic search)
        glob: str = "",
        type: str = "",
        context: int = 0,  # accepted, ignored
        limit: int = 200,
        **kwargs: Any,
    ) -> list[dict]:
        # Out-of-scope path → raise so the chain falls through to grep.
        # An empty result would be ambiguous ("no match" vs "wrong index"),
        # so a raised exception is clearer.
        if path:
            target = Path(path).resolve()
            try:
                target.relative_to(self.base_path)
            except ValueError as e:
                raise RuntimeError(
                    f"semble: path {target} is outside index root {self.base_path}"
                ) from e

        await self._ensure_index()
        if self._index is None:
            raise RuntimeError("semble: index unavailable after _ensure_index")

        # Filter paths approximation: if a glob is given, we let semble search
        # freely then post-filter on the glob (Path.match). Cheap and matches
        # user expectations for "find Python files about X".
        # type="py" → filter to .py extension via glob.
        ext_glob = ""
        if type and not glob:
            ext_glob = f"**/*.{type.lstrip('.')}"

        # semble's search is synchronous; run in thread to keep event loop free.
        top_k = max(1, int(limit))
        results = await asyncio.to_thread(self._index.search, query, top_k)

        # Apply glob filter if present.
        filter_glob = glob or ext_glob
        if filter_glob:
            filtered = []
            for r in results:
                fp = r.chunk.file_path
                if Path(fp).match(filter_glob):
                    filtered.append(r)
            results = filtered

        if mode == "files_with_matches":
            # Dedupe by file path, preserve rank order.
            seen: set[str] = set()
            out: list[dict] = []
            for r in results:
                fp = r.chunk.file_path
                if fp in seen:
                    continue
                seen.add(fp)
                out.append({"path": fp})
                if len(out) >= top_k:
                    break
            return out

        # mode == "content" — one entry per chunk, mapped to grep's shape.
        return [
            {
                "path": r.chunk.file_path,
                "line_number": r.chunk.start_line,
                "text": r.chunk.content,
                # Extras only semble provides — apps can ignore safely.
                "end_line": r.chunk.end_line,
                "score": getattr(r, "score", None),
            }
            for r in results[:top_k]
        ]

    async def reindex(self) -> dict:
        """Drop the current index so the next call rebuilds. Returns timing info."""
        import time

        async with self._index_lock:
            self._index = None
        t0 = time.time()
        await self._ensure_index()
        return {
            "ok": True,
            "base_path": str(self.base_path),
            "elapsed_s": round(time.time() - t0, 2),
        }
