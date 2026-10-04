"""Filesystem snapshot primitive — pure, kernel-free directory snapshots.

Infrastructure-level filesystem operations (snapshot a directory tree into a
dated archive or folder, prune old snapshots, list them) used by the
``vault-backup`` app. Lives in the SDK rather than the app layer because the
app layer should orchestrate capabilities, not perform raw ``shutil`` /
``zipfile`` tree operations — the same reason ``pdf.py`` and
``vault_library.py`` own their direct I/O here. OS-agnostic (stdlib only, no
robocopy/wmic), so it runs identically on Windows, Linux, and inside Docker
(CLAUDE.md rule 20).

Three snapshot ``mode``s, each producing a dated entry under ``dest_root``:

- ``"incremental"`` (default) — ``<YYYY-MM-DD>/`` folder where files unchanged
  since the most recent prior snapshot are **hardlinks** (zero extra bytes),
  only changed/new files are copied. Best for large binary-heavy vaults: one
  full baseline + deltas, yet every snapshot is a complete browsable tree.
  Falls back to a plain copy per-file when hardlinks aren't supported
  (FAT/exFAT/network dest).
- ``"zip"`` — single ``<YYYY-MM-DD>.zip``. Good for text-heavy vaults where
  compression actually shrinks things; for binary-heavy vaults it mostly just
  bundles (≈1x ratio). Clamps pre-1980 mtimes (ZIP/DOS limit).
- ``"folder"`` — plain full-copy ``<YYYY-MM-DD>/`` folder, no linking.

Pure functions — no ``self``, no kernel, no capabilities. Unit-testable
without a running daemon.
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

# Non-hidden dirs never worth snapshotting (build/dependency trees). Every
# hidden dot-directory (vault-viewer config + caches, .git, .trash, search
# indexes, etc.) is excluded generically by the leading-dot rule in
# _is_excluded_dir — so the snapshot skips them all without naming any tool.
# ``site-packages`` catches bundled Python envs that sit outside a ``venv/``
# folder (e.g. an embedded/portable interpreter) — 20k+ files of pure churn.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "venv", "venv_mac", "site-packages",
})
# Media suffixes, split out so a caller can opt media back IN (pass an
# ``exclude_suffixes`` without these) for a local media backup — useful when
# media's only other copy is an off-site push.
MEDIA_SUFFIXES: frozenset[str] = frozenset({
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".mp3", ".wav", ".m4a", ".aac", ".flac",
})
# Archives + regenerable / cross-platform binaries — never authored knowledge.
_ARCHIVE_BINARY_SUFFIXES: frozenset[str] = frozenset({
    ".zip", ".rar", ".7z", ".exe", ".msi",
    ".pyc", ".pyo", ".pyd", ".dll", ".dylib", ".so",
})
# File suffixes excluded by default — large/binary media + archives + binaries.
DEFAULT_EXCLUDE_SUFFIXES: frozenset[str] = MEDIA_SUFFIXES | _ARCHIVE_BINARY_SUFFIXES

_STAMP_FMT = "%Y-%m-%d"


def _is_excluded_dir(name: str, exclude_dirs: frozenset[str]) -> bool:
    return name.startswith(".") or name in exclude_dirs


def _make_ignore(exclude_dirs: frozenset[str], exclude_suffixes: frozenset[str]):
    """shutil.copytree ignore callback (folder mode)."""
    def _ignore(dir_path, names):
        skipped = set()
        for n in names:
            full = Path(dir_path) / n
            if full.is_dir() and _is_excluded_dir(n, exclude_dirs):
                skipped.add(n)
            elif full.is_file() and full.suffix.lower() in exclude_suffixes:
                skipped.add(n)
        return skipped
    return _ignore


def _iter_included_files(
    src: Path, exclude_dirs: frozenset[str], exclude_suffixes: frozenset[str],
):
    """Yield ``(abs_path, arcname)`` for every file under ``src`` that passes
    the exclude filters. Prunes excluded dirs in-place so their trees are
    never walked."""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not _is_excluded_dir(d, exclude_dirs)]
        root_p = Path(root)
        for f in files:
            p = root_p / f
            if p.suffix.lower() in exclude_suffixes:
                continue
            yield p, p.relative_to(src).as_posix()


_MODES = ("incremental", "zip", "folder")


def _clear_same_day(dest_root: Path, stamp: str) -> None:
    """Remove any existing same-date snapshot in EITHER form (folder or .zip)
    so 'one snapshot per day' holds across mode switches and failed-run
    leftovers (e.g. a partial .zip from a crashed backup)."""
    folder = dest_root / stamp
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    archive = dest_root / f"{stamp}.zip"
    if archive.exists():
        try:
            archive.unlink()
        except OSError:
            pass


def _latest_prior_snapshot_dir(dest_root: Path, today_stamp: str) -> Path | None:
    """Newest dated FOLDER snapshot that isn't today's — the hardlink base."""
    best: Path | None = None
    best_dt: datetime | None = None
    for child in dest_root.iterdir():
        if not child.is_dir() or child.name == today_stamp:
            continue
        dt = _snapshot_date(child.name)
        if dt is None:
            continue
        if best_dt is None or dt > best_dt:
            best, best_dt = child, dt
    return best


def _unchanged(a: Path, b: Path) -> bool:
    """Heuristic file-identity check (rsync default): same size + mtime."""
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) <= 2


def snapshot_tree(
    src: Path,
    dest_root: Path,
    *,
    mode: str = "incremental",
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    exclude_suffixes: frozenset[str] = DEFAULT_EXCLUDE_SUFFIXES,
) -> dict:
    """Snapshot ``src`` into a dated entry under ``dest_root``. ``mode`` is one
    of ``"incremental"`` (default, hardlink + link-dest folder), ``"zip"``, or
    ``"folder"``. Mirror semantics for the day: an existing same-day snapshot is
    replaced. Returns a dict with ``snapshot, files, bytes, mode, duration_s``
    plus mode-specific extras (``copied/linked/added_bytes`` for incremental;
    ``compressed/compressed_bytes`` for zip). Raises ``FileNotFoundError`` when
    ``src`` is missing; other per-file I/O errors are skipped fail-soft.

    Blocking — call via ``asyncio.to_thread`` from an async context so it never
    holds the event loop.
    """
    if not src or not src.exists():
        raise FileNotFoundError(f"source path does not exist: {src}")
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
    stamp = datetime.now().strftime(_STAMP_FMT)
    dest_root.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    if mode == "zip":
        return _snapshot_zip(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0)
    if mode == "incremental":
        return _snapshot_incremental(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0)
    return _snapshot_folder(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0)


def _snapshot_zip(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0) -> dict:
    _clear_same_day(dest_root, stamp)
    target = dest_root / f"{stamp}.zip"
    files = size = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p, arc in _iter_included_files(src, exclude_dirs, exclude_suffixes):
            try:
                zf.write(p, arc)
                files += 1
                size += p.stat().st_size
            except ValueError:
                # ZIP stores DOS timestamps (>= 1980-01-01); a file whose mtime
                # predates that raises. Clamp to 1980 and write the bytes
                # directly so one glitched mtime can't fail the backup.
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                zi = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, data)
                files += 1
                size += len(data)
            except OSError:
                continue  # file vanished / locked mid-copy — skip, fail-soft
    return {
        "snapshot": str(target), "files": files, "bytes": size, "mode": "zip",
        "compressed": True, "compressed_bytes": target.stat().st_size,
        "duration_s": round(time.time() - t0, 1),
    }


def _snapshot_folder(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0) -> dict:
    _clear_same_day(dest_root, stamp)
    target = dest_root / stamp
    shutil.copytree(
        src, target, ignore=_make_ignore(exclude_dirs, exclude_suffixes),
        dirs_exist_ok=True, symlinks=False,
    )
    files = size = 0
    for p in target.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return {
        "snapshot": str(target), "files": files, "bytes": size, "mode": "folder",
        "compressed": False, "duration_s": round(time.time() - t0, 1),
    }


def _snapshot_incremental(src, dest_root, stamp, exclude_dirs, exclude_suffixes, t0) -> dict:
    """Hardlink + link-dest snapshot. Files unchanged since the most recent
    prior snapshot folder are hardlinked (0 extra bytes); changed/new files are
    copied. Each snapshot is a complete, browsable tree. Pruning an old snapshot
    only frees data no other snapshot still references (hardlink refcount).
    Falls back to a per-file copy when the dest can't hardlink (FAT/network)."""
    _clear_same_day(dest_root, stamp)
    target = dest_root / stamp
    target.mkdir(parents=True, exist_ok=True)
    link_base = _latest_prior_snapshot_dir(dest_root, stamp)
    files = copied = linked = 0
    size = added = 0
    link_attempts = link_failures = 0
    for p, arc in _iter_included_files(src, exclude_dirs, exclude_suffixes):
        try:
            st = p.stat()
        except OSError:
            continue
        dest_file = target / arc
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        base_file = (link_base / arc) if link_base else None
        hardlinked = False
        if base_file is not None and base_file.exists() and _unchanged(p, base_file):
            link_attempts += 1
            try:
                os.link(base_file, dest_file)
                linked += 1
                hardlinked = True
            except OSError:
                link_failures += 1  # hardlink unsupported (FAT/network) — copy
        if not hardlinked:
            try:
                shutil.copy2(p, dest_file)
                copied += 1
                added += st.st_size
            except OSError:
                continue  # file vanished / locked mid-copy — skip, fail-soft
        files += 1
        size += st.st_size
    # A dest that can't hardlink fails every attempt — flag so the UI can warn
    # the user their snapshots are silently full copies (no dedup).
    hardlink_supported = not (link_attempts > 0 and link_failures == link_attempts)
    return {
        "snapshot": str(target), "files": files, "bytes": size,
        "mode": "incremental", "compressed": False,
        "copied": copied, "linked": linked, "added_bytes": added,
        "had_prior": link_base is not None, "hardlink_supported": hardlink_supported,
        "duration_s": round(time.time() - t0, 1),
    }


