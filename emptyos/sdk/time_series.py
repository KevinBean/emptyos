"""TimeSeriesCounter — bucketed SQLite counter primitive.

Aggregates events into time buckets (day or hour) with arbitrary string
dimensions. One row per (bucket, *dims); increments are upserts via
INSERT ... ON CONFLICT. Caller supplies the sqlite3 connection, so the
counter can live alongside an app's other tables in the same DB file.

Extracted from the hand-rolled pattern in apps/billing (daily_stats /
provider_stats / app_stats). Reused in apps/web-analytics.

    # construction creates/ensures the table
    views = TimeSeriesCounter(self.db, "pageviews",
                              dims=["site", "path"], granularity="day")

    views.bump({"site": "blog", "path": "/hello"})
    views.range("2026-04-01", "2026-04-30")
    views.top("path", "2026-04-01", "2026-04-30", limit=10)
    views.total(start="2026-04-01")
    views.trim(before="2025-04-15")
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable


_VALID_GRANULARITY = ("day", "hour")


def today_utc() -> str:
    """Today's date as a UTC 'YYYY-MM-DD' string — the query-side companion to bump()."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_ago_utc(n: int) -> str:
    """UTC date string for *n* days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _bucket(at: datetime | None, granularity: str) -> str:
    at = at or datetime.now(timezone.utc)
    if granularity == "day":
        return at.strftime("%Y-%m-%d")
    return at.strftime("%Y-%m-%dT%H")


def _safe_ident(s: str) -> str:
    if not s or not s.replace("_", "").isalnum():
        raise ValueError(f"invalid identifier: {s!r}")
    return s


class TimeSeriesCounter:

    def __init__(
        self,
        conn: sqlite3.Connection,
        name: str,
        dims: Iterable[str] | None = None,
        granularity: str = "day",
    ):
        if granularity not in _VALID_GRANULARITY:
            raise ValueError(f"granularity must be one of {_VALID_GRANULARITY}")
        self.conn = conn
        self.name = _safe_ident(name)
        self.dims = tuple(_safe_ident(d) for d in (dims or ()))
        self.granularity = granularity
        self._ensure_table()

    def _ensure_table(self) -> None:
        cols = ["bucket TEXT NOT NULL"]
        cols.extend(f"{d} TEXT NOT NULL DEFAULT ''" for d in self.dims)
        cols.append("count INTEGER NOT NULL DEFAULT 0")
        pk_cols = ["bucket", *self.dims]
        cols.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS {self.name} ({', '.join(cols)})")
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.name}_bucket ON {self.name}(bucket)"
        )
        self.conn.commit()

    def _dim_values(self, dims: dict | None) -> tuple[str, ...]:
        dims = dims or {}
        unknown = set(dims) - set(self.dims)
        if unknown:
            raise ValueError(f"unknown dims for {self.name}: {sorted(unknown)}")
        return tuple(str(dims.get(d, "")) for d in self.dims)

    def bump(
        self,
        dims: dict | None = None,
        by: int = 1,
        at: datetime | None = None,
    ) -> None:
        """Increment the counter for (current bucket, dims) by `by`."""
        if by == 0:
            return
        bucket = _bucket(at, self.granularity)
        values = self._dim_values(dims)
        all_cols = ("bucket", *self.dims, "count")
        placeholders = ",".join("?" * len(all_cols))
        pk_cols = ("bucket", *self.dims)
        self.conn.execute(
            f"INSERT INTO {self.name} ({','.join(all_cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({','.join(pk_cols)}) DO UPDATE SET count = count + excluded.count",
            (bucket, *values, by),
        )
        self.conn.commit()

    def _where_clause(
        self,
        start: str | None,
        end: str | None,
        where: dict | None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        args: list = []
        if start is not None:
            clauses.append("bucket >= ?")
            args.append(start)
        if end is not None:
            clauses.append("bucket <= ?")
            args.append(end)
        for k, v in (where or {}).items():
            if k not in self.dims:
                raise ValueError(f"unknown dim: {k}")
            clauses.append(f"{k} = ?")
            args.append(str(v))
        return (" WHERE " + " AND ".join(clauses) if clauses else "", args)

    def range(
        self,
        start: str | None = None,
        end: str | None = None,
        where: dict | None = None,
        group_by: str | None = None,
    ) -> list[dict]:
        """Rows in [start, end] optionally grouped by a dim (or 'bucket')."""
        where_sql, args = self._where_clause(start, end, where)
        if group_by:
            if group_by != "bucket" and group_by not in self.dims:
                raise ValueError(f"unknown group_by: {group_by}")
            sql = (
                f"SELECT {group_by} AS key, SUM(count) AS count FROM {self.name}"
                f"{where_sql} GROUP BY {group_by} ORDER BY {group_by}"
            )
            return [{"key": r[0], "count": r[1]} for r in self.conn.execute(sql, args)]
        cols = ["bucket", *self.dims, "count"]
        sql = f"SELECT {','.join(cols)} FROM {self.name}{where_sql} ORDER BY bucket"
        return [dict(zip(cols, r)) for r in self.conn.execute(sql, args)]

    def top(
        self,
        field: str,
        start: str | None = None,
        end: str | None = None,
        where: dict | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Top N values of `field` by summed count in the window."""
        if field not in self.dims:
            raise ValueError(f"unknown field: {field}")
        where_sql, args = self._where_clause(start, end, where)
        sql = (
            f"SELECT {field} AS key, SUM(count) AS count FROM {self.name}"
            f"{where_sql} GROUP BY {field} ORDER BY count DESC LIMIT ?"
        )
        args.append(limit)
        return [{"key": r[0], "count": r[1]} for r in self.conn.execute(sql, args)]

    def total(
        self,
        start: str | None = None,
        end: str | None = None,
        where: dict | None = None,
    ) -> int:
        where_sql, args = self._where_clause(start, end, where)
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(count), 0) FROM {self.name}{where_sql}", args
        ).fetchone()
        return int(row[0] or 0)

    def trim(self, before: str) -> int:
        """Delete buckets strictly before the given bucket string. Returns rows deleted."""
        cur = self.conn.execute(f"DELETE FROM {self.name} WHERE bucket < ?", (before,))
        self.conn.commit()
        return cur.rowcount or 0
