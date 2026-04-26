"""Unit tests for emptyos.sdk.TimeSeriesCounter.

Pure in-process SQLite — no daemon required. (conftest's server_health
fixture will skip these if the daemon is down, which is the project
convention; they pass regardless when the daemon is up.)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from emptyos.sdk import TimeSeriesCounter


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode=WAL")
    yield c
    c.close()


def _dt(s: str) -> datetime:
    # "2026-04-15" or "2026-04-15T14"
    if "T" in s:
        return datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def test_bump_increments_same_bucket(conn):
    c = TimeSeriesCounter(conn, "t1", dims=["site", "path"])
    t = _dt("2026-04-15")
    c.bump({"site": "blog", "path": "/"}, at=t)
    c.bump({"site": "blog", "path": "/"}, at=t)
    c.bump({"site": "blog", "path": "/"}, at=t, by=3)
    assert c.total() == 5
    rows = c.range()
    assert len(rows) == 1 and rows[0]["count"] == 5


def test_distinct_buckets_per_day(conn):
    c = TimeSeriesCounter(conn, "t2", dims=["site"])
    c.bump({"site": "blog"}, at=_dt("2026-04-14"))
    c.bump({"site": "blog"}, at=_dt("2026-04-15"))
    c.bump({"site": "blog"}, at=_dt("2026-04-15"))
    rows = c.range(group_by="bucket")
    assert rows == [
        {"key": "2026-04-14", "count": 1},
        {"key": "2026-04-15", "count": 2},
    ]


def test_hour_granularity(conn):
    c = TimeSeriesCounter(conn, "t3", dims=[], granularity="hour")
    c.bump(at=_dt("2026-04-15T09"))
    c.bump(at=_dt("2026-04-15T09"))
    c.bump(at=_dt("2026-04-15T10"))
    assert c.total() == 3
    buckets = {r["bucket"] for r in c.range()}
    assert buckets == {"2026-04-15T09", "2026-04-15T10"}


def test_range_window_filters(conn):
    c = TimeSeriesCounter(conn, "t4", dims=["site"])
    for d in ("2026-04-10", "2026-04-12", "2026-04-15", "2026-04-20"):
        c.bump({"site": "s"}, at=_dt(d))
    assert c.total(start="2026-04-12", end="2026-04-15") == 2
    assert len(c.range(start="2026-04-12", end="2026-04-15")) == 2


def test_top_by_dim(conn):
    c = TimeSeriesCounter(conn, "t5", dims=["path"])
    t = _dt("2026-04-15")
    for _ in range(5):
        c.bump({"path": "/hello"}, at=t)
    for _ in range(2):
        c.bump({"path": "/about"}, at=t)
    c.bump({"path": "/contact"}, at=t)
    top = c.top("path", limit=2)
    assert top == [
        {"key": "/hello", "count": 5},
        {"key": "/about", "count": 2},
    ]


def test_where_filter(conn):
    c = TimeSeriesCounter(conn, "t6", dims=["site", "path"])
    t = _dt("2026-04-15")
    c.bump({"site": "blog", "path": "/"}, at=t)
    c.bump({"site": "blog", "path": "/hi"}, at=t)
    c.bump({"site": "garden", "path": "/"}, at=t)
    assert c.total(where={"site": "blog"}) == 2
    assert c.total(where={"site": "garden"}) == 1
    assert c.top("path", where={"site": "blog"})[0]["count"] == 1


def test_trim_old_buckets(conn):
    c = TimeSeriesCounter(conn, "t7", dims=["site"])
    for d in ("2025-01-01", "2025-12-31", "2026-04-15"):
        c.bump({"site": "s"}, at=_dt(d))
    removed = c.trim(before="2026-01-01")
    assert removed == 2
    assert c.total() == 1


def test_unknown_dim_raises(conn):
    c = TimeSeriesCounter(conn, "t8", dims=["site"])
    with pytest.raises(ValueError):
        c.bump({"nope": "x"})
    with pytest.raises(ValueError):
        c.top("nope")
    with pytest.raises(ValueError):
        c.range(where={"nope": "x"})


def test_invalid_granularity_raises(conn):
    with pytest.raises(ValueError):
        TimeSeriesCounter(conn, "t9", granularity="week")


def test_unsafe_identifiers_rejected(conn):
    with pytest.raises(ValueError):
        TimeSeriesCounter(conn, "drop table users;--")
    with pytest.raises(ValueError):
        TimeSeriesCounter(conn, "ok", dims=["bad col"])


def test_table_persistence_across_instances(conn):
    c1 = TimeSeriesCounter(conn, "t10", dims=["site"])
    c1.bump({"site": "blog"}, at=_dt("2026-04-15"))
    # A second instance pointed at the same conn/name sees the same data
    c2 = TimeSeriesCounter(conn, "t10", dims=["site"])
    assert c2.total() == 1


def test_zero_increment_is_noop(conn):
    c = TimeSeriesCounter(conn, "t11", dims=["site"])
    c.bump({"site": "s"}, by=0)
    assert c.total() == 0
    assert c.range() == []


def test_dims_default_empty_string(conn):
    c = TimeSeriesCounter(conn, "t12", dims=["site", "path"])
    t = _dt("2026-04-15")
    c.bump({"site": "blog"}, at=t)  # path omitted → ""
    c.bump({"site": "blog", "path": ""}, at=t)
    assert c.total() == 2  # same row
    rows = c.range()
    assert len(rows) == 1 and rows[0]["path"] == ""
