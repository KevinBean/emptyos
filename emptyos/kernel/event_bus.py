"""Event bus — in-process async pub/sub with SQLite persistence."""

from __future__ import annotations

import inspect
import json
import sqlite3
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    source: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EventBus:
    """Async event bus with optional SQLite persistence."""

    def __init__(self, db_path: Path | None = None):
        self._handlers: dict[str, list[Callable]] = {}
        self._any_handlers: list[Callable] = []
        self._db: sqlite3.Connection | None = None
        self._syslog = None  # Set via set_syslog() after kernel init
        if db_path:
            self._init_db(db_path)

    def set_syslog(self, syslog):
        """Attach syslog for structured error logging (called after kernel init)."""
        self._syslog = syslog

    def _init_db(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                data TEXT,
                source TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
        self._db.commit()

    def on(self, event_type: str, callback: Callable) -> Callable:
        """Subscribe to events of a specific type. Returns unsubscribe function."""
        self._handlers.setdefault(event_type, []).append(callback)

        def unsubscribe():
            self._handlers[event_type].remove(callback)

        return unsubscribe

    def on_any(self, callback: Callable) -> Callable:
        """Subscribe to all events."""
        self._any_handlers.append(callback)

        def unsubscribe():
            self._any_handlers.remove(callback)

        return unsubscribe

    async def emit(self, event_type: str, data: dict, source: str = ""):
        """Emit an event. Notifies all matching handlers and persists to DB."""
        event = Event(type=event_type, data=data, source=source)

        if self._db:
            self._db.execute(
                "INSERT INTO events (id, type, data, source, timestamp) VALUES (?, ?, ?, ?, ?)",
                (event.id, event.type, json.dumps(event.data), event.source, event.timestamp),
            )
            self._db.commit()

        all_handlers = self._handlers.get(event_type, []) + self._any_handlers
        for handler in all_handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                tb = traceback.format_exc()
                if self._syslog:
                    self._syslog.error(
                        "event_bus",
                        f"Handler error for {event_type}: {e}",
                        data={
                            "event_type": event_type,
                            "handler": getattr(handler, "__name__", str(handler)),
                            "event_data": event.data,
                            "event_source": event.source,
                            "traceback": tb,
                        },
                    )
                else:
                    print(f"[EventBus] Handler error for {event_type}: {e}\n{tb}")

    async def history(
        self, event_type: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Query persisted event history."""
        if not self._db:
            return []
        if event_type:
            rows = self._db.execute(
                "SELECT id, type, data, source, timestamp FROM events WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, type, data, source, timestamp FROM events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "data": json.loads(r[2] or "{}"), "source": r[3], "timestamp": r[4]}
            for r in rows
        ]
