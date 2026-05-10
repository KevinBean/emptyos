"""ChatSessionStore — SQLite session + message store for chat-shaped apps.

Used by the agent app. The assistant app has an older equivalent inline and
can migrate to this when convenient.

Two tables, prefix-configurable:
    {prefix}sessions  — id, name, system_prompt, created, status, <extra>
    {prefix}messages  — id, session_id, role, content_json, <extra>, ts

Content is JSON — both plain strings (flat chat) and content-block arrays
(tool-use agent turns) round-trip losslessly.

Usage:
    class AgentApp(BaseApp):
        async def setup(self):
            self.sessions = ChatSessionStore(
                self.db, prefix="agent_",
                session_extras={"provider": "TEXT NOT NULL DEFAULT ''"},
                message_extras={"provider_kind": "TEXT NOT NULL DEFAULT 'anthropic'"},
            )
            self.sessions.init_schema()
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


class ChatSessionStore:
    """SQLite-backed chat session + messages store.

    All operations commit immediately — this is a thin persistence wrapper,
    not a unit-of-work. Callers are responsible for serializing writes when
    needed (the agent app uses an asyncio.Lock around cross-turn mutations).
    """

    def __init__(
        self,
        db,
        prefix: str = "",
        session_extras: dict[str, str] | None = None,
        message_extras: dict[str, str] | None = None,
    ):
        """
        Args:
            db: an open sqlite3 connection (BaseApp.db).
            prefix: table-name prefix, e.g. "agent_" → "agent_sessions".
            session_extras: extra columns on the sessions table as
                {column_name: "TYPE constraints"} — e.g. {"provider": "TEXT NOT NULL DEFAULT ''"}
            message_extras: same for the messages table, e.g. {"provider_kind": "TEXT NOT NULL DEFAULT 'openai'"}
        """
        self.db = db
        self.prefix = prefix
        self.session_extras = dict(session_extras or {})
        self.message_extras = dict(message_extras or {})

    # ── Table names ──────────────────────────────────────────

    @property
    def sessions_table(self) -> str:
        return f"{self.prefix}sessions"

    @property
    def messages_table(self) -> str:
        return f"{self.prefix}messages"

    # ── Schema ───────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create tables if missing. Idempotent."""
        sess_cols = [
            "id TEXT PRIMARY KEY",
            "name TEXT NOT NULL DEFAULT 'New session'",
            "system_prompt TEXT NOT NULL DEFAULT ''",
            "created TEXT NOT NULL",
            "status TEXT NOT NULL DEFAULT 'active'",
        ]
        for col, decl in self.session_extras.items():
            sess_cols.append(f"{col} {decl}")

        msg_cols = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            f"session_id TEXT NOT NULL REFERENCES {self.sessions_table}(id) ON DELETE CASCADE",
            "role TEXT NOT NULL",
            "content_json TEXT NOT NULL",
        ]
        for col, decl in self.message_extras.items():
            msg_cols.append(f"{col} {decl}")
        msg_cols.append("ts TEXT NOT NULL")

        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS {self.sessions_table} (
                {", ".join(sess_cols)}
            );
            CREATE TABLE IF NOT EXISTS {self.messages_table} (
                {", ".join(msg_cols)}
            );
            CREATE INDEX IF NOT EXISTS idx_{self.messages_table}_session
                ON {self.messages_table}(session_id);
        """)
        self.db.commit()

    # ── Sessions CRUD ────────────────────────────────────────

    def create_session(
        self, *, name: str = "", extras: dict | None = None, id_len: int = 10
    ) -> dict:
        sid = uuid.uuid4().hex[:id_len]
        created = datetime.now(UTC).isoformat()
        extras = extras or {}

        cols = ["id", "name", "system_prompt", "created", "status"]
        vals = [sid, name or "New session", "", created, "active"]
        for col in self.session_extras:
            cols.append(col)
            vals.append(extras.get(col, ""))

        placeholders = ",".join("?" * len(cols))
        self.db.execute(
            f"INSERT INTO {self.sessions_table} ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals),
        )
        self.db.commit()

        out = {
            "id": sid,
            "name": name or "New session",
            "system_prompt": "",
            "created": created,
            "status": "active",
            "messages": [],
        }
        for col in self.session_extras:
            out[col] = extras.get(col, "")
        return out

    def get_session(self, sid: str) -> dict | None:
        row = self.db.execute(
            f"SELECT * FROM {self.sessions_table} WHERE id = ?",
            (sid,),
        ).fetchone()
        if not row:
            return None
        msgs = self.load_messages(sid)
        out = dict(row) if hasattr(row, "keys") else {"id": row[0]}
        out["messages"] = msgs
        return out

    def list_sessions(self) -> list[dict]:
        """Return sessions ordered by most recent activity, with message counts."""
        rows = self.db.execute(f"""
            SELECT s.*,
                   COUNT(m.id) as message_count,
                   MAX(m.ts) as last_message
            FROM {self.sessions_table} s
            LEFT JOIN {self.messages_table} m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY MAX(m.ts) DESC, s.created DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def fork_session(self, sid: str, at_message: int | None = None, name: str = "") -> dict | None:
        """Clone a session into a new one, optionally truncated at a message index.

        at_message: 0-based index of the last message to include (None = all).
        Returns the new session dict, or None if source not found.
        """
        source = self.get_session(sid)
        if not source:
            return None
        msgs = source.get("messages", [])
        if at_message is not None:
            msgs = msgs[: at_message + 1]

        fork_name = name or f"Fork of {source.get('name', sid)}"
        extras = {col: source.get(col, "") for col in self.session_extras}
        new_session = self.create_session(name=fork_name, extras=extras)
        new_sid = new_session["id"]

        # Re-insert messages preserving role + content + extras
        for m in msgs:
            role = m.get("role", "")
            content = m.get("content", "")
            msg_extras = {col: m.get(col, "") for col in self.message_extras}
            self.append_message(new_sid, role, content, extras=msg_extras)

        return self.get_session(new_sid)

    def delete_session(self, sid: str) -> None:
        self.db.execute(f"DELETE FROM {self.sessions_table} WHERE id = ?", (sid,))
        self.db.commit()

    def update_session(self, sid: str, **fields) -> None:
        """Update any subset of: name, system_prompt, status, or an extras column."""
        allowed = {"name", "system_prompt", "status"} | set(self.session_extras.keys())
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return
        set_clause = ", ".join(f"{k} = ?" for k in clean)
        self.db.execute(
            f"UPDATE {self.sessions_table} SET {set_clause} WHERE id = ?",
            tuple(clean.values()) + (sid,),
        )
        self.db.commit()

    # ── Messages ─────────────────────────────────────────────

    def append_message(self, sid: str, role: str, content: Any, extras: dict | None = None) -> None:
        """Persist a message. `content` can be a str or any JSON-serializable block array."""
        ts = datetime.now(UTC).isoformat()
        content_json = json.dumps(content)
        extras = extras or {}

        cols = ["session_id", "role", "content_json"]
        vals = [sid, role, content_json]
        for col in self.message_extras:
            cols.append(col)
            vals.append(extras.get(col, ""))
        cols.append("ts")
        vals.append(ts)

        placeholders = ",".join("?" * len(cols))
        self.db.execute(
            f"INSERT INTO {self.messages_table} ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals),
        )
        self.db.commit()

    def load_messages(self, sid: str) -> list[dict]:
        rows = self.db.execute(
            f"SELECT role, content_json, ts FROM {self.messages_table} "
            f"WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                content = json.loads(r["content_json"])
            except (json.JSONDecodeError, TypeError):
                content = r["content_json"]
            out.append({"role": r["role"], "content": content, "ts": r["ts"]})
        return out

    def load_provider_messages(self, sid: str) -> list[dict]:
        """Same as load_messages but drops `ts` — shape the agent loop consumes.

        If `content_json` stored a full-message dict (new format, see the agent's
        `_append_message`), splat it in alongside the role so provider-specific
        fields like `tool_calls` (assistant) and `tool_call_id` (tool) survive
        reload. Dropping those fields on OpenAI breaks tool-using turns on the
        next user message with 'messages with role tool must be a response...'.
        """
        out: list[dict] = []
        for m in self.load_messages(sid):
            content = m["content"]
            # New format: `content_json` is the full message minus role (may
            # contain `content`, `tool_calls`, `tool_call_id`). Legacy format:
            # `content_json` is just the raw content (string or list of blocks).
            # Anthropic content is always a list, never a plain dict — so any
            # dict with these keys is definitely the new full-message format.
            if isinstance(content, dict) and (
                "content" in content or "tool_calls" in content or "tool_call_id" in content
            ):
                out.append({"role": m["role"], **content})
            else:
                out.append({"role": m["role"], "content": content})
        return out