def _snapshot_date(name: str) -> datetime | None:
    """Parse the snapshot date from a folder or ``.zip`` name, else None."""
    stem = name[:-4] if name.lower().endswith(".zip") else name
    try:
        return datetime.strptime(stem, _STAMP_FMT)
    except ValueError:
        return None


def prune_snapshots(dest_root: Path, retention_days: int) -> list[str]:
    """Remove dated snapshots (``YYYY-MM-DD`` folders or ``YYYY-MM-DD.zip``
    files) older than ``retention_days``. Non-dated entries are left untouched.
    Returns the names removed. Blocking — call via ``asyncio.to_thread``."""
    if not dest_root.exists():
        return []
    cutoff = time.time() - max(1, retention_days) * 86400
    removed: list[str] = []
    for child in dest_root.iterdir():
        dt = _snapshot_date(child.name)
        if dt is None or dt.timestamp() >= cutoff:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                continue
        removed.append(child.name)
    return removed


def list_snapshots(dest_root: Path) -> list[dict]:
    """List dated snapshots (folders + ``.zip`` files) newest-first as
    ``[{name, path, mtime, compressed, size}]``. Non-dated entries skipped."""
    out: list[dict] = []
    if not dest_root.exists():
        return out
    for child in sorted(dest_root.iterdir(), reverse=True):
        if _snapshot_date(child.name) is None:
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime).isoformat()
        except OSError:
            mtime = None
        is_zip = child.is_file() and child.name.lower().endswith(".zip")
        try:
            size = child.stat().st_size if is_zip else None
        except OSError:
            size = None
        out.append({
            "name": child.name, "path": str(child), "mtime": mtime,
            "compressed": is_zip, "size": size,
        })
    return out


