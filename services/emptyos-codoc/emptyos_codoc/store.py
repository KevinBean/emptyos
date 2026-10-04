"""SQLite store for the co-doc service — documents, members, persisted state.

One file, stdlib only (mirrors the commons SQLite backend's zero-infra posture).
A co-doc is the *live* working surface (Phase 2 of the collaboration roadmap):
ephemeral real-time edit, distinct from the durable vault and the published
commons. Membership here is the ACL-gated-join control — who may open the live
doc, at what level — the same shape as commons AclGrant.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

# read = observe the live doc; write = apply edits. (comment is a commons-level
# concept; the live relay only distinguishes observe vs mutate.)
LEVELS = ("read", "write")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS codocs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codoc_members (
    doc_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'read',
    PRIMARY KEY (doc_id, principal_id)
);
CREATE TABLE IF NOT EXISTS codoc_state (
    doc_id TEXT PRIMARY KEY,
    state BLOB,
    updated TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class CoDocStore:
    def __init__(self, path: str = ":memory:"):
        # check_same_thread=False so the ASGI event loop + test threads share it;
        # writes are short and serialized by SQLite's own lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    @contextmanager
    def _cur(self):
        cur = self._db.cursor()
        try:
            yield cur
            self._db.commit()
        finally:
            cur.close()

    def create_doc(self, owner_id: str, title: str = "") -> dict:
        doc_id = str(uuid.uuid4())
        now = _now()
        with self._cur() as c:
            c.execute(
                "INSERT INTO codocs (id, owner_id, title, created, updated)"
                " VALUES (?,?,?,?,?)",
                (doc_id, owner_id, title, now, now),
            )
        return {"id": doc_id, "owner_id": owner_id, "title": title,
                "created": now, "updated": now}

    def get_doc(self, doc_id: str) -> dict | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT id, owner_id, title, created, updated FROM codocs WHERE id=?",
                (doc_id,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "owner_id": row[1], "title": row[2],
                "created": row[3], "updated": row[4]}

    def add_member(self, doc_id: str, principal_id: str, level: str = "read") -> None:
        with self._cur() as c:
            c.execute(
                "INSERT INTO codoc_members (doc_id, principal_id, level) VALUES (?,?,?)"
                " ON CONFLICT (doc_id, principal_id) DO UPDATE SET level=excluded.level",
                (doc_id, principal_id, level),
            )

    def member_level(self, doc_id: str, principal_id: str) -> str | None:
        """Effective level for a principal on a doc, or None if not a member.

        The owner is always an implicit ``write`` member.
        """
        doc = self.get_doc(doc_id)
        if doc is None:
            return None
        if doc["owner_id"] == principal_id:
            return "write"
        with self._cur() as c:
            row = c.execute(
                "SELECT level FROM codoc_members WHERE doc_id=? AND principal_id=?",
                (doc_id, principal_id),
            ).fetchone()
        return row[0] if row else None

    def contributors(self, doc_id: str) -> list[str]:
        """Principals who could have authored the doc — owner + write members."""
        doc = self.get_doc(doc_id)
        if doc is None:
            return []
        with self._cur() as c:
            rows = c.execute(
                "SELECT principal_id FROM codoc_members WHERE doc_id=? AND level='write'",
                (doc_id,),
            ).fetchall()
        return sorted({doc["owner_id"]} | {r[0] for r in rows})

    def get_state(self, doc_id: str) -> bytes | None:
        with self._cur() as c:
            row = c.execute(
                "SELECT state FROM codoc_state WHERE doc_id=?", (doc_id,)
            ).fetchone()
        return bytes(row[0]) if row and row[0] is not None else None

    def save_state(self, doc_id: str, state: bytes) -> None:
        with self._cur() as c:
            c.execute(
                "INSERT INTO codoc_state (doc_id, state, updated) VALUES (?,?,?)"
                " ON CONFLICT (doc_id) DO UPDATE SET state=excluded.state, updated=excluded.updated",
                (doc_id, state, _now()),
            )
