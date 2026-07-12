"""Episodic memory store — `emptyos/sdk/episodic.py`.

Pure JSONL store + compaction-archive reader. No daemon, no kernel — the module
must work over a plain tmp dir. Covers write→read round-trip, newest-first order,
limit capping, malformed-line tolerance, fail-soft on bad paths, and the
compaction-archive reader (incl. the filename-sanitization match with
agent_loop._archive_compacted).

Run: python -m pytest tests/test_sdk_episodic.py -v
"""
from __future__ import annotations

import json

import pytest

from emptyos.sdk.episodic import (
    EpisodicStore,
    episode_record,
    read_compaction_archive,
)

pytestmark = pytest.mark.unit


def test_write_then_read_roundtrip(tmp_path):
    store = EpisodicStore(tmp_path)
    ok = store.write_episode(
        episode_record(session_id="s1", task="fix the boards bug", outcome="merged", app="agent")
    )
    assert ok is True
    eps = store.read_episodes()
    assert len(eps) == 1
    assert eps[0]["task"] == "fix the boards bug"
    assert eps[0]["session_id"] == "s1"
    # recency keys mirror ts so the record drops into BaseApp.recall
    assert eps[0]["created"] == eps[0]["ts"] == eps[0]["updated"]


def test_read_is_newest_first(tmp_path):
    store = EpisodicStore(tmp_path)
    store.write_episode(episode_record(session_id="a", task="first", outcome="", ts="2026-01-01T00:00:00+00:00"))
    store.write_episode(episode_record(session_id="b", task="second", outcome="", ts="2026-02-01T00:00:00+00:00"))
    eps = store.read_episodes()
    assert [e["task"] for e in eps] == ["second", "first"]


def test_limit_caps_to_newest(tmp_path):
    store = EpisodicStore(tmp_path)
    for i in range(5):
        store.write_episode(episode_record(session_id=f"s{i}", task=f"t{i}", outcome=""))
    eps = store.read_episodes(limit=2)
    assert len(eps) == 2
    assert eps[0]["task"] == "t4"  # newest
    assert eps[1]["task"] == "t3"


def test_malformed_line_tolerated(tmp_path):
    store = EpisodicStore(tmp_path)
    store.write_episode(episode_record(session_id="ok", task="good", outcome=""))
    # inject a truncated / non-JSON line (crash mid-write)
    with open(store.path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
        f.write("\n")  # blank line
    eps = store.read_episodes()
    assert len(eps) == 1
    assert eps[0]["task"] == "good"


def test_read_missing_file_is_empty(tmp_path):
    store = EpisodicStore(tmp_path / "does-not-exist-yet")
    assert store.read_episodes() == []


def test_episode_record_normalizes_and_caps():
    rec = episode_record(
        session_id="s",
        task="x" * 5000,
        outcome="done",
        decisions=[f"d{i}" for i in range(50)],
        salience=3.5,
    )
    assert len(rec["task"]) == 2000  # capped
    assert len(rec["decisions"]) == 20  # capped
    assert rec["salience"] == 3.5
    assert rec["ts"] and rec["created"] == rec["ts"]


def test_write_episode_backfills_timestamps(tmp_path):
    store = EpisodicStore(tmp_path)
    # a raw dict lacking ts/created/updated
    assert store.write_episode({"session_id": "raw", "task": "no stamps"}) is True
    ep = store.read_episodes()[0]
    assert ep["ts"] and ep["created"] and ep["updated"]


def test_read_compaction_archive_roundtrip(tmp_path):
    # Simulate what agent_loop._archive_compacted writes.
    arch_dir = tmp_path / "compaction-archive"
    arch_dir.mkdir(parents=True)
    rec = {"ts": "2026-01-01T00:00:00+00:00", "session_id": "sess-1", "items": [{"role": "tool", "content": "big grep"}]}
    with open(arch_dir / "sess-1.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    got = read_compaction_archive(tmp_path, "sess-1")
    assert len(got) == 1
    assert got[0]["items"][0]["content"] == "big grep"


def test_read_compaction_archive_sanitizes_session_id(tmp_path):
    # A session id with unsafe chars maps to the sanitized filename.
    arch_dir = tmp_path / "compaction-archive"
    arch_dir.mkdir(parents=True)
    # "a/b:c" -> "a_b_c" per the _safe_id rule
    with open(arch_dir / "a_b_c.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "t", "session_id": "a/b:c", "items": []}) + "\n")
    got = read_compaction_archive(tmp_path, "a/b:c")
    assert len(got) == 1


def test_read_compaction_archive_missing_is_empty(tmp_path):
    assert read_compaction_archive(tmp_path, "nope") == []


# ── BaseApp integration (remember_episode / recall_episodes) ──────────────────
# A minimal fake kernel gives BaseApp a data_dir; no network — embeddings are
# off, so recall falls back to recency+salience (still ranks + dedupes).

from pathlib import Path  # noqa: E402

from emptyos.kernel.app_loader import AppManifest  # noqa: E402
from emptyos.sdk.base_app import BaseApp  # noqa: E402


class _Cfg:
    def __init__(self, d):
        self.data_dir = Path(d)


class _Kernel:
    class _Services:
        def get_optional(self, _):
            return None

    services = _Services()

    def __init__(self, d):
        self.config = _Cfg(d)


class _MemApp(BaseApp):
    pass


def _make_app(tmp_path):
    manifest = AppManifest(
        id="episodictest", name="e", version="0", description="", path=Path(".")
    )
    return _MemApp(_Kernel(tmp_path), manifest)


@pytest.mark.asyncio
async def test_baseapp_remember_recall_roundtrip(tmp_path):
    app = _make_app(tmp_path)
    assert app.remember_episode("s1", task="fix the boards drag bug", outcome="merged") is True
    app.remember_episode("s2", task="write the podcast pipeline", outcome="shipped")
    hits = await app.recall_episodes("boards bug", top_k=5)
    assert hits  # recency fallback returns candidates with scores
    assert any("boards" in (h.get("task") or "") for h in hits)


@pytest.mark.asyncio
async def test_baseapp_scope_isolates_actors(tmp_path):
    app = _make_app(tmp_path)
    app.remember_episode("shiftA1", task="agent A work", outcome="x", scope="agent-A")
    app.remember_episode("shiftB1", task="agent B work", outcome="y", scope="agent-B")
    a = await app.recall_episodes("anything", scope="agent-A")
    assert len(a) == 1 and a[0]["task"] == "agent A work"
    # the unscoped (app-wide) log is empty — scoped writes don't leak into it
    assert await app.recall_episodes("anything") == []


@pytest.mark.asyncio
async def test_baseapp_recall_dedupes_newest_per_session(tmp_path):
    app = _make_app(tmp_path)
    # same session, two turns — recall must collapse to the newest
    app.remember_episode("s1", task="opening task", outcome="turn 1")
    app.remember_episode("s1", task="opening task", outcome="turn 2 latest")
    hits = await app.recall_episodes("task", top_k=5)
    s1 = [h for h in hits if h.get("session_id") == "s1"]
    assert len(s1) == 1
    assert s1[0]["outcome"] == "turn 2 latest"

