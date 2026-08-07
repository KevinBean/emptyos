"""System Log — structured, persisted, queryable log for EmptyOS.

All apps, plugins, and kernel components can log here.
Persisted to SQLite, queryable via API, shown in system-log app.

Levels: debug, info, warn, error
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path


# Personal-pattern scrubber for log writes.
# Capability handlers sometimes log `f"think failed for prompt: {prompt}"` or
# similar — raw prompt fragments persisting in `data/syslog.db` is exactly the
# leak class .eos-personal is meant to catch. Load patterns once on first use;
# fail-open if the load fails (syslog must keep working).
_SCRUB_PATTERNS: list[re.Pattern] | None = None


def _load_scrub_patterns() -> list[re.Pattern]:
    global _SCRUB_PATTERNS
    if _SCRUB_PATTERNS is not None:
        return _SCRUB_PATTERNS
    try:
        from emptyos.sdk.personal_patterns import find_and_load

        _SCRUB_PATTERNS = find_and_load()
    except Exception:
        _SCRUB_PATTERNS = []
    return _SCRUB_PATTERNS


def _scrub(value):
    """Recursively replace personal-pattern matches with `***`.

    Operates on strings; descends into dicts/lists; other types pass through.
    """
    pats = _load_scrub_patterns()
    if not pats:
        return value
    if isinstance(value, str):
        out = value
        for pat in pats:
            out = pat.sub("***", out)
        return out
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(x) for x in value]
    return value


class SystemLog:
    """Kernel-level structured log. Thread-safe via WAL mode."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Guards the shared connection. Writes arrive from the event loop while
        # retention (`trim`) runs on an asyncio.to_thread worker, so the two
        # must serialize — same reasoning as EventBus._db_lock.
        self._lock = threading.Lock()
        # timeout=15: SQLite's own retry-before-raising window. Longer than
        # the 5s default because a just-killed daemon's WAL handle on this
        # file can take a few seconds to release (daemon-handling.md); see
        # the try/except in log() below for what happens if it still isn't
        # enough.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=15.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS syslog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                source TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                data TEXT DEFAULT '',
                job_id TEXT DEFAULT ''
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_syslog_ts ON syslog(ts DESC)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_syslog_source ON syslog(source)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_syslog_level ON syslog(level)")
        self._conn.commit()
        self._pending = 0

    def log(
        self, level: str, source: str, message: str, data: dict | None = None, job_id: str = ""
    ):
        """Write a log entry. WAL mode means commits are cheap but we still batch.

        Personal-pattern scrubbing applies to `message` + every string inside
        `data` before insert (and before console print). See `_scrub` at the
        top of this module.

        The DB write is best-effort: logging must never be able to crash its
        caller. A transient "database is locked" (e.g. a stale WAL handle
        still releasing from a just-killed daemon, daemon-handling.md) used
        to propagate straight out of this call — app_loader's harmless "slow
        load" diagnostic and plugin_loader's success/failure report both hit
        it and took down otherwise-fine app/plugin loads with it
        (data/wedge-evidence/20260801T080139Z and siblings). A dropped log
        row is a fine trade for that; the message still prints below either
        way.
        """
        safe_message = _scrub(message) if isinstance(message, str) else message
        safe_data = _scrub(data) if data else (data or {})
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO syslog (ts, level, source, message, data, job_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), level, source, safe_message, json.dumps(safe_data, default=str), job_id),
                )
                self._pending += 1
                if self._pending >= 10 or level in ("error", "warn"):
                    self._conn.commit()
                    self._pending = 0
        except sqlite3.Error:
            pass
        # Console output (scrubbed — stdout gets captured to log files by the
        # launcher; let's not undo the DB scrub at the print boundary)
        tag = f"[{source}]" if source else ""
        lvl = level.upper() if level != "info" else ""
        prefix = f"{lvl} {tag}" if lvl else tag
        print(f"{prefix} {safe_message}")

    def flush(self):
        """Flush pending writes."""
        with self._lock:
            if self._pending > 0:
                self._conn.commit()
                self._pending = 0

    def info(self, source: str, message: str, **kwargs):
        self.log("info", source, message, **kwargs)

    def warn(self, source: str, message: str, **kwargs):
        self.log("warn", source, message, **kwargs)

    # Muscle memory writes `.warning` — that is what stdlib `logging` calls it,
    # and `warn` is the *deprecated* alias there, so the instinct is well-trained
    # and backwards here. Six sites in one plugin had already written it; every
    # one sat on an error path, so each was an AttributeError waiting for the
    # exact moment something else had already gone wrong. The stored level stays
    # "warn" so queries and existing rows are unchanged.
    warning = warn

    def error(self, source: str, message: str, **kwargs):
        self.log("error", source, message, **kwargs)

    def debug(self, source: str, message: str, **kwargs):
        self.log("debug", source, message, **kwargs)

    def query(
        self,
        limit: int = 100,
        level: str = "",
        source: str = "",
        since: float = 0,
        job_id: str = "",
    ) -> list[dict]:
        """Query log entries."""
        sql = "SELECT id, ts, level, source, message, data, job_id FROM syslog WHERE 1=1"
        params: list = []
        if level:
            sql += " AND level = ?"
            params.append(level)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if job_id:
            sql += " AND job_id = ?"
            params.append(job_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "level": r[2],
                "source": r[3],
                "message": r[4],
                "data": json.loads(r[5]) if r[5] else {},
                "job_id": r[6],
            }
            for r in rows
        ]

    def trim(self, keep: int = 5000) -> int:
        """Keep only the most recent N entries. Returns rows removed.

        Synchronous — the kernel housekeeping loop calls this via
        asyncio.to_thread, so the connection lock is required.
        """
        if keep <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM syslog WHERE id NOT IN (SELECT id FROM syslog ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()
            return cur.rowcount or 0
