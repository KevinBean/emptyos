"""Search — background maintenance of the vault embedding index.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the on-disk manifest that lets ``_embed_search`` answer without
touching the filesystem, and the scheduled sweep that keeps it current.

WHY THIS EXISTS
---------------
``_embed_search`` used to rebuild its candidate set on every query: rglob the
vault, read every candidate file, hash each one to look up its cached vector.
Measured at a 12,000-note cap that is ~3.1s of a 3.6s warm search -- ~86% of the
time spent re-reading files the daemon had already read on the previous query.
The candidate cap exists *because* of that cost, which is what capped semantic
recall at a fraction of the vault.

So indexing moves off the query path: a scheduled sweep embeds what changed and
records ``{path: mtime, size, sig}``; a search then resolves sig -> vector
straight from the embedder's content-hash cache and never opens a note.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Reaches into other modules: uses ``_embed_candidate_order`` +
``_EMBED_SKIP_DIRS`` from ``.app`` at call time via ``self`` / passed args only.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import atomic_write_text, scheduled, web_route
from emptyos.sdk.embeddings import _sig

if TYPE_CHECKING:
    from .app import SearchApp  # noqa: F401 — for type hints only


# ─── Bind to SearchApp class as ──────────────────────────────────────
#   sweep_embed_index      = _indexer.sweep_embed_index      # @scheduled
#   api_embed_index_status = _indexer.api_embed_index_status # @web_route
#   api_embed_index_build  = _indexer.api_embed_index_build  # @web_route
#   _index_manifest_path   = _indexer._index_manifest_path
#   _load_index_manifest   = _indexer._load_index_manifest
#   _save_index_manifest   = _indexer._save_index_manifest
#   _background_index_on   = _indexer._background_index_on
#   _run_index_sweep       = _indexer._run_index_sweep
#   _candidates_from_manifest = _indexer._candidates_from_manifest
#   _read_changed             = _indexer._read_changed
#   _scan_vault_files         = _indexer._scan_vault_files
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

MANIFEST_VERSION = 1
# One sweep never embeds more than this. A first run on a cold vault would
# otherwise hold the worker for the better part of an hour; instead each sweep
# advances the frontier and the next one continues.
SWEEP_EMBED_BUDGET = 1200
SWEEP_READ_BUDGET = 8000


def _background_index_on(self) -> bool:
    return bool(self.app_config("feature.background-index.enabled", False))


def _index_manifest_path(self) -> Path:
    return self.data_dir / "embed-manifest.json"


def _load_index_manifest(self) -> dict:
    try:
        data = json.loads(self._index_manifest_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return {}
    # A manifest built for a different embedding model is worthless: its sigs
    # key vectors of another dimension in another cache namespace.
    if data.get("model") != getattr(self._embedder(), "model", ""):
        return {}
    return data


def _save_index_manifest(self, entries: dict, *, complete: bool) -> None:
    payload = {
        "version": MANIFEST_VERSION,
        "model": getattr(self._embedder(), "model", ""),
        "updated": time.time(),
        "complete": complete,
        "entries": entries,
    }
    path = self._index_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Shared primitive rather than a hand-rolled tmp+rename: it fsyncs the
    # payload before replacing and cleans up a stranded temp on failure
    # (.claude/rules/atomic-persistence.md).
    atomic_write_text(path, json.dumps(payload))


def _scan_vault_files(self, skip_dirs: set[str]) -> list[tuple[str, float, int]]:
    """(rel, mtime, size) for every candidate note. Blocking — call in a thread.

    DELIBERATE exception to CLAUDE.md rule 1 (apps use capabilities, not raw
    tools): the read capability is an ``async def`` around sync I/O that never
    yields, so awaiting it once per file across ~24k notes is the documented way
    to wedge this daemon. Bulk stat/read belongs in a worker thread instead, and
    this is the same shape ``_embed_search`` in app.py already uses. Do not
    "fix" this to ``await self.read()``.
    """
    vault = Path(self._vault_path())
    if not vault.exists():
        return []
    out: list[tuple[str, float, int]] = []
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault)
        if any(part in skip_dirs or part.startswith(".") for part in rel.parts):
            continue
        if rel.name.startswith("PLAYWRIGHT-TEST-"):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((rel.as_posix(), st.st_mtime, st.st_size))
    return out


def _read_changed(
    self, vault: Path, todo: list[str], limit: int
) -> list[tuple[str, str]]:
    """(rel, text) for notes needing re-embedding. Blocking — call in a thread.

    Raw ``read_text`` for the same reason as ``_scan_vault_files`` above — see
    that docstring before changing this to the read capability.
    """
    out: list[tuple[str, str]] = []
    for rel in todo[:limit]:
        try:
            text = (vault / rel).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if text.strip():
            out.append((rel, text[: self._EMBED_TEXT_LIMIT]))
    return out


async def _run_index_sweep(self, *, embed_budget: int = SWEEP_EMBED_BUDGET) -> dict:
    """Bring the manifest up to date. Returns a small report.

    Every filesystem walk and read happens in a worker thread. Doing it inline
    would block the event loop for seconds across thousands of files, which is
    the documented way to wedge this daemon (CLAUDE.md § Development Gotchas —
    a filesystem provider is an ``async def`` around sync I/O and never yields).
    """
    # `available` is a PROPERTY, not a method — calling it raises
    # "'bool' object is not callable" and kills the sweep.
    embedder = self._embedder()
    if not embedder or not self.embeddings_available:
        return {"ok": False, "reason": "embeddings unavailable"}

    from .app import _EMBED_SKIP_DIRS  # local import: app.py imports this module

    t0 = time.time()
    files = await asyncio.to_thread(self._scan_vault_files, _EMBED_SKIP_DIRS)
    if not files:
        return {"ok": False, "reason": "vault empty or unreachable"}

    manifest = self._load_index_manifest()
    entries: dict = dict(manifest.get("entries") or {})

    # mtime+size is the change test. mtime alone is a selection heuristic that
    # git and sync rewrite (.claude/rules/scoped-retrieval.md), but paired with
    # size it is only ever used to decide whether to re-read -- a false "changed"
    # costs one read, never a wrong answer.
    live = {rel for rel, _, _ in files}
    todo = [
        rel for rel, mt, sz in files
        if (e := entries.get(rel)) is None or e.get("m") != mt or e.get("s") != sz
    ]
    for gone in [r for r in entries if r not in live]:
        entries.pop(gone, None)

    stat_by_rel = {rel: (mt, sz) for rel, mt, sz in files}
    vault = Path(self._vault_path())
    pairs = await asyncio.to_thread(self._read_changed, vault, todo, SWEEP_READ_BUDGET)

    # Only embed what the cache does not already hold; a re-read whose content is
    # unchanged (git touched the mtime) resolves to a sig already on disk.
    cache = embedder.cache
    fresh = [(rel, text, _sig(text)) for rel, text in pairs]
    missing = [(rel, text, sig) for rel, text, sig in fresh if sig not in cache]
    to_embed = missing[:embed_budget]
    if to_embed:
        await embedder.embed_many([t for _, t, _ in to_embed])

    embedded_sigs = {sig for _, _, sig in to_embed}
    for rel, _text, sig in fresh:
        if sig in cache or sig in embedded_sigs:
            mt, sz = stat_by_rel.get(rel, (0.0, 0))
            entries[rel] = {"m": mt, "s": sz, "sig": sig}

    complete = len(missing) <= len(to_embed) and len(todo) <= len(pairs)
    self._save_index_manifest(entries, complete=complete)
    report = {
        "ok": True,
        "files": len(files),
        "indexed": len(entries),
        "changed": len(todo),
        "embedded": len(to_embed),
        "remaining": max(0, len(missing) - len(to_embed)),
        "complete": complete,
        "seconds": round(time.time() - t0, 1),
    }
    await self.emit("search:index_swept", report)
    return report


@scheduled("17 * * * *", id="embed-index-sweep")
async def sweep_embed_index(self) -> None:
    """Hourly: keep the embedding manifest current so searches stay off disk.

    Off-hour minute so it does not pile onto the top-of-hour cron crowd. Dark by
    default — with the flag off the sweep does nothing and the query path is
    byte-identical to before this module existed.
    """
    if not self._background_index_on():
        return
    try:
        report = await self._run_index_sweep()
    except Exception as e:
        self.log_activity({"action": "embed_index_sweep", "error": str(e)})
        return
    if report.get("ok") and (report.get("embedded") or report.get("remaining")):
        self.log_activity({"action": "embed_index_sweep", **report})


def _candidates_from_manifest(self, cap: int, cold_prefixes: tuple[str, ...]):
    """Candidate items + their vectors, read entirely from the manifest.

    Returns ``(items, embeddings, meta)`` or ``None`` when the manifest cannot
    serve the request — caller then falls back to the live filesystem walk, so a
    missing or stale manifest degrades to the old behaviour rather than to no
    results.
    """
    manifest = self._load_index_manifest()
    entries = manifest.get("entries") or {}
    if not entries:
        return None

    from .app import _embed_candidate_order

    ordered = _embed_candidate_order(
        [(rel, float(e.get("m") or 0.0)) for rel, e in entries.items()], cold_prefixes
    )
    cache = self._embedder().cache
    vault = Path(self._vault_path())
    items: list[dict] = []
    embeddings: list[list[float]] = []
    scanned = 0
    for rel, _mtime in ordered:
        if len(items) >= cap:
            break
        scanned += 1
        vec = cache.get((entries[rel] or {}).get("sig") or "")
        if not vec:
            continue  # not embedded yet; the next sweep picks it up
        items.append({"path": str(vault / rel).replace("\\", "/"), "text": ""})
        embeddings.append(vec)

    if not items:
        return None
    meta = {
        "total": len(ordered),
        "indexed": len(items),
        "truncated": len(items) >= cap and scanned < len(ordered),
        "source": "manifest",
    }
    return items, embeddings, meta


@web_route("GET", "/api/index/status")
async def api_embed_index_status(self, request):
    manifest = self._load_index_manifest()
    entries = manifest.get("entries") or {}
    return {
        "enabled": self._background_index_on(),
        "indexed": len(entries),
        "complete": bool(manifest.get("complete")),
        "updated": manifest.get("updated"),
        "model": manifest.get("model", ""),
    }


@web_route("POST", "/api/index/build")
async def api_embed_index_build(self, request):
    """Run a sweep now. Useful for the first build rather than waiting an hour."""
    if not self._background_index_on():
        return {"ok": False, "error": "background index disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    budget = int(body.get("budget") or SWEEP_EMBED_BUDGET)
    return await self._run_index_sweep(embed_budget=max(1, budget))
