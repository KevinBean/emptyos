"""PostgreSQL and in-memory commons repositories.

Identity/session methods follow the englishos-control-plane pattern verbatim
(app-gate RLS via `SET LOCAL commons.app='on'`, user-scoping in the SQL). The
content methods (notes + ACL) are new; both backends enforce the SAME visibility
semantics as emptyos_commons.visibility — the in-memory repo by delegating to it,
Postgres by mirroring it in SQL with an RLS backstop.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from . import visibility as vis
from .models import (
    AclGrant,
    CommonsNote,
    InstanceRecord,
    SessionPrincipal,
    UserRecord,
    VerifiedIdentity,
)

_NOTE_COLS = (
    "id, owner_id, slug, title, body, visibility, kind, domain, tags, author, "
    "to_char(created, 'YYYY-MM-DD\"T\"HH24:MI:SSZ'), "
    "to_char(updated, 'YYYY-MM-DD\"T\"HH24:MI:SSZ')"
)

# Fields a write-grantee (or owner) may edit via update_note / PATCH. The slug,
# owner, visibility, and timestamps are NOT editable through the write path.
EDITABLE_NOTE_FIELDS = ("title", "body", "tags")


class CommonsRepository(Protocol):
    # --- identity + sessions (lifted) ---
    def healthcheck(self) -> None: ...
    def user_by_subject(self, oidc_subject: str) -> UserRecord | None: ...
    def user_by_email(self, email: str) -> UserRecord | None: ...
    def user_by_id(self, user_id: str) -> UserRecord | None: ...
    def ensure_user(self, identity: VerifiedIdentity) -> UserRecord: ...
    def instance_for_user(self, user_id: str) -> InstanceRecord | None: ...
    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> None: ...
    def principal_for_session(self, token_hash: str, now: datetime) -> SessionPrincipal | None: ...
    def revoke_session(self, token_hash: str) -> None: ...
    def audit(self, user_id: str | None, event_type: str, data: dict) -> None: ...

    # --- in-service password auth (optional; unused under a token IdP) ---
    def set_password(self, user_id: str, password_hash: str) -> None: ...
    def password_hash_for(self, user_id: str) -> str | None: ...

    # --- machine API tokens (durable daemon auth; never expire, revocable) ---
    def create_api_token(self, user_id: str, token_hash: str, label: str) -> str: ...
    def user_for_api_token(self, token_hash: str) -> UserRecord | None: ...
    def list_api_tokens(self, user_id: str) -> list[dict]: ...
    def revoke_api_token(self, user_id: str, token_id: str) -> bool: ...

    # --- content + ACL (new) ---
    def publish_note(self, owner_id: str, note: dict, visibility: str) -> CommonsNote: ...
    def update_note(self, note_id: str, fields: dict) -> CommonsNote | None: ...
    def get_note(self, note_id: str) -> CommonsNote | None: ...
    def list_visible_notes(self, user_id: str) -> list[CommonsNote]: ...
    def delete_note(self, note_id: str) -> None: ...
    def set_visibility(self, note_id: str, visibility: str) -> None: ...
    def share_note(
        self, note_id: str, principal_user_id: str, level: str = "read"
    ) -> None: ...
    def unshare_note(self, note_id: str, principal_user_id: str) -> None: ...
    def acl_for_note(self, note_id: str) -> list[AclGrant]: ...


# --------------------------------------------------------------------------- #
# Postgres
# --------------------------------------------------------------------------- #
class PostgresCommonsRepository:
    def __init__(self, database_url: str):
        if not database_url:
            raise RuntimeError("COMMONS_DATABASE_URL is required")
        self.database_url = database_url

    @contextmanager
    def _connection(self, *, as_user: str | None = None):
        """Open a transaction with the app-gate RLS flag set. When *as_user* is
        given, also set commons.current_user so the restrictive per-user READ
        policy (migration 0002) enforces visibility at the DB layer — used by the
        content read path as defense-in-depth behind the app's WHERE clause."""
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                'Postgres backend needs psycopg: pip install -e "services/emptyos-commons[postgres]"'
            ) from exc
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL commons.app = 'on'")
                if as_user:
                    connection.execute("SET LOCAL commons.current_user = %s", (as_user,))
                yield connection

    @staticmethod
    def _user(row) -> UserRecord:
        return UserRecord(
            id=str(row[0]), oidc_subject=row[1], email=row[2] or "", handle=row[3] or ""
        )

    @staticmethod
    def _note(row) -> CommonsNote:
        return CommonsNote(
            id=str(row[0]),
            owner_id=str(row[1]),
            slug=row[2],
            title=row[3] or "",
            body=row[4] or "",
            visibility=row[5],
            kind=row[6] or "concept",
            domain=row[7] or "",
            tags=tuple(row[8] or ()),
            author=row[9] or "user",
            created=row[10] or "",
            updated=row[11] or "",
        )

    def healthcheck(self) -> None:
        with self._connection() as c:
            c.execute("SELECT 1").fetchone()

    def user_by_subject(self, oidc_subject: str) -> UserRecord | None:
        with self._connection() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE oidc_subject = %s",
                (oidc_subject,),
            ).fetchone()
        return self._user(row) if row else None

    def user_by_email(self, email: str) -> UserRecord | None:
        if not email:
            return None
        with self._connection() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE lower(email) = lower(%s)",
                (email,),
            ).fetchone()
        return self._user(row) if row else None

    def user_by_id(self, user_id: str) -> UserRecord | None:
        if not user_id:
            return None
        with self._connection() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE id = %s",
                (user_id,),
            ).fetchone()
        return self._user(row) if row else None

    def ensure_user(self, identity: VerifiedIdentity) -> UserRecord:
        with self._connection() as c:
            row = c.execute(
                """
                INSERT INTO commons_users (oidc_subject, email)
                VALUES (%s, %s)
                ON CONFLICT (oidc_subject) DO UPDATE
                SET email = EXCLUDED.email, updated_at = now()
                RETURNING id, oidc_subject, email, handle
                """,
                (identity.subject, identity.email),
            ).fetchone()
        return self._user(row)

    def instance_for_user(self, user_id: str) -> InstanceRecord | None:
        with self._connection() as c:
            row = c.execute(
                """
                SELECT id, user_id, routing_key, status, upstream_url, inner_token_env
                FROM commons_instances WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        if not row or not row[0]:
            return None
        return InstanceRecord(
            id=str(row[0]), user_id=str(row[1]), routing_key=row[2],
            status=row[3], upstream_url=row[4] or "", inner_token_env=row[5] or "",
        )

    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> None:
        with self._connection() as c:
            c.execute(
                "INSERT INTO commons_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, expires_at),
            )

    def principal_for_session(self, token_hash: str, now: datetime) -> SessionPrincipal | None:
        with self._connection() as c:
            row = c.execute(
                """
                SELECT u.id, u.oidc_subject, u.email, u.handle,
                       i.id, i.user_id, i.routing_key, i.status, i.upstream_url, i.inner_token_env
                FROM commons_sessions s
                JOIN commons_users u ON u.id = s.user_id
                LEFT JOIN commons_instances i ON i.user_id = u.id
                WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s
                """,
                (token_hash, now),
            ).fetchone()
        if not row:
            return None
        user = self._user(row[:4])
        inst = None
        if row[4]:
            inst = InstanceRecord(
                id=str(row[4]), user_id=str(row[5]), routing_key=row[6],
                status=row[7], upstream_url=row[8] or "", inner_token_env=row[9] or "",
            )
        return SessionPrincipal(user=user, instance=inst)

    def revoke_session(self, token_hash: str) -> None:
        with self._connection() as c:
            c.execute(
                "UPDATE commons_sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash,),
            )

    def audit(self, user_id: str | None, event_type: str, data: dict) -> None:
        with self._connection() as c:
            c.execute(
                "INSERT INTO commons_audit_events (user_id, event_type, data) VALUES (%s, %s, %s::jsonb)",
                (user_id, event_type, json.dumps(data, sort_keys=True)),
            )

    def set_password(self, user_id: str, password_hash: str) -> None:
        with self._connection() as c:
            c.execute(
                "UPDATE commons_users SET password_hash=%s, updated_at=now() WHERE id=%s",
                (password_hash, user_id),
            )

    def password_hash_for(self, user_id: str) -> str | None:
        with self._connection() as c:
            row = c.execute(
                "SELECT password_hash FROM commons_users WHERE id=%s", (user_id,)
            ).fetchone()
        return row[0] if row and row[0] else None

    def create_api_token(self, user_id: str, token_hash: str, label: str) -> str:
        with self._connection() as c:
            row = c.execute(
                "INSERT INTO commons_api_tokens (token_hash, user_id, label)"
                " VALUES (%s, %s, %s) RETURNING id",
                (token_hash, user_id, label),
            ).fetchone()
        return str(row[0])

    def user_for_api_token(self, token_hash: str) -> UserRecord | None:
        with self._connection() as c:
            row = c.execute(
                "SELECT u.id, u.oidc_subject, u.email, u.handle"
                " FROM commons_api_tokens t JOIN commons_users u ON u.id = t.user_id"
                " WHERE t.token_hash=%s AND t.revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
        return self._user(row) if row else None

    def list_api_tokens(self, user_id: str) -> list[dict]:
        with self._connection() as c:
            rows = c.execute(
                "SELECT id, label, to_char(created_at,'YYYY-MM-DD\"T\"HH24:MI:SSZ'),"
                " (revoked_at IS NOT NULL) FROM commons_api_tokens"
                " WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [{"id": str(r[0]), "label": r[1] or "", "created": r[2] or "", "revoked": bool(r[3])} for r in rows]

    def revoke_api_token(self, user_id: str, token_id: str) -> bool:
        with self._connection() as c:
            row = c.execute(
                "UPDATE commons_api_tokens SET revoked_at=now()"
                " WHERE id=%s AND user_id=%s AND revoked_at IS NULL RETURNING id",
                (token_id, user_id),
            ).fetchone()
        return bool(row)

    # --- content ---
    def publish_note(self, owner_id: str, note: dict, visibility: str) -> CommonsNote:
        with self._connection() as c:
            row = c.execute(
                f"""
                INSERT INTO commons_notes
                    (owner_id, slug, title, body, visibility, kind, domain, tags, author, updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (owner_id, slug) DO UPDATE
                SET title = EXCLUDED.title, body = EXCLUDED.body,
                    visibility = EXCLUDED.visibility, kind = EXCLUDED.kind,
                    domain = EXCLUDED.domain, tags = EXCLUDED.tags,
                    author = EXCLUDED.author, updated = now()
                RETURNING {_NOTE_COLS}
                """,
                (
                    owner_id, note.get("slug"), note.get("title", ""), note.get("body", ""),
                    visibility, note.get("kind", "concept"), note.get("domain", ""),
                    list(note.get("tags", ())), note.get("author", "user"),
                ),
            ).fetchone()
        return self._note(row)

    def update_note(self, note_id: str, fields: dict) -> CommonsNote | None:
        sets, params = [], []
        for col in EDITABLE_NOTE_FIELDS:
            if col in fields:
                sets.append(f"{col} = %s")
                params.append(list(fields[col]) if col == "tags" else fields[col])
        if not sets:
            return self.get_note(note_id)
        params.append(note_id)
        with self._connection() as c:
            row = c.execute(
                f"UPDATE commons_notes SET {', '.join(sets)}, updated = now()"
                f" WHERE id = %s RETURNING {_NOTE_COLS}",
                params,
            ).fetchone()
        return self._note(row) if row else None

    def get_note(self, note_id: str) -> CommonsNote | None:
        with self._connection() as c:
            row = c.execute(
                f"SELECT {_NOTE_COLS} FROM commons_notes WHERE id = %s", (note_id,)
            ).fetchone()
        return self._note(row) if row else None

    def list_visible_notes(self, user_id: str) -> list[CommonsNote]:
        with self._connection(as_user=user_id) as c:
            rows = c.execute(
                f"""
                SELECT {_NOTE_COLS} FROM commons_notes n
                WHERE n.owner_id = %s
                   OR n.visibility = 'public'
                   OR (n.visibility = 'shared' AND EXISTS (
                        SELECT 1 FROM commons_acl a
                        WHERE a.note_id = n.id AND a.principal_user_id = %s
                          AND a.level IN ('read', 'write', 'comment')))
                ORDER BY n.updated DESC NULLS LAST, n.id DESC
                """,
                (user_id, user_id),
            ).fetchall()
        return [self._note(r) for r in rows]

    def delete_note(self, note_id: str) -> None:
        with self._connection() as c:
            c.execute("DELETE FROM commons_notes WHERE id = %s", (note_id,))

    def set_visibility(self, note_id: str, visibility: str) -> None:
        with self._connection() as c:
            c.execute(
                "UPDATE commons_notes SET visibility = %s, updated = now() WHERE id = %s",
                (visibility, note_id),
            )

    def share_note(
        self, note_id: str, principal_user_id: str, level: str = "read"
    ) -> None:
        with self._connection() as c:
            c.execute(
                """
                INSERT INTO commons_acl (note_id, principal_user_id, level)
                VALUES (%s, %s, %s)
                ON CONFLICT (note_id, principal_user_id) DO UPDATE SET level = EXCLUDED.level
                """,
                (note_id, principal_user_id, level),
            )

    def unshare_note(self, note_id: str, principal_user_id: str) -> None:
        with self._connection() as c:
            c.execute(
                "DELETE FROM commons_acl WHERE note_id = %s AND principal_user_id = %s",
                (note_id, principal_user_id),
            )

    def acl_for_note(self, note_id: str) -> list[AclGrant]:
        with self._connection() as c:
            rows = c.execute(
                "SELECT note_id, principal_user_id, level FROM commons_acl WHERE note_id = %s",
                (note_id,),
            ).fetchall()
        return [AclGrant(note_id=str(r[0]), principal_user_id=str(r[1]), level=r[2]) for r in rows]


# --------------------------------------------------------------------------- #
# In-memory (tests + local dev)
# --------------------------------------------------------------------------- #
class InMemoryCommonsRepository:
    """Deterministic repository for unit tests. Content visibility delegates to
    emptyos_commons.visibility so the test oracle and the service agree."""

    def __init__(self):
        self.users: dict[str, UserRecord] = {}
        self.users_by_subject: dict[str, str] = {}
        self.instances: dict[str, InstanceRecord] = {}
        self.sessions: dict[str, tuple[str, datetime, bool]] = {}
        self.audit_events: list[tuple[str | None, str, dict]] = []
        self.passwords: dict[str, str] = {}
        self.api_tokens: dict[str, dict] = {}  # id -> {token_hash,user_id,label,created,revoked}
        self.notes: dict[str, CommonsNote] = {}
        self._by_owner_slug: dict[tuple[str, str], str] = {}
        self.acl: list[AclGrant] = []

    def healthcheck(self) -> None:
        return None

    def user_by_subject(self, oidc_subject: str) -> UserRecord | None:
        uid = self.users_by_subject.get(oidc_subject)
        return self.users.get(uid) if uid else None

    def user_by_email(self, email: str) -> UserRecord | None:
        if not email:
            return None
        for u in self.users.values():
            if u.email and u.email.lower() == email.lower():
                return u
        return None

    def user_by_id(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id) if user_id else None

    def ensure_user(self, identity: VerifiedIdentity) -> UserRecord:
        uid = self.users_by_subject.get(identity.subject)
        if uid:
            cur = self.users[uid]
            user = UserRecord(cur.id, cur.oidc_subject, identity.email or cur.email, cur.handle)
        else:
            user = UserRecord(str(uuid4()), identity.subject, identity.email)
            self.users_by_subject[identity.subject] = user.id
        self.users[user.id] = user
        return user

    def instance_for_user(self, user_id: str) -> InstanceRecord | None:
        return self.instances.get(user_id)

    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> None:
        self.sessions[token_hash] = (user_id, expires_at, False)

    def principal_for_session(self, token_hash: str, now: datetime) -> SessionPrincipal | None:
        session = self.sessions.get(token_hash)
        if not session:
            return None
        user_id, expires_at, revoked = session
        if revoked or expires_at <= now:
            return None
        return SessionPrincipal(self.users[user_id], self.instances.get(user_id))

    def revoke_session(self, token_hash: str) -> None:
        s = self.sessions.get(token_hash)
        if s:
            self.sessions[token_hash] = (s[0], s[1], True)

    def audit(self, user_id: str | None, event_type: str, data: dict) -> None:
        self.audit_events.append((user_id, event_type, data))

    def set_password(self, user_id: str, password_hash: str) -> None:
        self.passwords[user_id] = password_hash

    def password_hash_for(self, user_id: str) -> str | None:
        return self.passwords.get(user_id)

    def create_api_token(self, user_id: str, token_hash: str, label: str) -> str:
        from datetime import UTC, datetime as _dt

        tid = str(uuid4())
        self.api_tokens[tid] = {
            "token_hash": token_hash, "user_id": user_id, "label": label,
            "created": _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "revoked": False,
        }
        return tid

    def user_for_api_token(self, token_hash: str) -> UserRecord | None:
        for t in self.api_tokens.values():
            if t["token_hash"] == token_hash and not t["revoked"]:
                return self.users.get(t["user_id"])
        return None

    def list_api_tokens(self, user_id: str) -> list[dict]:
        return [
            {"id": tid, "label": t["label"], "created": t["created"], "revoked": t["revoked"]}
            for tid, t in self.api_tokens.items() if t["user_id"] == user_id
        ]

    def revoke_api_token(self, user_id: str, token_id: str) -> bool:
        t = self.api_tokens.get(token_id)
        if not t or t["user_id"] != user_id or t["revoked"]:
            return False
        t["revoked"] = True
        return True

    # --- content ---
    def publish_note(self, owner_id: str, note: dict, visibility: str) -> CommonsNote:
        from datetime import UTC, datetime as _dt

        now = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        key = (owner_id, note.get("slug", ""))
        existing_id = self._by_owner_slug.get(key)
        nid = existing_id or str(uuid4())
        created = self.notes[existing_id].created if existing_id else now
        rec = CommonsNote(
            id=nid,
            owner_id=owner_id,
            slug=note.get("slug", ""),
            title=note.get("title", ""),
            body=note.get("body", ""),
            visibility=visibility,
            kind=note.get("kind", "concept"),
            domain=note.get("domain", ""),
            tags=tuple(note.get("tags", ())),
            author=note.get("author", "user"),
            created=created,
            updated=now,
        )
        self.notes[nid] = rec
        self._by_owner_slug[key] = nid
        return rec

    def update_note(self, note_id: str, fields: dict) -> CommonsNote | None:
        from datetime import UTC, datetime as _dt

        rec = self.notes.get(note_id)
        if rec is None:
            return None
        updates = {}
        for col in EDITABLE_NOTE_FIELDS:
            if col in fields:
                updates[col] = tuple(fields[col]) if col == "tags" else fields[col]
        updates["updated"] = _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = CommonsNote(**{**rec.__dict__, **updates})
        self.notes[note_id] = rec
        return rec

    def get_note(self, note_id: str) -> CommonsNote | None:
        return self.notes.get(note_id)

    def list_visible_notes(self, user_id: str) -> list[CommonsNote]:
        return vis.visible_notes(self.notes.values(), user_id, self.acl)

    def delete_note(self, note_id: str) -> None:
        rec = self.notes.pop(note_id, None)
        if rec:
            self._by_owner_slug.pop((rec.owner_id, rec.slug), None)
            self.acl = [g for g in self.acl if g.note_id != note_id]

    def set_visibility(self, note_id: str, visibility: str) -> None:
        rec = self.notes.get(note_id)
        if rec:
            self.notes[note_id] = CommonsNote(
                **{**rec.__dict__, "visibility": visibility}
            )

    def share_note(
        self, note_id: str, principal_user_id: str, level: str = "read"
    ) -> None:
        # Upsert — re-sharing at a new level upgrades/downgrades the grant.
        self.acl = [
            g for g in self.acl
            if not (g.note_id == note_id and g.principal_user_id == principal_user_id)
        ]
        self.acl.append(
            AclGrant(note_id=note_id, principal_user_id=principal_user_id, level=level)
        )

    def unshare_note(self, note_id: str, principal_user_id: str) -> None:
        self.acl = [
            g for g in self.acl
            if not (g.note_id == note_id and g.principal_user_id == principal_user_id)
        ]

    def acl_for_note(self, note_id: str) -> list[AclGrant]:
        return [g for g in self.acl if g.note_id == note_id]


# --------------------------------------------------------------------------- #
# SQLite — the trusted-team backend (stdlib, one file, zero infra)
# --------------------------------------------------------------------------- #
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS commons_users (
    id TEXT PRIMARY KEY, oidc_subject TEXT UNIQUE NOT NULL, email TEXT, handle TEXT,
    password_hash TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS commons_instances (
    id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL, routing_key TEXT UNIQUE,
    status TEXT, upstream_url TEXT, inner_token_env TEXT
);
CREATE TABLE IF NOT EXISTS commons_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS commons_audit_events (
    id TEXT PRIMARY KEY, user_id TEXT, event_type TEXT, data TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS commons_notes (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, slug TEXT NOT NULL,
    title TEXT DEFAULT '', body TEXT DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private',
    kind TEXT DEFAULT 'concept', domain TEXT DEFAULT '', tags TEXT DEFAULT '[]',
    author TEXT DEFAULT 'user', created TEXT, updated TEXT,
    UNIQUE (owner_id, slug)
);
CREATE INDEX IF NOT EXISTS commons_notes_owner_idx ON commons_notes(owner_id);
CREATE TABLE IF NOT EXISTS commons_acl (
    note_id TEXT NOT NULL, principal_user_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'read',
    PRIMARY KEY (note_id, principal_user_id)
);
CREATE TABLE IF NOT EXISTS commons_api_tokens (
    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user_id TEXT NOT NULL,
    label TEXT, created_at TEXT, revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS commons_api_tokens_user_idx ON commons_api_tokens(user_id);
"""


class SqliteCommonsRepository:
    """Persistent commons store on stdlib sqlite3 — the trusted-team backend.

    Same Protocol + same visibility semantics as the others. Isolation is the
    app-layer query (own + public + granted); there is no DB-level RLS backstop
    (that's the Postgres backend's job, for the public/scale phase). Schema is
    auto-created on first open; data survives restarts.
    """

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # one shared connection so :memory: persists across calls within a process;
        # WAL + a short busy timeout handle the low write volume of a small team.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=4000")
        self._db.executescript(_SQLITE_SCHEMA)
        # Lightweight migration: add password_hash to pre-existing user tables.
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(commons_users)")}
        if "password_hash" not in cols:
            self._db.execute("ALTER TABLE commons_users ADD COLUMN password_hash TEXT")
        self._db.commit()

    @contextmanager
    def _cur(self):
        cur = self._db.cursor()
        try:
            yield cur
            self._db.commit()
        finally:
            cur.close()

    @staticmethod
    def _now() -> str:
        from datetime import UTC, datetime as _dt

        return _dt.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _user(row) -> UserRecord:
        return UserRecord(id=row[0], oidc_subject=row[1], email=row[2] or "", handle=row[3] or "")

    @staticmethod
    def _note(row) -> CommonsNote:
        return CommonsNote(
            id=row[0], owner_id=row[1], slug=row[2], title=row[3] or "", body=row[4] or "",
            visibility=row[5], kind=row[6] or "concept", domain=row[7] or "",
            tags=tuple(json.loads(row[8] or "[]")), author=row[9] or "user",
            created=row[10] or "", updated=row[11] or "",
        )

    _NOTE_SEL = ("id, owner_id, slug, title, body, visibility, kind, domain, tags, "
                 "author, created, updated")

    def healthcheck(self) -> None:
        with self._cur() as c:
            c.execute("SELECT 1").fetchone()

    def user_by_subject(self, oidc_subject: str) -> UserRecord | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE oidc_subject=?",
                (oidc_subject,),
            ).fetchone()
        return self._user(row) if row else None

    def user_by_email(self, email: str) -> UserRecord | None:
        if not email:
            return None
        with self._cur() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE lower(email)=lower(?)",
                (email,),
            ).fetchone()
        return self._user(row) if row else None

    def user_by_id(self, user_id: str) -> UserRecord | None:
        if not user_id:
            return None
        with self._cur() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE id=?",
                (user_id,),
            ).fetchone()
        return self._user(row) if row else None

    def ensure_user(self, identity: VerifiedIdentity) -> UserRecord:
        now = self._now()
        with self._cur() as c:
            row = c.execute(
                "SELECT id, oidc_subject, email, handle FROM commons_users WHERE oidc_subject=?",
                (identity.subject,),
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE commons_users SET email=?, updated_at=? WHERE id=?",
                    (identity.email, now, row[0]),
                )
                return UserRecord(row[0], row[1], identity.email or (row[2] or ""), row[3] or "")
            uid = str(uuid4())
            c.execute(
                "INSERT INTO commons_users (id, oidc_subject, email, handle, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (uid, identity.subject, identity.email, "", now, now),
            )
        return UserRecord(uid, identity.subject, identity.email)

    def instance_for_user(self, user_id: str) -> InstanceRecord | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT id, user_id, routing_key, status, upstream_url, inner_token_env"
                " FROM commons_instances WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return InstanceRecord(
            id=row[0], user_id=row[1], routing_key=row[2], status=row[3],
            upstream_url=row[4] or "", inner_token_env=row[5] or "",
        )

    def create_session(self, user_id: str, token_hash: str, expires_at: datetime) -> None:
        with self._cur() as c:
            c.execute(
                "INSERT INTO commons_sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
                (token_hash, user_id, expires_at.isoformat()),
            )

    def principal_for_session(self, token_hash: str, now: datetime) -> SessionPrincipal | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT u.id, u.oidc_subject, u.email, u.handle,"
                " i.id, i.user_id, i.routing_key, i.status, i.upstream_url, i.inner_token_env"
                " FROM commons_sessions s JOIN commons_users u ON u.id=s.user_id"
                " LEFT JOIN commons_instances i ON i.user_id=u.id"
                " WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at > ?",
                (token_hash, now.isoformat()),
            ).fetchone()
        if not row:
            return None
        inst = None
        if row[4]:
            inst = InstanceRecord(
                id=row[4], user_id=row[5], routing_key=row[6], status=row[7],
                upstream_url=row[8] or "", inner_token_env=row[9] or "",
            )
        return SessionPrincipal(user=self._user(row[:4]), instance=inst)

    def revoke_session(self, token_hash: str) -> None:
        with self._cur() as c:
            c.execute(
                "UPDATE commons_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (self._now(), token_hash),
            )

    def audit(self, user_id: str | None, event_type: str, data: dict) -> None:
        with self._cur() as c:
            c.execute(
                "INSERT INTO commons_audit_events (id, user_id, event_type, data, created_at)"
                " VALUES (?,?,?,?,?)",
                (str(uuid4()), user_id, event_type, json.dumps(data, sort_keys=True), self._now()),
            )

    def set_password(self, user_id: str, password_hash: str) -> None:
        with self._cur() as c:
            c.execute(
                "UPDATE commons_users SET password_hash=?, updated_at=? WHERE id=?",
                (password_hash, self._now(), user_id),
            )

    def password_hash_for(self, user_id: str) -> str | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT password_hash FROM commons_users WHERE id=?", (user_id,)
            ).fetchone()
        return row[0] if row and row[0] else None

    def create_api_token(self, user_id: str, token_hash: str, label: str) -> str:
        tid = str(uuid4())
        with self._cur() as c:
            c.execute(
                "INSERT INTO commons_api_tokens (id, token_hash, user_id, label, created_at)"
                " VALUES (?,?,?,?,?)",
                (tid, token_hash, user_id, label, self._now()),
            )
        return tid

    def user_for_api_token(self, token_hash: str) -> UserRecord | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT u.id, u.oidc_subject, u.email, u.handle"
                " FROM commons_api_tokens t JOIN commons_users u ON u.id=t.user_id"
                " WHERE t.token_hash=? AND t.revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
        return self._user(row) if row else None

    def list_api_tokens(self, user_id: str) -> list[dict]:
        with self._cur() as c:
            rows = c.execute(
                "SELECT id, label, created_at, revoked_at FROM commons_api_tokens"
                " WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [{"id": r[0], "label": r[1] or "", "created": r[2] or "", "revoked": r[3] is not None} for r in rows]

    def revoke_api_token(self, user_id: str, token_id: str) -> bool:
        with self._cur() as c:
            cur = c.execute(
                "UPDATE commons_api_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                (self._now(), token_id, user_id),
            )
            return cur.rowcount > 0

    def publish_note(self, owner_id: str, note: dict, visibility: str) -> CommonsNote:
        now = self._now()
        slug = note.get("slug", "")
        tags = json.dumps(list(note.get("tags", ())))
        with self._cur() as c:
            row = c.execute(
                "SELECT id, created FROM commons_notes WHERE owner_id=? AND slug=?",
                (owner_id, slug),
            ).fetchone()
            if row:
                nid, created = row[0], row[1]
                c.execute(
                    "UPDATE commons_notes SET title=?, body=?, visibility=?, kind=?, domain=?,"
                    " tags=?, author=?, updated=? WHERE id=?",
                    (note.get("title", ""), note.get("body", ""), visibility,
                     note.get("kind", "concept"), note.get("domain", ""), tags,
                     note.get("author", "user"), now, nid),
                )
            else:
                nid, created = str(uuid4()), now
                c.execute(
                    "INSERT INTO commons_notes (id, owner_id, slug, title, body, visibility,"
                    " kind, domain, tags, author, created, updated)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, owner_id, slug, note.get("title", ""), note.get("body", ""), visibility,
                     note.get("kind", "concept"), note.get("domain", ""), tags,
                     note.get("author", "user"), created, now),
                )
        return self.get_note(nid)

    def update_note(self, note_id: str, fields: dict) -> CommonsNote | None:
        sets, params = [], []
        for col in EDITABLE_NOTE_FIELDS:
            if col in fields:
                sets.append(f"{col}=?")
                params.append(
                    json.dumps(list(fields[col])) if col == "tags" else fields[col]
                )
        if not sets:
            return self.get_note(note_id)
        params += [self._now(), note_id]
        with self._cur() as c:
            c.execute(
                f"UPDATE commons_notes SET {', '.join(sets)}, updated=? WHERE id=?",
                params,
            )
        return self.get_note(note_id)

    def get_note(self, note_id: str) -> CommonsNote | None:
        with self._cur() as c:
            row = c.execute(
                f"SELECT {self._NOTE_SEL} FROM commons_notes WHERE id=?", (note_id,)
            ).fetchone()
        return self._note(row) if row else None

    def list_visible_notes(self, user_id: str) -> list[CommonsNote]:
        with self._cur() as c:
            rows = c.execute(
                f"SELECT {self._NOTE_SEL} FROM commons_notes n"
                " WHERE n.owner_id=? OR n.visibility='public'"
                " OR (n.visibility='shared' AND EXISTS ("
                "   SELECT 1 FROM commons_acl a WHERE a.note_id=n.id"
                "   AND a.principal_user_id=? AND a.level IN ('read','write','comment')))"
                " ORDER BY n.updated DESC, n.id DESC",
                (user_id, user_id),
            ).fetchall()
        return [self._note(r) for r in rows]

    def delete_note(self, note_id: str) -> None:
        with self._cur() as c:
            c.execute("DELETE FROM commons_acl WHERE note_id=?", (note_id,))
            c.execute("DELETE FROM commons_notes WHERE id=?", (note_id,))

    def set_visibility(self, note_id: str, visibility: str) -> None:
        with self._cur() as c:
            c.execute(
                "UPDATE commons_notes SET visibility=?, updated=? WHERE id=?",
                (visibility, self._now(), note_id),
            )

    def share_note(
        self, note_id: str, principal_user_id: str, level: str = "read"
    ) -> None:
        with self._cur() as c:
            c.execute(
                "INSERT INTO commons_acl (note_id, principal_user_id, level)"
                " VALUES (?,?,?)"
                " ON CONFLICT (note_id, principal_user_id) DO UPDATE SET level=excluded.level",
                (note_id, principal_user_id, level),
            )

    def unshare_note(self, note_id: str, principal_user_id: str) -> None:
        with self._cur() as c:
            c.execute(
                "DELETE FROM commons_acl WHERE note_id=? AND principal_user_id=?",
                (note_id, principal_user_id),
            )

    def acl_for_note(self, note_id: str) -> list[AclGrant]:
        with self._cur() as c:
            rows = c.execute(
                "SELECT note_id, principal_user_id, level FROM commons_acl WHERE note_id=?",
                (note_id,),
            ).fetchall()
        return [AclGrant(note_id=r[0], principal_user_id=r[1], level=r[2]) for r in rows]
