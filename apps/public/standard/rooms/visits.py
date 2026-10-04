"""Rooms — read/unread tracking.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: per-room visit timestamps and unread-count aggregation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._list_agents`` (agents.py) to know which rooms exist.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────
#   _visits_path    = _visits._visits_path
#   _load_visits    = _visits._load_visits
#   _save_visits    = _visits._save_visits
#   mark_visited    = _visits.mark_visited
#   get_visits      = _visits.get_visits
#   get_unread      = _visits.get_unread
#   api_visit_room  = _visits.api_visit_room
#   api_visits      = _visits.api_visits
#   api_unread      = _visits.api_unread
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _visits_path(self) -> Path:
    return self.data_dir / "visits.json"


def _load_visits(self) -> dict:
    """{room_id → ISO timestamp of last visit}. Single-user system, so
    no per-user split — keeps the file small + the read trivial."""
    p = self._visits_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_visits(self, visits: dict) -> None:
    try:
        self._visits_path().write_text(
            json.dumps(visits, indent=2), encoding="utf-8",
        )
    except Exception:
        pass


def mark_visited(self, room_id: str) -> dict:
    """Stamp room_id with the current timestamp. Idempotent — fine to
    call from every openChat."""
    if not room_id:
        return {"error": "room_id required"}
    visits = self._load_visits()
    visits[room_id] = datetime.now(timezone.utc).isoformat()
    self._save_visits(visits)
    return {"ok": True, "room_id": room_id, "visited": visits[room_id]}


def get_visits(self) -> dict:
    """Return the full {room_id → last_visited_ts} map. Used by the
    sidebar to compute unread state in one fetch."""
    return self._load_visits()


def get_unread(self) -> dict:
    """Return {room_id: {count, last_ts}} for rooms with new messages
    since their last visit. Skips rooms whose latest message was from
    the user (they sent it — they know it's there).

    Single-pass scan of history JSONs; cheap because each file is
    capped at 200 messages.
    """
    visits = self._load_visits()
    out: dict = {}
    history_dir = self.data_dir / "history"
    if not history_dir.exists():
        return out
    for f in history_dir.glob("*.json"):
        room_id = f.stem
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        messages = data.get("messages", []) if isinstance(data, dict) else []
        if not isinstance(messages, list) or not messages:
            continue
        last_visited = visits.get(room_id, "")
        # Last message must be a non-user one and newer than last_visited.
        last = messages[-1]
        if last.get("role") == "user":
            continue
        last_ts = last.get("ts", "")
        if not last_ts or (last_visited and last_ts <= last_visited):
            continue
        # Count non-user messages newer than last_visited.
        count = 0
        for m in messages:
            if m.get("role") == "user":
                continue
            ts = m.get("ts", "")
            if not last_visited or ts > last_visited:
                count += 1
        if count > 0:
            out[room_id] = {"count": count, "last_ts": last_ts}
    return out


@web_route("POST", "/api/rooms/{room_id}/visit")
async def api_visit_room(self, request):
    return self.mark_visited(request.path_params["room_id"])


@web_route("GET", "/api/visits")
async def api_visits(self, request):
    return self.get_visits()


@web_route("GET", "/api/unread")
async def api_unread(self, request):
    return self.get_unread()
