"""Shared session management for practice apps.

Used by: interview-studio, speaking, voice-review, shadowing, and future practice apps.

Usage:
    class MyApp(BaseApp):
        def __init__(self, ...):
            self.sessions = SessionStore(self.data_dir / "sessions")

        async def start(self):
            session = self.sessions.create({"scenario": "free", "turns": []})
            self.sessions.save(session)

        async def get(self, sid):
            return self.sessions.load(sid)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path


class SessionStore:
    """JSON-file session store. Each session is a .json file in a directory.

    Provides create/load/save/list/find operations. Apps add their own
    fields to the session dict — this class only manages the file I/O
    and common metadata (id, created, updated).
    """

    def __init__(self, directory: Path | str, max_history: int = 500):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max = max_history

    @property
    def directory(self) -> Path:
        return self._dir

    def create(self, data: dict | None = None, id_len: int = 12) -> dict:
        """Create a new session dict with auto-generated id and timestamp."""
        now = datetime.now(UTC).isoformat()
        session = {
            "id": uuid.uuid4().hex[:id_len],
            "created": now,
            "updated": now,
        }
        if data:
            session.update(data)
        return session

    def save(self, session: dict):
        """Persist session to disk."""
        session["updated"] = datetime.now(UTC).isoformat()
        sid = session["id"]
        p = self._dir / f"{sid}.json"
        p.write_text(
            json.dumps(session, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def load(self, session_id: str) -> dict | None:
        """Load a session by ID. Returns None if not found."""
        p = self._dir / f"{session_id}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def find(self, partial_id: str) -> dict | None:
        """Find a session by partial ID match."""
        for p in self._dir.glob("*.json"):
            if partial_id in p.stem:
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
        return None

    def list(self, limit: int = 20, fields: list[str] | None = None) -> list[dict]:
        """List sessions, most recent first (by file mtime). Optionally extract only specific fields."""
        paths = list(self._dir.glob("*.json"))
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        sessions = []
        for p in paths[:limit]:
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
                if fields:
                    s = {k: s.get(k) for k in fields if k in s}
                sessions.append(s)
            except Exception:
                continue
        return sessions

    def delete(self, session_id: str) -> bool:
        """Delete a session file."""
        p = self._dir / f"{session_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def count(self) -> int:
        """Count total sessions."""
        return len(list(self._dir.glob("*.json")))


class HistoryStore:
    """Single-file history store for apps that use one JSON array (e.g., shadowing).

    Keeps a rolling window of the most recent entries.
    """

    def __init__(self, path: Path | str, max_entries: int = 500):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max = max_entries

    def load(self) -> list[dict]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def save(self, entries: list[dict]):
        self._path.write_text(
            json.dumps(entries[-self._max :], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append(self, entry: dict) -> list[dict]:
        """Add an entry and save. Returns updated list."""
        entries = self.load()
        entries.append(entry)
        self.save(entries)
        return entries

    def count(self) -> int:
        return len(self.load())
