"""rooms `GET /api/agents` → `last_active` (history file mtime).

Portal's Recent sidebar sorts rooms by when they were last USED; the list only
carried `created`, so a room used yesterday but made in April sorted as Older.
The route now adds `last_active` from the room's history file — the same signal
`panel_recent_rooms` already sorted by. Calls the handler directly with a fake
`self` (no daemon), so it runs offline and under eos-mutation-verify.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

agents = importlib.import_module("apps.rooms.agents")


def _fake(tmp_path, records):
    adir = tmp_path / "agents"
    hdir = tmp_path / "history"
    adir.mkdir()
    hdir.mkdir()
    for r in records:
        (adir / f"{r['id']}.json").write_text(json.dumps(r), encoding="utf-8")
    self = SimpleNamespace(_agents_dir=lambda: adir, _history_dir=lambda: hdir)
    self._list_agents = lambda: agents._list_agents(self)
    self._history_path = lambda aid: agents._history_path(self, aid)
    return self, hdir


def _call(self, query=""):
    req = SimpleNamespace(query_params=dict(p.split("=") for p in query.split("&") if p))
    return asyncio.run(agents.api_list_agents(self, req))


def test_a_room_with_history_carries_its_history_time(tmp_path):
    self, hdir = _fake(tmp_path, [{"id": "used", "name": "Used", "created": "2026-04-01T00:00:00+00:00"}])
    h = hdir / "used.json"
    h.write_text("[]", encoding="utf-8")
    when = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc).timestamp()
    os.utime(h, (when, when))
    row = _call(self)[0]
    assert datetime.fromisoformat(row["last_active"]) == datetime.fromtimestamp(when, timezone.utc)


def test_a_room_without_history_has_no_activity_time(tmp_path):
    # Absent, not "now" and not `created`: the reader falls back to created itself.
    self, _ = _fake(tmp_path, [{"id": "fresh", "name": "Fresh", "created": "2026-10-01T00:00:00+00:00"}])
    assert "last_active" not in _call(self)[0]


def test_the_stored_record_is_not_mutated(tmp_path):
    self, hdir = _fake(tmp_path, [{"id": "r", "name": "R"}])
    (hdir / "r.json").write_text("[]", encoding="utf-8")
    _call(self)
    stored = json.loads((tmp_path / "agents" / "r.json").read_text(encoding="utf-8"))
    assert "last_active" not in stored


def test_status_filter_still_applies(tmp_path):
    self, _ = _fake(tmp_path, [{"id": "a", "status": "archived"}, {"id": "b"}])
    assert [r["id"] for r in _call(self)] == ["b"]
    assert [r["id"] for r in _call(self, "status=archived")] == ["a"]
