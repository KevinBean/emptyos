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
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

#: Bump to make every store rebuild its search index on the next ``init_schema``
#: (after a change to what ``index_text`` extracts). 2: bodies whitespace-collapsed.
SEARCH_INDEX_VERSION = "2"

#: A trigram FTS5 index needs at least this many characters to use the index;
#: shorter queries fall back to a substring scan.
_TRIGRAM_MIN = 3


def flatten_content(content: Any) -> str:
    """Stored message content → plain text, whatever encoding it is in.

    Three encodings share one table (see the agent's ``_persist_message``): a
    plain string, a block list (Anthropic: text / tool_use / tool_result), and
    the full-message dict ``{content, tool_calls?, tool_call_id?}``. Only text
    is kept — tool calls and tool results are machinery, not conversation.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return flatten_content(content.get("content"))
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(p for p in parts if p)
    return ""


def index_text(role: str, content: Any, extras: dict | None = None) -> str:
    """What a message contributes to the search index — ``""`` for nothing.

    User and assistant text only. A user message whose typed text was stored
    beside prepended context (``display_text``) is indexed by what the user
    typed, so a search does not match the injected orient/memory block;
    loop nudges (``origin == "system"``) are not the conversation at all.
    """
    extras = extras or {}
    if role not in ("user", "assistant"):
        return ""
    if extras.get("origin") == "system":
        return ""
    if role == "user" and extras.get("display_text"):
        return str(extras["display_text"])
    return flatten_content(content).strip()


def _flat(text: Any) -> str:
    """Whitespace collapsed to single spaces — applied to the query AND to what
    is indexed, so "line second" finds "first line\\nsecond line"."""
    return " ".join(str(text or "").split())


def _like_pattern(q: str) -> str:
    """``%q%`` with LIKE's own metacharacters escaped (``ESCAPE '\\'``)."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _snippet(text: str, query: str, width: int = 80) -> str:
    """A window of ``text`` around the first case-insensitive match of ``query``."""
    flat = " ".join(text.split())
    at = flat.casefold().find(query.casefold())
    if at < 0:
        return flat[: width * 2]
    start = max(0, at - width)
    end = min(len(flat), at + len(query) + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


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
        projects: bool = False,
        search: bool = False,
        artifacts: bool = False,
    ):
        """
        Args:
            db: an open sqlite3 connection (BaseApp.db).
            prefix: table-name prefix, e.g. "agent_" → "agent_sessions".
            session_extras: extra columns on the sessions table as
                {column_name: "TYPE constraints"} — e.g. {"provider": "TEXT NOT NULL DEFAULT ''"}
            message_extras: same for the messages table, e.g. {"provider_kind": "TEXT NOT NULL DEFAULT 'openai'"}
            projects: add a ``{prefix}projects`` table (name + instructions).
                Sessions point at one through a ``project_id`` session extra.
            search: keep a full-text index of user/assistant text
                (``{prefix}search``, FTS5 trigram — so CJK substrings match,
                which a LIKE over ``content_json`` cannot: ``json.dumps``
                escapes them to ``\\uXXXX``). Falls back to a scan when this
                SQLite has no FTS5.
            artifacts: add a ``{prefix}artifacts`` table recording which
                artifacts a session produced (id + title + shape, newest
                first). The artifact itself lives in the app that stores it;
                this is only the session→artifact link, which is what lets a
                reopened chat put its side panel back. Unique per
                (session, artifact) — revising an artifact updates the row
                rather than adding one.
        """
        self.db = db
        self.prefix = prefix
        self.session_extras = dict(session_extras or {})
        self.message_extras = dict(message_extras or {})
        self.projects = projects
        self.search_enabled = search
        self.artifacts = artifacts
        self.fts = False  # set by init_schema when the FTS5 table exists

    # ── Table names ──────────────────────────────────────────

    @property
    def sessions_table(self) -> str:
        return f"{self.prefix}sessions"

    @property
    def messages_table(self) -> str:
        return f"{self.prefix}messages"

    @property
    def projects_table(self) -> str:
        return f"{self.prefix}projects"

    @property
    def artifacts_table(self) -> str:
        return f"{self.prefix}artifacts"

    @property
    def search_table(self) -> str:
        return f"{self.prefix}search"

    @property
    def meta_table(self) -> str:
        return f"{self.prefix}meta"

    # ── Schema ───────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create tables if missing and backfill newly declared extra columns."""
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
        self._ensure_extra_columns(self.sessions_table, self.session_extras)
        self._ensure_extra_columns(self.messages_table, self.message_extras)
        if self.projects:
            self.db.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.projects_table} (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    instructions TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL,
                    updated TEXT NOT NULL
                )
            """)
        if self.artifacts:
            self.db.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.artifacts_table} (
                    session_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    shape TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    PRIMARY KEY (session_id, artifact_id)
                )
            """)
        if self.search_enabled:
            self._init_search()
        self.db.commit()

    def _init_search(self) -> None:
        self.db.execute(
            f"CREATE TABLE IF NOT EXISTS {self.meta_table} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        try:
            self.db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.search_table} USING fts5("
                f"body, session_id UNINDEXED, message_id UNINDEXED, role UNINDEXED, "
                f"tokenize='trigram')"
            )
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # no FTS5 / no trigram tokenizer: search scans instead
            return
        row = self.db.execute(
            f"SELECT value FROM {self.meta_table} WHERE key = 'search_version'"
        ).fetchone()
        if not row or row[0] != SEARCH_INDEX_VERSION:
            self.rebuild_search_index()

    def rebuild_search_index(self) -> int:
        """Re-index every message (first boot with search on, or a version bump)."""
        if not self.fts:
            return 0
        self.db.execute(f"DELETE FROM {self.search_table}")
        extra_cols = [c for c in ("display_text", "origin") if c in self.message_extras]
        cols = ", ".join(["id", "session_id", "role", "content_json", *extra_cols])
        n = 0
        for r in self.db.execute(f"SELECT {cols} FROM {self.messages_table}").fetchall():
            n += self._index_row(r["id"], r["session_id"], r["role"], r["content_json"],
                                 {c: r[c] for c in extra_cols})
        self.db.execute(
            f"INSERT OR REPLACE INTO {self.meta_table} (key, value) VALUES ('search_version', ?)",
            (SEARCH_INDEX_VERSION,),
        )
        self.db.commit()
        return n

    def _index_row(self, message_id, sid, role, content_json, extras) -> int:
        try:
            content = json.loads(content_json)
        except (json.JSONDecodeError, TypeError):
            content = content_json
        body = _flat(index_text(role, content, extras))
        if not body:
            return 0
        self.db.execute(
            f"INSERT INTO {self.search_table} (body, session_id, message_id, role) VALUES (?, ?, ?, ?)",
            (body, sid, message_id, role),
        )
        return 1

    def _ensure_extra_columns(self, table: str, extras: dict[str, str]) -> None:
        """ALTER TABLE-add any declared extra columns missing from an existing DB."""
        if not extras:
            return
        rows = self.db.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {
            (row["name"] if hasattr(row, "keys") else row[1])
            for row in rows
        }
        for col, decl in extras.items():
            if col in existing:
                continue
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

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
        # Messages explicitly: the FK's ON DELETE CASCADE only runs with
        # PRAGMA foreign_keys=ON, which BaseApp.db never sets — so a deleted
        # session's messages used to stay behind as orphans.
        self.db.execute(f"DELETE FROM {self.messages_table} WHERE session_id = ?", (sid,))
        if self.fts:
            self.db.execute(f"DELETE FROM {self.search_table} WHERE session_id = ?", (sid,))
        if self.artifacts:
            # The artifacts themselves belong to the app that stores them and
            # outlive the chat; only the link goes.
            self.db.execute(f"DELETE FROM {self.artifacts_table} WHERE session_id = ?", (sid,))
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
        cur = self.db.execute(
            f"INSERT INTO {self.messages_table} ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals),
        )
        if self.fts:
            self._index_row(cur.lastrowid, sid, role, content_json, extras)
        self.db.commit()

    def load_messages(self, sid: str) -> list[dict]:
        """Messages with their declared extra columns (non-empty ones only).

        The extras are returned because two readers need them: ``fork_session``
        copies each message's extras (it read them from here and, before this,
        always got blanks — so a fork lost every ``provider_kind``), and a
        caller may store display metadata beside provider content.
        """
        extra_cols = list(self.message_extras)
        cols = ", ".join(["role", "content_json", "ts", *extra_cols])
        rows = self.db.execute(
            f"SELECT {cols} FROM {self.messages_table} WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                content = json.loads(r["content_json"])
            except (json.JSONDecodeError, TypeError):
                content = r["content_json"]
            msg = {"role": r["role"], "content": content, "ts": r["ts"]}
            for col in extra_cols:
                value = r[col]
                if value not in (None, ""):
                    msg[col] = value
            out.append(msg)
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

    # ── Artifacts (opt-in: artifacts=True) ───────────────────

    def _require_artifacts(self) -> None:
        if not self.artifacts:
            raise RuntimeError("this ChatSessionStore was created without artifacts=True")

    def record_artifact(self, sid: str, artifact_id: str, title: str = "", shape: str = "") -> dict:
        """Note that a session produced (or revised) an artifact.

        Idempotent per (session, artifact): a revision moves the row's
        ``updated`` and refreshes the title, so the panel orders by when an
        artifact was last touched rather than when it first appeared.
        """
        self._require_artifacts()
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            f"""INSERT INTO {self.artifacts_table}
                    (session_id, artifact_id, title, shape, created, updated)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, artifact_id) DO UPDATE SET
                    title = excluded.title, shape = excluded.shape, updated = excluded.updated""",
            (sid, artifact_id, title, shape, now, now),
        )
        self.db.commit()
        return {"session_id": sid, "artifact_id": artifact_id, "title": title,
                "shape": shape, "updated": now}

    def list_artifacts(self, sid: str) -> list[dict]:
        """This session's artifacts, most recently touched first."""
        self._require_artifacts()
        rows = self.db.execute(
            f"SELECT * FROM {self.artifacts_table} WHERE session_id = ? ORDER BY updated DESC",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Projects (opt-in: projects=True) ─────────────────────

    def _require_projects(self) -> None:
        if not self.projects:
            raise RuntimeError("this ChatSessionStore was created without projects=True")

    def create_project(self, name: str, instructions: str = "") -> dict:
        self._require_projects()
        pid = "prj-" + uuid.uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            f"INSERT INTO {self.projects_table} (id, name, instructions, created, updated) VALUES (?, ?, ?, ?, ?)",
            (pid, name, instructions, now, now),
        )
        self.db.commit()
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict | None:
        self._require_projects()
        row = self.db.execute(f"SELECT * FROM {self.projects_table} WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        """Projects, most recently active first, each with its session count."""
        self._require_projects()
        if "project_id" not in self.session_extras:
            rows = self.db.execute(f"SELECT *, 0 AS session_count FROM {self.projects_table} ORDER BY updated DESC").fetchall()
            return [dict(r) for r in rows]
        rows = self.db.execute(f"""
            SELECT p.*,
                   (SELECT COUNT(*) FROM {self.sessions_table} s WHERE s.project_id = p.id) AS session_count,
                   (SELECT MAX(m.ts) FROM {self.messages_table} m
                      JOIN {self.sessions_table} s ON s.id = m.session_id
                     WHERE s.project_id = p.id) AS last_active
            FROM {self.projects_table} p
            ORDER BY COALESCE(last_active, p.updated) DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def update_project(self, pid: str, **fields) -> dict | None:
        self._require_projects()
        clean = {k: v for k, v in fields.items() if k in ("name", "instructions")}
        if clean:
            clean["updated"] = datetime.now(UTC).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in clean)
            self.db.execute(
                f"UPDATE {self.projects_table} SET {set_clause} WHERE id = ?",
                (*clean.values(), pid),
            )
            self.db.commit()
        return self.get_project(pid)

    def delete_project(self, pid: str) -> None:
        """Delete a project; its sessions stay, detached (project_id → '')."""
        self._require_projects()
        if "project_id" in self.session_extras:
            self.db.execute(
                f"UPDATE {self.sessions_table} SET project_id = '' WHERE project_id = ?", (pid,)
            )
        self.db.execute(f"DELETE FROM {self.projects_table} WHERE id = ?", (pid,))
        self.db.commit()

    # ── Search (opt-in: search=True) ─────────────────────────

    def search(self, query: str, *, limit: int = 30, where: dict | None = None) -> list[dict]:
        """Sessions whose name or conversation text contains ``query``.

        One row per session — ``{session_id, name, snippet, role, message_id,
        ts, hits}`` — ordered by the session's latest activity, newest first;
        the snippet is from the session's newest matching message. ``where``
        narrows by session columns, e.g. ``{"project_id": "prj-…"}`` or
        ``{"profile": "chat"}`` (declared columns only). The filter and the
        per-session grouping run in SQL, so ``limit`` counts sessions and a
        busy session elsewhere can never crowd a match out. Substring
        semantics throughout: a query of 3+ chars uses the trigram index,
        shorter ones scan the same index with LIKE, and a store without FTS5
        scans the flattened messages. Whitespace is collapsed on both sides.
        """
        q = _flat(query)
        if not q:
            return []
        where = {k: v for k, v in (where or {}).items() if k in self.session_extras}
        hits = {h["session_id"]: h for h in self._text_matches(q, where, limit)}

        clause, args = self._where_sql(where, "")
        for r in self.db.execute(
            f"SELECT id FROM {self.sessions_table} WHERE name LIKE ? ESCAPE '\\'{clause} LIMIT ?",
            (_like_pattern(q), *args, limit),
        ).fetchall():
            hits.setdefault(r["id"], {"session_id": r["id"], "message_id": None, "role": "", "snippet": "", "hits": 0})

        if not hits:
            return []
        meta = {
            r["id"]: r
            for r in self.db.execute(
                f"SELECT s.id, s.name, s.created, MAX(m.ts) AS last_ts FROM {self.sessions_table} s "
                f"LEFT JOIN {self.messages_table} m ON m.session_id = s.id "
                f"WHERE s.id IN ({','.join('?' * len(hits))}) GROUP BY s.id",
                tuple(hits),
            ).fetchall()
        }
        out = []
        for sid, h in hits.items():
            m = meta.get(sid)
            if not m:
                continue  # an index row for a session that no longer exists
            out.append({**h, "name": m["name"], "ts": m["last_ts"] or m["created"]})
        out.sort(key=lambda h: h["ts"] or "", reverse=True)
        return out[:limit]

    @staticmethod
    def _where_sql(where: dict, alias: str) -> tuple[str, tuple]:
        """`` AND <alias>col = ?…`` for a ``where`` whose keys are already
        restricted to declared session columns (never caller-chosen SQL)."""
        clause = "".join(f" AND {alias}{k} = ?" for k in where)
        return clause, tuple(where.values())

    def _text_matches(self, q: str, where: dict, limit: int) -> list[dict]:
        """One hit per matching session (newest match first, at most ``limit``):
        ``{session_id, message_id, role, snippet, hits}``."""
        clause, wargs = self._where_sql(where, "s.")
        if self.fts:
            if len(q) >= _TRIGRAM_MIN:
                cond = f"x.rowid IN (SELECT rowid FROM {self.search_table} WHERE {self.search_table} MATCH ?)"
                cargs = ('"' + q.replace('"', '""') + '"',)
            else:
                cond, cargs = "x.body LIKE ? ESCAPE '\\'", (_like_pattern(q),)
            # SQLite returns a bare column from the row holding the single MAX()
            # aggregate, so body/role are the newest matching message's.
            rows = self.db.execute(
                f"SELECT x.session_id, COUNT(*) AS hits, MAX(CAST(x.message_id AS INTEGER)) AS mid, "
                f"x.role AS role, x.body AS body "
                f"FROM {self.search_table} x JOIN {self.sessions_table} s ON s.id = x.session_id "
                f"WHERE {cond}{clause} GROUP BY x.session_id ORDER BY mid DESC LIMIT ?",
                (*cargs, *wargs, limit),
            ).fetchall()
            return [{"session_id": r["session_id"], "message_id": r["mid"], "role": r["role"],
                     "snippet": _snippet(r["body"], q), "hits": r["hits"]} for r in rows]
        # No FTS5: scan the flattened messages of the sessions ``where`` allows.
        needle = q.casefold()
        extra_cols = [c for c in ("display_text", "origin") if c in self.message_extras]
        cols = ", ".join(["m.id", "m.session_id", "m.role", "m.content_json", *(f"m.{c}" for c in extra_cols)])
        hits: dict[str, dict] = {}
        for r in self.db.execute(
            f"SELECT {cols} FROM {self.messages_table} m JOIN {self.sessions_table} s ON s.id = m.session_id "
            f"WHERE 1=1{clause} ORDER BY m.id DESC",
            wargs,
        ).fetchall():
            try:
                content = json.loads(r["content_json"])
            except (json.JSONDecodeError, TypeError):
                content = r["content_json"]
            body = _flat(index_text(r["role"], content, {c: r[c] for c in extra_cols}))
            if not body or needle not in body.casefold():
                continue
            h = hits.get(r["session_id"])
            if h:
                h["hits"] += 1
            elif len(hits) < limit:
                hits[r["session_id"]] = {"session_id": r["session_id"], "message_id": r["id"], "role": r["role"],
                                         "snippet": _snippet(body, q), "hits": 1}
        return list(hits.values())