def restore_snapshot(snapshot: Path, target: Path) -> dict:
    """Restore a snapshot (folder or ``.zip``) into ``target`` — an independent
    copy with hardlinks dereferenced (real files), so the restore stands alone
    even after the source snapshot is pruned. The caller chooses ``target``;
    this never writes into the live vault. Returns ``{target, files, bytes}``.
    Raises ``FileNotFoundError`` / ``ValueError`` on a bad snapshot.

    Blocking — call via ``asyncio.to_thread``."""
    if not snapshot.exists():
        raise FileNotFoundError(f"snapshot does not exist: {snapshot}")
    target.mkdir(parents=True, exist_ok=True)
    files = size = 0
    if snapshot.is_file() and snapshot.name.lower().endswith(".zip"):
        with zipfile.ZipFile(snapshot) as zf:
            zf.extractall(target)  # our own archive — names are vault-relative
            for info in zf.infolist():
                if not info.is_dir():
                    files += 1
                    size += info.file_size
        return {"target": str(target), "files": files, "bytes": size}
    if snapshot.is_dir():
        # copytree uses copy2 → each file becomes an independent copy (any
        # hardlinks in the snapshot are dereferenced into standalone files).
        shutil.copytree(snapshot, target, dirs_exist_ok=True, symlinks=False)
        for p in target.rglob("*"):
            if p.is_file():
                files += 1
                try:
                    size += p.stat().st_size
                except OSError:
                    pass
        return {"target": str(target), "files": files, "bytes": size}
    raise ValueError(f"not a restorable snapshot (zip or folder): {snapshot}")
