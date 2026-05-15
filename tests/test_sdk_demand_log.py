"""Tests for demand_log + BaseApp hooks (search, vault_query, think with_confidence).

Pure in-process — no daemon required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from emptyos.sdk import BaseApp, demand_log


def _mk_app(tmp_path: Path, vault_index=None):
    config = MagicMock()
    config.notes_path = tmp_path / "vault"
    config.data_dir = tmp_path / "data"
    services = MagicMock()
    services.get_optional = MagicMock(
        side_effect=lambda name: vault_index if name == "vault_index" else None
    )
    kernel = SimpleNamespace(config=config, services=services, vault_map=MagicMock())
    manifest = SimpleNamespace(id="testapp")
    app = BaseApp.__new__(BaseApp)
    app.kernel = kernel
    app.manifest = manifest
    return app


# ---------- demand_log module ----------

def test_append_creates_file_and_writes_one_line(tmp_path):
    demand_log.append(tmp_path, {"kind": "search", "query": "foo"})
    entries = demand_log.read_all(tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "search"
    assert entries[0]["query"] == "foo"
    assert "ts" in entries[0]


def test_append_is_append_only(tmp_path):
    demand_log.append(tmp_path, {"kind": "search", "query": "a"})
    demand_log.append(tmp_path, {"kind": "vault_query", "query": "b"})
    entries = demand_log.read_all(tmp_path)
    assert len(entries) == 2
    assert entries[0]["query"] == "a"
    assert entries[1]["query"] == "b"


def test_append_never_raises_on_bad_dir(tmp_path):
    # Pass a nonsense data dir path — should swallow, not raise.
    demand_log.append(tmp_path / "nul" / "0", {"kind": "x", "query": "y"})


def test_read_all_skips_malformed_lines(tmp_path):
    path = tmp_path / demand_log.LOG_FILENAME
    path.write_text('{"ok": 1}\n{not json}\n{"ok": 2}\n', encoding="utf-8")
    entries = demand_log.read_all(tmp_path)
    assert [e["ok"] for e in entries] == [1, 2]


# ---------- BaseApp._record_demand ----------

def test_record_demand_writes_with_app_id(tmp_path):
    app = _mk_app(tmp_path)
    app._record_demand(kind="search", query="missing thing")
    entries = demand_log.read_all(tmp_path / "data")
    assert len(entries) == 1
    assert entries[0]["app"] == "testapp"
    assert entries[0]["kind"] == "search"
    assert entries[0]["result"] == "empty"


# ---------- search() hook ----------

def test_search_empty_result_logs_demand(tmp_path):
    app = _mk_app(tmp_path)
    cap = MagicMock()
    cap.execute = AsyncMock(return_value=SimpleNamespace(value=[]))
    app.kernel.capability = MagicMock(return_value=cap)

    asyncio.run(app.search("nothing matches"))
    entries = demand_log.read_all(tmp_path / "data")
    assert len(entries) == 1
    assert entries[0]["kind"] == "search"
    assert entries[0]["query"] == "nothing matches"


def test_search_with_results_does_not_log(tmp_path):
    app = _mk_app(tmp_path)
    cap = MagicMock()
    cap.execute = AsyncMock(return_value=SimpleNamespace(value=[{"hit": 1}]))
    app.kernel.capability = MagicMock(return_value=cap)

    asyncio.run(app.search("something"))
    assert demand_log.read_all(tmp_path / "data") == []


# ---------- vault_query() hook ----------

def test_vault_query_empty_logs_demand(tmp_path):
    vi = MagicMock()
    vi.find = MagicMock(return_value=[])
    app = _mk_app(tmp_path, vault_index=vi)

    rows = app.vault_query(tags=["job-application"], company="Atlassian")
    assert rows == []
    entries = demand_log.read_all(tmp_path / "data")
    assert len(entries) == 1
    assert entries[0]["kind"] == "vault_query"
    assert "job-application" in entries[0]["query"]


def test_vault_query_with_results_does_not_log(tmp_path):
    vi = MagicMock()
    vi.find = MagicMock(return_value=[{"path": "foo.md"}])
    app = _mk_app(tmp_path, vault_index=vi)

    app.vault_query(tags=["x"])
    assert demand_log.read_all(tmp_path / "data") == []


# ---------- _finalize_think ----------

def test_finalize_passthrough_when_off(tmp_path):
    app = _mk_app(tmp_path)
    out = app._finalize_think("plain string", with_confidence=False, prompt="", threshold=3.0)
    assert out == "plain string"


def test_finalize_parses_envelope_and_returns_dict(tmp_path):
    app = _mk_app(tmp_path)
    raw = '{"answer": "yes", "confidence": 5, "missing": [], "assumed": []}'
    out = app._finalize_think(raw, with_confidence=True, prompt="q", threshold=3.0)
    assert isinstance(out, dict)
    assert out["answer"] == "yes"
    assert out["confidence"] == 5.0
    # Confidence above threshold — no demand log entry.
    assert demand_log.read_all(tmp_path / "data") == []


def test_finalize_logs_low_confidence(tmp_path):
    app = _mk_app(tmp_path)
    raw = '{"answer": "maybe", "confidence": 2, "missing": ["term X"], "assumed": []}'
    out = app._finalize_think(raw, with_confidence=True, prompt="why?", threshold=3.0)
    assert out["confidence"] == 2.0
    entries = demand_log.read_all(tmp_path / "data")
    assert len(entries) == 1
    assert entries[0]["kind"] == "think"
    assert entries[0]["result"] == "low_confidence"
    assert entries[0]["confidence"] == 2.0
    assert entries[0]["missing"] == ["term X"]


def test_finalize_falls_back_when_unparseable(tmp_path):
    app = _mk_app(tmp_path)
    out = app._finalize_think(
        "totally not json", with_confidence=True, prompt="", threshold=3.0
    )
    assert out["answer"] == "totally not json"
    assert out["confidence"] is None
    # No confidence to compare — no log.
    assert demand_log.read_all(tmp_path / "data") == []
