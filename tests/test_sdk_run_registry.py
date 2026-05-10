"""Unit tests for emptyos.sdk.run_registry.

Pure tmp_path — no daemon required.
"""

from __future__ import annotations

import time

import pytest

from emptyos.sdk.run_registry import RunHandle, RunRegistry


def test_new_creates_dir_with_minted_run_id(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new()
    assert h.dir.is_dir()
    assert h.run_id
    assert h.dir.name == h.run_id
    # default mint shape: timestamp-uuid6
    ts, _, suffix = h.run_id.partition("-")
    assert len(ts) == 15  # YYYYMMDDTHHMMSS
    assert "T" in ts
    assert len(suffix) == 6


def test_new_accepts_explicit_run_id(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new(run_id="custom-id-001")
    assert h.run_id == "custom-id-001"
    assert (tmp_path / "runs" / "custom-id-001").is_dir()


def test_state_round_trip(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new()
    h.write_state({"status": "running", "scenario": "foo"})
    assert h.read_state() == {"status": "running", "scenario": "foo"}


def test_read_state_returns_none_when_missing(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new()
    assert h.read_state() is None


def test_read_state_swallows_corrupt_json(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new()
    (h.dir / "run.json").write_text("not json {", encoding="utf-8")
    assert h.read_state() is None


def test_artifact_text_and_bytes(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new()
    h.write_artifact("notes.md", "hello")
    h.write_artifact("blob.bin", b"\x00\x01\x02")
    assert h.read_artifact("notes.md") == "hello"
    assert h.artifact("blob.bin").read_bytes() == b"\x00\x01\x02"
    assert h.read_artifact("missing.txt") is None


def test_get_returns_existing_handle(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    h = r.new(run_id="abc")
    h.write_state({"status": "ok"})
    again = r.get("abc")
    assert again is not None
    assert again.run_id == "abc"
    assert again.read_state() == {"status": "ok"}


def test_get_returns_none_for_unknown(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    assert r.get("never-existed") is None
    assert "never-existed" not in r


def test_contains(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    r.new(run_id="here")
    assert "here" in r
    assert "not-here" not in r


def test_recent_states_sorted_newest_first(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    r.new(run_id="20260510T100000-aaaaaa").write_state({"status": "ok", "n": 1})
    r.new(run_id="20260510T110000-bbbbbb").write_state({"status": "ok", "n": 2})
    r.new(run_id="20260510T120000-cccccc").write_state({"status": "error", "n": 3})
    rows = list(r.recent_states(10))
    assert [s["n"] for _, s in rows] == [3, 2, 1]


def test_recent_states_skips_runs_without_state(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    r.new(run_id="20260510T100000-aaaaaa")  # no state
    r.new(run_id="20260510T110000-bbbbbb").write_state({"status": "ok"})
    rows = list(r.recent_states(10))
    assert len(rows) == 1
    assert rows[0][0].run_id == "20260510T110000-bbbbbb"


def test_recent_states_status_filter(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    r.new(run_id="20260510T100000-aaaaaa").write_state({"status": "ok"})
    r.new(run_id="20260510T110000-bbbbbb").write_state({"status": "error"})
    r.new(run_id="20260510T120000-cccccc").write_state({"status": "ok"})
    rows = list(r.recent_states(10, status="ok"))
    assert [h.run_id for h, _ in rows] == [
        "20260510T120000-cccccc",
        "20260510T100000-aaaaaa",
    ]


def test_recent_states_limit(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    for i in range(5):
        r.new(run_id=f"20260510T10000{i}-xxxxxx").write_state({"i": i})
    assert len(list(r.recent_states(2))) == 2
    assert len(list(r.recent_states(None))) == 5


def test_custom_state_filename(tmp_path):
    r = RunRegistry(tmp_path / "runs", state_filename="meta.json")
    h = r.new()
    h.write_state({"k": "v"})
    assert (h.dir / "meta.json").exists()
    assert not (h.dir / "run.json").exists()
    rows = list(r.recent_states(10))
    assert rows[0][1] == {"k": "v"}


def test_minted_run_ids_are_unique(tmp_path):
    r = RunRegistry(tmp_path / "runs")
    ids = {r.new().run_id for _ in range(20)}
    assert len(ids) == 20


def test_two_registries_share_no_state(tmp_path):
    a = RunRegistry(tmp_path / "a")
    b = RunRegistry(tmp_path / "b")
    a.new(run_id="x").write_state({"who": "a"})
    assert b.get("x") is None
    assert "x" not in b
