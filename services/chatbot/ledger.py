"""SQLite-backed ledger for rate limits and per-site $ tracking.

Tables:
  requests   — one row per chat call, tokens + cost + ts
  daily_site — running per-site $ total (UTC day key)

All DB writes are short and synchronous; FastAPI calls them from threadpool via
asyncio.to_thread. Reads for rate-limit checks are also synchronous.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _data_dir() -> Path:
    p = Path(os.environ.get("CHATBOT_DATA_DIR", "./data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_day_key(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def hash_ip(ip: str) -> str:
    """One-way hash IPs for the ledger so the DB doesn't store raw addresses."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


@dataclass
class RateLimitState:
    allowed: bool
    reason: str = ""           # "ip_hour", "ip_day", "site_cap", "global_cap"
    retry_after_seconds: int = 0


class Ledger:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (_data_dir() / "ledger.sqlite")
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id     TEXT NOT NULL,
                    ip_hash     TEXT NOT NULL,
                    session_id  TEXT,
                    tokens_in   INTEGER NOT NULL,
                    tokens_out  INTEGER NOT NULL,
                    cost_usd    REAL NOT NULL,
                    model       TEXT,
                    ts          REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_requests_ip_ts ON requests(ip_hash, ts);
                CREATE INDEX IF NOT EXISTS idx_requests_site_ts ON requests(site_id, ts);

                CREATE TABLE IF NOT EXISTS daily_site (
                    day_key   TEXT NOT NULL,
                    site_id   TEXT NOT NULL,
                    cost_usd  REAL NOT NULL DEFAULT 0,
                    requests  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day_key, site_id)
                );

                -- Q&A log: every model-served reply auto-logs as 'pending'.
                -- Owner approves in the EmptyOS UI → status='curated' → future
                -- matching queries serve free + instant. Rejected entries are
                -- never offered. Promoted entries graduate to faqs.toml.
                CREATE TABLE IF NOT EXISTS qa_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id       TEXT NOT NULL,
                    query         TEXT NOT NULL,
                    reply         TEXT NOT NULL,
                    sources_json  TEXT NOT NULL DEFAULT '[]',
                    status        TEXT NOT NULL DEFAULT 'pending',
                    ts            REAL NOT NULL,
                    curated_at    REAL
                );
                CREATE INDEX IF NOT EXISTS idx_qa_site_status ON qa_log(site_id, status);
                CREATE INDEX IF NOT EXISTS idx_qa_ts ON qa_log(ts DESC);
                """
            )

    # ── Reads ────────────────────────────────────────────────────────

    def ip_count(self, ip_hash: str, since_ts: float) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM requests WHERE ip_hash = ? AND ts >= ?",
                (ip_hash, since_ts),
            ).fetchone()
            return int(row[0]) if row else 0

    def site_today_cost(self, site_id: str) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT cost_usd FROM daily_site WHERE day_key = ? AND site_id = ?",
                (_utc_day_key(), site_id),
            ).fetchone()
            return float(row[0]) if row else 0.0

    def global_today_cost(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_site WHERE day_key = ?",
                (_utc_day_key(),),
            ).fetchone()
            return float(row[0]) if row else 0.0

    # ── Rate-limit gate (read-only check, no mutation) ───────────────

    def check_limits(
        self,
        *,
        site_id: str,
        ip_hash: str,
        site_daily_cap: float,
        global_daily_cap: float,
        rate_per_hour: int,
        rate_per_day: int,
    ) -> RateLimitState:
        now = time.time()
        # Per-IP hourly
        hour_ago = now - 3600
        ip_hour = self.ip_count(ip_hash, hour_ago)
        if ip_hour >= rate_per_hour:
            return RateLimitState(False, "ip_hour", retry_after_seconds=3600)
        # Per-IP daily
        day_ago = now - 86400
        ip_day = self.ip_count(ip_hash, day_ago)
        if ip_day >= rate_per_day:
            return RateLimitState(False, "ip_day", retry_after_seconds=86400)
        # Per-site daily $
        site_cost = self.site_today_cost(site_id)
        if site_cost >= site_daily_cap:
            return RateLimitState(False, "site_cap", retry_after_seconds=_seconds_until_utc_midnight())
        # Global daily $
        if self.global_today_cost() >= global_daily_cap:
            return RateLimitState(False, "global_cap", retry_after_seconds=_seconds_until_utc_midnight())
        return RateLimitState(True)

    # ── Writes ───────────────────────────────────────────────────────

    def record(
        self,
        *,
        site_id: str,
        ip_hash: str,
        session_id: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        model: str,
    ) -> None:
        now = time.time()
        day = _utc_day_key(now)
        with self._conn() as c:
            c.execute(
                "INSERT INTO requests (site_id, ip_hash, session_id, tokens_in, tokens_out, cost_usd, model, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (site_id, ip_hash, session_id, tokens_in, tokens_out, cost_usd, model, now),
            )
            c.execute(
                "INSERT INTO daily_site (day_key, site_id, cost_usd, requests) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(day_key, site_id) DO UPDATE SET "
                "cost_usd = cost_usd + excluded.cost_usd, requests = requests + 1",
                (day, site_id, cost_usd),
            )


    # ── Q&A log ──────────────────────────────────────────────────────

    def log_qa_pending(
        self, *, site_id: str, query: str, reply: str, sources: list[dict]
    ) -> int:
        """Insert a pending Q&A row, return its id."""
        import json as _json
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO qa_log (site_id, query, reply, sources_json, status, ts) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (site_id, query, reply, _json.dumps(sources, ensure_ascii=False), now),
            )
            return int(cur.lastrowid)

    def list_qa(
        self, *, site_id: str, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        import json as _json
        sql = "SELECT id, site_id, query, reply, sources_json, status, ts, curated_at FROM qa_log WHERE site_id = ?"
        params: list = [site_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                sources = _json.loads(r[4]) if r[4] else []
            except Exception:
                sources = []
            out.append({
                "id": r[0], "site_id": r[1], "query": r[2], "reply": r[3],
                "sources": sources, "status": r[5], "ts": r[6],
                "curated_at": r[7],
            })
        return out

    def get_qa(self, qa_id: int) -> dict | None:
        rows = self._raw_get_qa(qa_id)
        return rows

    def _raw_get_qa(self, qa_id: int) -> dict | None:
        import json as _json
        with self._conn() as c:
            r = c.execute(
                "SELECT id, site_id, query, reply, sources_json, status, ts, curated_at "
                "FROM qa_log WHERE id = ?",
                (qa_id,),
            ).fetchone()
        if not r:
            return None
        try:
            sources = _json.loads(r[4]) if r[4] else []
        except Exception:
            sources = []
        return {
            "id": r[0], "site_id": r[1], "query": r[2], "reply": r[3],
            "sources": sources, "status": r[5], "ts": r[6], "curated_at": r[7],
        }

    def update_qa(
        self, qa_id: int, *,
        status: str | None = None,
        reply: str | None = None,
    ) -> bool:
        """Update status and/or reply on a qa_log row. Returns True if a row changed."""
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
            if status == "curated":
                sets.append("curated_at = ?")
                params.append(time.time())
        if reply is not None:
            sets.append("reply = ?")
            params.append(reply)
        if not sets:
            return False
        params.append(qa_id)
        with self._conn() as c:
            cur = c.execute(f"UPDATE qa_log SET {', '.join(sets)} WHERE id = ?", params)
            return cur.rowcount > 0

    def find_curated(self, site_id: str) -> list[dict]:
        """Return all curated Q&A rows for a site (used by the matcher)."""
        return self.list_qa(site_id=site_id, status="curated", limit=500)

    def sweep_old_pending(self, *, max_age_seconds: int = 30 * 86400) -> int:
        """Delete pending rows older than max_age_seconds. Returns rows removed."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM qa_log WHERE status = 'pending' AND ts < ?",
                (cutoff,),
            )
            return cur.rowcount


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(tz=timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight = midnight.replace(day=now.day) if now.hour == 0 and now.minute == 0 else midnight
    # Next midnight = today 00:00 + 1 day
    from datetime import timedelta
    return int((midnight + timedelta(days=1) - now).total_seconds())
