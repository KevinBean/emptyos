"""Auto-hash SQLite cache for think() responses.

Keyed by SHA-256 of all inputs (prompt, system, model, temperature,
max_tokens, agent, domain). On a cache hit the provider is never called
and no billing event is emitted — $0 cost, zero latency.

Distinct from `llm_cache.py` (vault-backed, caller-keyed, curated).
This module is for transparent, automatic caching via `think(cache=True)`.

Provider-side caching (Anthropic prompt cache, OpenAI cached tokens) is a
separate layer — those still make API calls, just cheaper. This cache
eliminates the API call entirely.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_CREATE = """
CREATE TABLE IF NOT EXISTS entries (
    cache_id  TEXT PRIMARY KEY,
    app_id    TEXT NOT NULL,
    prompt    TEXT NOT NULL,
    system    TEXT,
    model     TEXT,
    response  TEXT NOT NULL,
    hits      INTEGER NOT NULL DEFAULT 0,
    stored_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_app ON entries (app_id);
CREATE INDEX IF NOT EXISTS idx_entries_exp ON entries (expires_at) WHERE expires_at IS NOT NULL;
"""


_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def db_path(app) -> Path:
    """Resolve `data/cache/think.db` relative to the project root."""
    return Path(app.kernel.config.path).parent / "data" / "cache" / "think.db"


def _conn(path: Path) -> sqlite3.Connection:
    key = str(path)
    if key not in _CONNECTIONS:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_CREATE)
        _CONNECTIONS[key] = conn
    return _CONNECTIONS[key]


def make_key(
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    temperature: Any = None,
    max_tokens: Any = None,
    agent: str | None = None,
    domain: str | None = None,
) -> str:
    """16-char SHA-256 prefix of all inputs that affect the response."""
    payload = json.dumps(
        {
            "prompt": prompt,
            "system": system or "",
            "model": model or "",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "agent": agent,
            "domain": domain,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def get(path: Path, cache_id: str) -> str | None:
    """Return cached response string, or None on miss/expiry."""
    try:
        conn = _conn(path)
        now = datetime.now().isoformat(timespec="seconds")
        row = conn.execute(
            "SELECT response, expires_at FROM entries WHERE cache_id = ?",
            (cache_id,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < now:
            conn.execute("DELETE FROM entries WHERE cache_id = ?", (cache_id,))
            conn.commit()
            return None
        conn.execute("UPDATE entries SET hits = hits + 1 WHERE cache_id = ?", (cache_id,))
        conn.commit()
        return row["response"]
    except Exception:
        return None


def put(
    path: Path,
    cache_id: str,
    *,
    prompt: str,
    system: str | None,
    model: str | None,
    response: str,
    app_id: str,
    ttl_hours: int | None = None,
) -> bool:
    """Store a response. Returns True on success."""
    try:
        conn = _conn(path)
        now = datetime.now()
        expires_at = None
        if ttl_hours is not None:
            expires_at = (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT OR REPLACE INTO entries
                (cache_id, app_id, prompt, system, model, response, hits, stored_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                cache_id,
                app_id,
                prompt,
                system or "",
                model or "",
                response,
                now.isoformat(timespec="seconds"),
                expires_at,
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False


def clear(path: Path, app_id: str | None = None) -> int:
    """Delete entries. If app_id given, only that app's entries. Returns count deleted."""
    try:
        conn = _conn(path)
        if app_id:
            cur = conn.execute("DELETE FROM entries WHERE app_id = ?", (app_id,))
        else:
            cur = conn.execute("DELETE FROM entries")
        conn.commit()
        return cur.rowcount
    except Exception:
        return 0


def evict_expired(path: Path) -> int:
    """Delete all expired entries. Returns count deleted."""
    try:
        conn = _conn(path)
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "DELETE FROM entries WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        )
        conn.commit()
        return cur.rowcount
    except Exception:
        return 0


def stats(path: Path) -> dict:
    """Return {entries, hits, app_breakdown, oldest, newest} for the whole cache."""
    try:
        conn = _conn(path)
        row = conn.execute(
            "SELECT COUNT(*) as n, SUM(hits) as h, MIN(stored_at) as old, MAX(stored_at) as new FROM entries"
        ).fetchone()
        breakdown = {
            r["app_id"]: r["cnt"]
            for r in conn.execute(
                "SELECT app_id, COUNT(*) as cnt FROM entries GROUP BY app_id"
            ).fetchall()
        }
        return {
            "entries": row["n"] or 0,
            "hits": row["h"] or 0,
            "oldest": row["old"],
            "newest": row["new"],
            "app_breakdown": breakdown,
        }
    except Exception:
        return {"entries": 0, "hits": 0, "oldest": None, "newest": None, "app_breakdown": {}}
