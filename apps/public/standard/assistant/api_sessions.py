"""Assistant — REST endpoints for session CRUD + archive.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: list / create / get / update / delete / export sessions,
project-pin/unpin, bulk auto-archive. Thin wrappers over the SessionsMixin
helpers — these handlers are the HTTP shell, the mixin is the work.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._sessions_lock`` / ``self._create_session``
/ ``self._get_session`` / ``self._export_session`` / ``self._auto_archive``
(sessions.py mixin).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   api_list_sessions    = _api_sessions.api_list_sessions
#   api_create_session   = _api_sessions.api_create_session
#   api_get_session      = _api_sessions.api_get_session
#   get_session          = _api_sessions.get_session
#   list_recent_messages = _api_sessions.list_recent_messages
#   api_update_session   = _api_sessions.api_update_session
#   api_pin_project      = _api_sessions.api_pin_project
#   api_unpin_project    = _api_sessions.api_unpin_project
#   api_delete_session   = _api_sessions.api_delete_session
#   api_export_session   = _api_sessions.api_export_session
#   api_archive          = _api_sessions.api_archive
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


@web_route("GET", "/api/sessions")
async def api_list_sessions(self, request):
    rows = self.db.execute("""
        SELECT s.id, s.name, s.backend, s.created,
               COUNT(m.id) as message_count,
               MAX(m.ts) as last_message
        FROM sessions s LEFT JOIN messages m ON s.id = m.session_id
        GROUP BY s.id ORDER BY COALESCE(MAX(m.ts), s.created) DESC
    """).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "backend": r["backend"],
            "message_count": r["message_count"],
            "created": r["created"],
            "last_message": r["last_message"] or "",
        }
        for r in rows
    ]


@web_route("POST", "/api/sessions")
async def api_create_session(self, request):
    data = await request.json()
    async with self._sessions_lock:
        session = self._create_session(data.get("name", ""), data.get("backend", "auto"))
    return session


@web_route("GET", "/api/sessions/{sid}")
async def api_get_session(self, request):
    sid = request.path_params["sid"]
    session = self._get_session(sid)
    if not session:
        return {"error": "not found"}
    return session


async def get_session(self, sid: str = "", session_id: str = ""):
    """Cross-app callable (``call_app`` target) returning a session dict by id,
    or None. Public counterpart to the request-bound ``api_get_session`` — the
    private ``_get_session`` is not reachable across the export RPC boundary,
    so callers like ``runbook`` must go through this. Accepts ``sid`` or
    ``session_id`` for caller convenience."""
    key = (sid or session_id or "").strip()
    return self._get_session(key) if key else None


async def list_recent_messages(self, days: int = 30, limit: int = 2000):
    """Cross-app callable (``call_app`` target): flat list of chat messages
    from the last ``days`` days across every session, ordered so each
    session's turns stay adjacent (session, then insertion order). Read-only
    — consumers that mine Q&A history (kb-gap-miner) use this instead of
    parsing app.db directly. ``ts`` is isoformat text written by one code
    path, so the cutoff comparison is safely lexicographic."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 30
    try:
        limit = max(1, min(int(limit), 10000))
    except (TypeError, ValueError):
        limit = 2000
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = self.db.execute(
        "SELECT session_id, role, text, ts FROM messages "
        "WHERE ts >= ? ORDER BY session_id, id LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [
        {"session_id": r["session_id"], "role": r["role"], "text": r["text"], "ts": r["ts"]}
        for r in rows
    ]


@web_route("PUT", "/api/sessions/{sid}")
async def api_update_session(self, request):
    sid = request.path_params["sid"]
    data = await request.json()
    async with self._sessions_lock:
        row = self.db.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not row:
            return {"error": "not found"}
        _ALLOWED_COLS = {"name", "backend", "system_prompt", "project_id"}
        for key in _ALLOWED_COLS:
            if key in data:
                self.db.execute(
                    "UPDATE sessions SET " + key + " = ? WHERE id = ?", (data[key], sid)
                )
        self.db.commit()
    return self._get_session(sid)


@web_route("POST", "/api/sessions/{sid}/pin-project")
async def api_pin_project(self, request):
    """Bind a session to a project so its docs become persistent context."""
    sid = request.path_params["sid"]
    data = await request.json()
    project_id = (data.get("project_id") or "").strip()
    if not project_id:
        return {"error": "project_id required"}
    async with self._sessions_lock:
        row = self.db.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not row:
            return {"error": "session not found"}
        self.db.execute(
            "UPDATE sessions SET project_id = ? WHERE id = ?", (project_id, sid)
        )
        self.db.commit()
    return {"ok": True, "session_id": sid, "project_id": project_id}


@web_route("DELETE", "/api/sessions/{sid}/pin-project")
async def api_unpin_project(self, request):
    """Clear a session's project binding."""
    sid = request.path_params["sid"]
    async with self._sessions_lock:
        row = self.db.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not row:
            return {"error": "session not found"}
        self.db.execute("UPDATE sessions SET project_id = '' WHERE id = ?", (sid,))
        self.db.commit()
    return {"ok": True, "session_id": sid}


@web_route("DELETE", "/api/sessions/{sid}")
async def api_delete_session(self, request):
    sid = request.path_params["sid"]
    async with self._sessions_lock:
        self.db.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        self.db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        self.db.commit()
    return {"ok": True}


@web_route("POST", "/api/sessions/{sid}/export")
async def api_export_session(self, request):
    """Export session to vault as markdown."""
    sid = request.path_params["sid"]
    return await self._export_session(sid)


@web_route("POST", "/api/archive")
async def api_archive(self, request):
    """Archive old idle sessions to vault."""
    archived = await self._auto_archive()
    return {"archived": len(archived), "sessions": archived}
