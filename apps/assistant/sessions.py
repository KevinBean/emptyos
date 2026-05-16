"""Session store + export/archive mixin for AssistantApp.

SQLite-backed session + message store with one-time JSON migration. Kept as a
mixin (not a pure helper class) so methods still use ``self.db`` / ``self.kernel``
without threading state through arguments — same trade-off the reactor
``reactions_*`` mixins take.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .prompts import MAX_HISTORY


SESSION_AUTONAME_SYSTEM = (
    "You name a chat session in 3 to 5 words capturing its topic. Output "
    "the words only — no quotes, no punctuation, no preamble.\n\n"
    "Do NOT:\n"
    "- Wrap the output in quotes, dashes, or brackets.\n"
    "- Add a verb form like 'Discussion of' or 'Chat about' — name the topic directly.\n"
    "- Use trailing punctuation.\n"
    "- Exceed 5 words."
)


class SessionsMixin:
    """SQLite session store + transcript export/archive helpers."""

    # ── SQLite Session Store ──────────────────────────────────

    def _init_db(self):
        """Create tables and migrate from JSON if needed."""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'New chat',
                backend TEXT NOT NULL DEFAULT 'auto',
                system_prompt TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                agent TEXT NOT NULL DEFAULT '',
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        """)
        # In-place migration for DBs created before project_id landed.
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(sessions)").fetchall()}
        if "project_id" not in cols:
            self.db.execute(
                "ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
            )
        self.db.commit()
        json_path = self.data_dir / "sessions.json"
        if (
            json_path.exists()
            and self.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        ):
            self._migrate_from_json(json_path)

    def _migrate_from_json(self, json_path: Path):
        """One-time migration from sessions.json → SQLite."""
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for sid, s in data.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO sessions (id, name, backend, system_prompt, created) VALUES (?,?,?,?,?)",
                    (
                        sid,
                        s.get("name", "New chat"),
                        s.get("backend", "auto"),
                        s.get("system_prompt", ""),
                        s.get("created", ""),
                    ),
                )
                for msg in s.get("messages", []):
                    self.db.execute(
                        "INSERT INTO messages (session_id, role, text, agent, ts) VALUES (?,?,?,?,?)",
                        (
                            sid,
                            msg.get("role", ""),
                            msg.get("text", ""),
                            msg.get("agent", ""),
                            msg.get("ts", ""),
                        ),
                    )
            self.db.commit()
            json_path.rename(json_path.with_suffix(".json.bak"))
        except Exception as e:
            print(f"[Assistant] JSON migration failed: {e}")

    def _get_session(self, sid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if not row:
            return None
        messages = [
            {"role": m["role"], "text": m["text"], "agent": m["agent"], "ts": m["ts"]}
            for m in self.db.execute(
                "SELECT role, text, agent, ts FROM messages WHERE session_id = ? ORDER BY id",
                (sid,),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "name": row["name"],
            "backend": row["backend"],
            "system_prompt": row["system_prompt"],
            "project_id": row["project_id"] if "project_id" in row.keys() else "",
            "messages": messages,
            "created": row["created"],
        }

    def _create_session(self, name: str = "", backend: str = "auto") -> dict:
        sid = str(uuid.uuid4())[:8]
        created = datetime.now(UTC).isoformat()
        self.db.execute(
            "INSERT INTO sessions (id, name, backend, system_prompt, created) VALUES (?,?,?,?,?)",
            (sid, name or "New chat", backend, "", created),
        )
        self.db.commit()
        return {
            "id": sid,
            "name": name or "New chat",
            "backend": backend,
            "system_prompt": "",
            "messages": [],
            "created": created,
        }

    def _add_message(self, sid: str, role: str, text: str, agent: str = ""):
        ts = datetime.now(UTC).isoformat()
        self.db.execute(
            "INSERT INTO messages (session_id, role, text, agent, ts) VALUES (?,?,?,?,?)",
            (sid, role, text, agent, ts),
        )
        count = self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()[0]
        if count > MAX_HISTORY:
            self.db.execute(
                "DELETE FROM messages WHERE id IN ("
                "  SELECT id FROM messages WHERE session_id = ? ORDER BY id LIMIT ?"
                ")",
                (sid, count - MAX_HISTORY),
            )
        self.db.commit()

    # ── Auto-Naming ───────────────────────────────────────────

    async def _auto_name(self, session_id: str, first_message: str, websocket=None):
        """Generate a short session name from the first message."""
        try:
            name = await self.think(
                f"First message: {first_message[:200]}",
                system=SESSION_AUTONAME_SYSTEM,
                domain="text",
                temperature=0.3,
            )
            name = name.strip().strip("\"'").strip()[:50]
            if name:
                self.db.execute("UPDATE sessions SET name = ? WHERE id = ?", (name, session_id))
                self.db.commit()
                if websocket:
                    try:
                        await websocket.send_json({"type": "session-renamed", "name": name})
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Session Export ────────────────────────────────────────

    async def _export_session(self, session_id: str) -> dict:
        """Export a session to a vault markdown note."""
        session = self._get_session(session_id)
        if not session:
            return {"error": "session not found"}

        messages = session.get("messages", [])
        if not messages:
            return {"error": "no messages to export"}

        name = session.get("name", "Chat")
        ts = session.get("created", datetime.now(UTC).isoformat())[:10]
        lines = [
            "---",
            f"date: {ts}",
            "tags:",
            "  - ai-chat",
            "  - assistant",
            "---",
            "",
            f"# {name}",
            "",
        ]
        for msg in messages:
            role = (
                "**You**" if msg["role"] == "user" else f"**AI** ({msg.get('agent', 'assistant')})"
            )
            text = msg.get("text", "")
            lines.append(f"{role}:")
            lines.append(text)
            lines.append("")

        content = "\n".join(lines)
        safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in name)[:40].strip()
        filename = f"{ts} {safe_name}.md"
        vault_dir = self.vault_config_path(
            "exports", "30_Resources/EmptyOS/assistant"
        )  # default fallback
        if not vault_dir:
            return {"error": "vault not configured"}
        vault_dir.mkdir(parents=True, exist_ok=True)
        path = vault_dir / filename
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path).replace("\\", "/"), "filename": filename}

    # ── Auto-Archive ─────────────────────────────────────────

    async def _auto_archive(self):
        """Archive sessions idle >7 days with >5 messages to vault, then delete."""
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        rows = self.db.execute(
            """
            SELECT s.id, s.name, MAX(m.ts) as last_msg, COUNT(m.id) as msg_count
            FROM sessions s JOIN messages m ON s.id = m.session_id
            GROUP BY s.id
            HAVING msg_count >= 5 AND last_msg < ?
        """,
            (cutoff,),
        ).fetchall()

        archived = []
        for row in rows:
            result = await self._export_session(row["id"])
            if result.get("ok"):
                async with self._sessions_lock:
                    self.db.execute("DELETE FROM messages WHERE session_id = ?", (row["id"],))
                    self.db.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
                    self.db.commit()
                archived.append({"id": row["id"], "name": row["name"], "path": result["path"]})
        return archived
