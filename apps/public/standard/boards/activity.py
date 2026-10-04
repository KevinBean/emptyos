"""Boards — durable per-item activity log (append-only JSONL).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the append-only activity store at
``data/apps/boards/activity/{board_id}.jsonl``, written at every item
mutation choke point (create/update/archive, plus comment/attachment events
logged by their own modules), and the merged activity read behind
``GET /api/boards/{id}/items/{file}/activity`` (durable entries first,
legacy event-bus reconstruction appended for pre-existing installs).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .shared import safe_segment

if TYPE_CHECKING:
    from .app import BoardsApp  # noqa: F401 — for type hints only


# ─── Bind to BoardsApp class as ──────────────────────────────────────
#   _activity_dir      = _activity._activity_dir
#   _actor_name        = _activity._actor_name
#   _log_activity      = _activity._log_activity
#   _read_activity     = _activity._read_activity
#   api_item_activity  = _activity.api_item_activity
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# Cap how much JSONL we read back per request — the file is append-only and
# unbounded; the tail is what a detail pane wants.
_TAIL_LINES = 2000

# Board ids are slugs already, but the id arrives from a path param — treat
# as hostile.
def _safe_board_id(board_id: str) -> str:
    return safe_segment(board_id, "board")


def _activity_dir(self):
    return self.data_subdir("activity")


def _actor_name(self) -> str:
    """The display name stamped on activity entries + comments."""
    return str(self.setting("boards.me", "") or "me")


def _log_activity(self, board_id: str, file: str, type: str, actor: str = "", **payload):
    """Append one activity entry. Never raises — logging must not break writes."""
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "file": file,
            "type": type,
            "actor": actor or self._actor_name(),
        }
        entry.update(payload)
        path = self._activity_dir() / f"{_safe_board_id(board_id)}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        self.log(f"activity log write failed: {e}", level="warning")


def _read_activity(self, board_id: str, file: str, limit: int = 30) -> list[dict]:
    """Newest-first durable entries for one item (tail-capped read)."""
    path = self._activity_dir() / f"{_safe_board_id(board_id)}.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-_TAIL_LINES:]
    except Exception:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("file") != file:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


@web_route("GET", "/api/boards/{id}/items/{file}/activity")
async def api_item_activity(self, request):
    """Activity for one item: durable JSONL entries merged with the legacy
    event-bus reconstruction (kept so pre-existing installs don't lose the
    history they had before the durable log landed)."""
    board_id = request.path_params.get("id", "")
    filename = request.path_params.get("file", "")
    limit = int(request.query_params.get("limit", 30))

    durable = self._read_activity(board_id, filename, limit=limit)
    out = [
        {
            "type": e.get("type", ""),
            "timestamp": e.get("ts"),
            "actor": e.get("actor", ""),
            "updates": e.get("updates") or {},
            "data": {k: v for k, v in e.items() if k not in ("ts", "file", "type", "actor")},
            "durable": True,
        }
        for e in durable
    ]
    seen = {(str(e["timestamp"] or "")[:19], e["type"]) for e in out}

    # Legacy reconstruction from the ephemeral event bus (lossy, pre-existing).
    try:
        events = await self.kernel.events.history(limit=500)
    except Exception:
        events = []
    for e in events:
        if len(out) >= limit:
            break
        data = e.get("data") or {}
        etype = e.get("type", "")
        if not etype.startswith("board:"):
            continue
        if data.get("board") != board_id:
            continue
        if data.get("file") not in (filename, None):
            continue
        key = (str(e.get("timestamp") or "")[:19], etype)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "type": etype,
                "timestamp": e.get("timestamp"),
                "updates": data.get("updates") or {},
                "data": data,
            }
        )

    out.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return {"events": out[:limit]}
