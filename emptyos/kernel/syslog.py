"""System Log — structured, persisted, queryable log for EmptyOS.

All apps, plugins, and kernel components can log here.
Persisted to SQLite, queryable via API, shown in system-log app.

Levels: debug, info, warn, error
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class SystemLog:
    """Kernel-level structured log. Thread-safe via WAL mode."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
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
        """Write a log entry. WAL mode means commits are cheap but we still batch."""
        self._conn.execute(
            "INSERT INTO syslog (ts, level, source, message, data, job_id) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), level, source, message, json.dumps(data or {}, default=str), job_id),
        )
        self._pending += 1
        if self._pending >= 10 or level in ("error", "warn"):
            self._conn.commit()
            self._pending = 0
        # Console output
        tag = f"[{source}]" if source else ""
        lvl = level.upper() if level != "info" else ""
        prefix = f"{lvl} {tag}" if lvl else tag
        print(f"{prefix} {message}")

    def flush(self):
        """Flush pending writes."""
        if self._pending > 0:
            self._conn.commit()
            self._pending = 0

    def info(self, source: str, message: str, **kwargs):
        self.log("info", source, message, **kwargs)

    def warn(self, source: str, message: str, **kwargs):
        self.log("warn", source, message, **kwargs)

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

    def trim(self, keep: int = 5000):
        """Keep only the most recent N entries."""
        self._conn.execute(
            "DELETE FROM syslog WHERE id NOT IN (SELECT id FROM syslog ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        self._conn.commit()
