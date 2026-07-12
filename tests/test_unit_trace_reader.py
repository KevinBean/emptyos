"""Unit tests for emptyos/sdk/trace_reader.py — pure stitch + thin read IO.

The "Record" half of the `replay` app: stitch one trace_id's think:executed
events (events.db) + tool-audit.jsonl lines into an ordered WorkItem timeline.
`stitch` is pure (dicts in, dicts out); the read_* helpers touch a temp db/file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from emptyos.sdk.trace_reader import (
    read_trace_audit,
    read_trace_events,
    stitch,
    work_trace,
)


def test_stitch_orders_by_ts_audit_before_event():
    audit = [{"tool": "Read", "input": {"path": "a"}, "ok": True, "ts": "2026-01-01T00:00:02Z", "duration_ms": 5}]
    events = [
        {"type": "think:executed", "ts": "2026-01-01T00:00:01Z", "data": {"app": "agent", "domain": "code", "prompt_len": 40}},
        {"type": "think:executed", "ts": "2026-01-01T00:00:02Z", "data": {"app": "kb", "domain": "text"}},
    ]
    items = stitch(events, audit)
    assert [i.seq for i in items] == [1, 2, 3]
    # ts 01 think first, then at ts 02 the audit (tool) sorts before the event
    assert items[0].kind == "think" and items[0].name == "agent:code"
    assert items[1].kind == "tool" and items[1].name == "Read"
    assert items[2].kind == "think" and items[2].name == "kb:text"
    assert items[1].duration_ms == 5 and items[1].ok is True


def test_stitch_skips_malformed_rows():
    items = stitch([None, {"data": "notadict"}, 7], [None, "x", {"tool": "Bash"}])
    # only the one valid audit row survives
    assert len(items) == 1
    assert items[0].kind == "tool" and items[0].name == "Bash"
    assert items[0].input == {} and items[0].ok is None


def test_stitch_empty():
    assert stitch([], []) == []
    assert stitch(None, None) == []


def test_read_helpers_filter_by_trace_id(tmp_path: Path):
    data_root = tmp_path
    # events.db
    db = sqlite3.connect(str(data_root / "events.db"))
    db.execute("CREATE TABLE events (id TEXT PRIMARY KEY, type TEXT NOT NULL, data TEXT, source TEXT, timestamp TEXT NOT NULL)")
    db.execute("CREATE INDEX idx_events_type ON events(type)")
    rows = [
        ("1", "think:executed", json.dumps({"app": "agent", "trace_id": "trace-keep"}), "kernel", "2026-01-01T00:00:01Z"),
        ("2", "think:executed", json.dumps({"app": "kb", "trace_id": "trace-other"}), "kernel", "2026-01-01T00:00:02Z"),
        ("3", "agent:started", json.dumps({"trace_id": "trace-keep"}), "agent", "2026-01-01T00:00:00Z"),
    ]
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?)", rows)
    db.commit()
    db.close()
    # tool-audit.jsonl
    audit_dir = data_root / "apps" / "agent"
    audit_dir.mkdir(parents=True)
    (audit_dir / "tool-audit.jsonl").write_text(
        json.dumps({"tool": "Read", "trace_id": "trace-keep", "ts": "2026-01-01T00:00:03Z", "ok": True}) + "\n"
        + json.dumps({"tool": "Bash", "trace_id": "trace-other", "ts": "2026-01-01T00:00:04Z"}) + "\n"
        + "{ not json\n",
        encoding="utf-8",
    )

    evs = read_trace_events(data_root, "trace-keep")
    assert len(evs) == 1 and evs[0]["data"]["app"] == "agent"  # only think:executed type queried by default

    aud = read_trace_audit(data_root, "trace-keep")
    assert len(aud) == 1 and aud[0]["tool"] == "Read"

    wt = work_trace(data_root, "trace-keep")
    assert [w["kind"] for w in wt] == ["think", "tool"]
    assert wt[0]["name"] == "agent" and wt[1]["name"] == "Read"


def test_read_helpers_missing_inputs(tmp_path: Path):
    assert read_trace_events(tmp_path, "") == []
    assert read_trace_events(tmp_path, "x") == []  # no events.db
    assert read_trace_audit(tmp_path, "x") == []  # no jsonl
    assert work_trace(tmp_path, "x") == []
