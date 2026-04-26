"""Vault-backed LLM response cache.

Deterministic cache keyed by (app_id, logical_key). Persisted as JSON notes
under `{vault}/30_Resources/EmptyOS/llm-cache/{app_id}/{hash}.json` so that
the same prompt produces the same answer across restarts without another
model call — critical for demo-day reliability.

Use via `BaseApp.think_cached(prompt, key=...)` rather than the module
functions directly. Module functions are public for testing and for apps
that need to seed / inspect / clear cache entries.

Design notes
------------
* Cache entries are **content-addressed by `key`**, not by prompt contents.
  The caller decides what invalidates a cache entry: if the key includes
  the model name or prompt version, upgrading invalidates automatically.
* The stored JSON carries prompt + system + response + metadata so future
  debugging can inspect what was actually sent.
* `force_live=True` on `think_cached` bypasses the read path but still
  writes the fresh output back to cache for next time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

CACHE_DIR_REL = "30_Resources/EmptyOS/llm-cache"


def hash_key(app_id: str, key: Any) -> str:
    """Build a deterministic 16-char cache id from app + logical key.

    `key` can be anything JSON-serialisable — a string, tuple, or dict.
    Dicts / nested structures are sorted so order doesn't matter.
    """
    payload = json.dumps(
        {"app": app_id, "key": key},
        sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _cache_path(vault_root: Path, app_id: str, cache_id: str) -> Path:
    return vault_root / CACHE_DIR_REL / app_id / f"{cache_id}.json"


def _vault_root_of(app) -> Path | None:
    """Resolve the mounted vault path from the app's kernel config."""
    try:
        vault = app.kernel.config.notes_path
    except AttributeError:
        return None
    return Path(vault) if vault else None


def cache_get(app, cache_id: str) -> str | None:
    """Return the cached response string for `cache_id`, or None on miss."""
    root = _vault_root_of(app)
    if root is None:
        return None
    p = _cache_path(root, app.manifest.id, cache_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("response")
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(app, cache_id: str, prompt: str, system: str | None,
              response: str, *, key: Any = None, meta: dict | None = None) -> bool:
    """Persist an LLM response to the vault cache. Returns True on success."""
    root = _vault_root_of(app)
    if root is None:
        return False
    p = _cache_path(root, app.manifest.id, cache_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": app.manifest.id,
        "cache_id": cache_id,
        "key": key,
        "prompt": prompt,
        "system": system,
        "response": response,
        "stored_at": datetime.now().isoformat(timespec="seconds"),
        "meta": meta or {},
    }
    try:
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return True
    except OSError:
        return False


def cache_clear(app, key: Any | None = None) -> int:
    """Remove cache entries. If `key` given, remove only that one; otherwise
    clear the whole app's cache directory. Returns the number of files removed."""
    root = _vault_root_of(app)
    if root is None:
        return 0
    if key is not None:
        p = _cache_path(root, app.manifest.id, hash_key(app.manifest.id, key))
        if p.exists():
            p.unlink()
            return 1
        return 0
    app_dir = root / CACHE_DIR_REL / app.manifest.id
    if not app_dir.exists():
        return 0
    removed = 0
    for child in app_dir.glob("*.json"):
        try:
            child.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def cache_stats(app) -> dict:
    """Return {entries, bytes, oldest, newest} for this app's cache."""
    root = _vault_root_of(app)
    if root is None:
        return {"entries": 0, "bytes": 0, "oldest": None, "newest": None}
    app_dir = root / CACHE_DIR_REL / app.manifest.id
    if not app_dir.exists():
        return {"entries": 0, "bytes": 0, "oldest": None, "newest": None}
    entries = 0; total = 0
    oldest = None; newest = None
    for child in app_dir.glob("*.json"):
        try:
            st = child.stat()
        except OSError:
            continue
        entries += 1
        total += st.st_size
        if oldest is None or st.st_mtime < oldest:
            oldest = st.st_mtime
        if newest is None or st.st_mtime > newest:
            newest = st.st_mtime
    def _iso(t):
        return datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else None
    return {"entries": entries, "bytes": total,
            "oldest": _iso(oldest), "newest": _iso(newest)}
