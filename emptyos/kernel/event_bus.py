"""Event bus — in-process async pub/sub with SQLite persistence."""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


# A handler slower than this taxes every emit of its event type, because
# emit() awaits handlers serially (that ordering is a semantic guarantee —
# e.g. VaultIndex._on_vault_changed must finish before later handlers query
# the index — so we cannot fan out or time-out). We therefore *detect* rather
# than intervene: the warning names the call site that should wrap its slow
# work in asyncio.to_thread / asyncio.create_task.
# See .claude/rules/debugging.md § async-wedge catalog.
SLOW_HANDLER_S = 1.0

# Don't let one chronically-slow handler on a chatty event flood syslog.
SLOW_HANDLER_WARN_INTERVAL_S = 60.0


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    source: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EventBus:
    """Async event bus with optional SQLite persistence."""

    def __init__(self, db_path: Path | None = None):
        self._handlers: dict[str, list[Callable]] = {}
        self._any_handlers: list[Callable] = []
        self._db: sqlite3.Connection | None = None
        # Guards the shared connection: both reads (history) and writes (emit)
        # run off the event loop via asyncio.to_thread, so concurrent worker
        # threads must serialize access to the single connection.
        self._db_lock = threading.Lock()
        self._syslog = None  # Set via set_syslog() after kernel init
        # handler_name -> monotonic ts of last slow-handler warning.
        # Deliberately NOT keyed by event type: an on_any handler runs on every
        # one of the ~540 emitted event names, so a per-type key would let one
        # wedged handler emit dozens of duplicate warnings a minute, drowning
        # the log precisely when it is being read. The remediation is per
        # handler, and the message names the event type that tripped it.
        self._slow_warn_at: dict[str, float] = {}
        if db_path:
            self._init_db(db_path)

    def set_syslog(self, syslog):
        """Attach syslog for structured error logging (called after kernel init)."""
        self._syslog = syslog

    def _init_db(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so persist/query can run on asyncio.to_thread
        # worker threads (serialized by self._db_lock) instead of the loop.
        # timeout=15: see the matching comment in kernel/syslog.py — same
        # class of transient lock, same reasoning, separate file.
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15.0)
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
        # Retention prune filters on timestamp; without this the daily DELETE
        # full-scans a table that grows by one row per emit, forever.
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
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

    def _persist(self, event: Event) -> None:
        """Synchronous DB write — run via asyncio.to_thread, never on the loop.

        The INSERT + commit performs a WAL fsync; doing it inline in emit()
        blocked the event loop on every emitted event (async-wedge catalog,
        .claude/rules/debugging.md).

        Best-effort, same as syslog.py's log(): a transient "database is
        locked" here must not crash emit() and take down whatever awaited
        it. Mirrors the "never raises" guard _check_slow already documents
        below — this was the one write in this file that didn't honor it.
        """
        try:
            with self._db_lock:
                self._db.execute(
                    "INSERT INTO events (id, type, data, source, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (event.id, event.type, json.dumps(event.data), event.source, event.timestamp),
                )
                self._db.commit()
        except sqlite3.Error as e:
            # Console, not self._syslog — that write path can hit the exact
            # same class of transient lock, and this is the fallback for
            # when a write already failed once.
            print(f"[EventBus] dropped persist for {event.type}: {e}")

    def _check_slow(self, handler: Callable, event_type: str, elapsed: float) -> None:
        """Warn when a handler blocked emit() for too long.

        Detection only — never cancels. Cancelling a handler mid read-modify-write
        would manufacture the vault data-loss class we defend against elsewhere.
        Never raises: a broken logger must not break the bus.
        """
        if elapsed < SLOW_HANDLER_S or not self._syslog:
            return
        name = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", str(handler))
        now = time.monotonic()
        last = self._slow_warn_at.get(name, 0.0)
        if now - last < SLOW_HANDLER_WARN_INTERVAL_S:
            return
        self._slow_warn_at[name] = now
        try:
            self._syslog.warn(
                "event_bus",
                f"slow handler {name} took {elapsed:.2f}s for {event_type}",
                data={"handler": name, "event_type": event_type, "elapsed_s": round(elapsed, 3)},
            )
        except Exception:
            pass

    async def emit(self, event_type: str, data: dict, source: str = ""):
        """Emit an event. Notifies all matching handlers and persists to DB.

        Handlers run **serially**, each awaited to completion. That ordering is
        load-bearing (later handlers may depend on earlier ones having landed),
        so a slow handler delays everything behind it. `_check_slow` surfaces
        those call sites rather than silently absorbing the latency.
        """
        event = Event(type=event_type, data=data, source=source)

        if self._db:
            await asyncio.to_thread(self._persist, event)

        all_handlers = self._handlers.get(event_type, []) + self._any_handlers
        for handler in all_handlers:
            started = time.monotonic()
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
            finally:
                self._check_slow(handler, event_type, time.monotonic() - started)

    def prune(self, days: int, chunk: int = 10_000) -> int:
        """Delete events older than `days`. Returns rows removed.

        Synchronous — run via asyncio.to_thread, never on the loop. The table
        gains a row on every emit and had no retention, so on a long-lived
        daemon the first prune can face a very large backlog: delete in bounded
        chunks, committing each, so we never hold the lock (or a transaction)
        for an unbounded stretch.

        `timestamp` is a UTC ISO-8601 string, so lexical `<` is chronological.
        """
        if not self._db or days <= 0:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        removed = 0
        while True:
            with self._db_lock:
                cur = self._db.execute(
                    "DELETE FROM events WHERE id IN "
                    "(SELECT id FROM events WHERE timestamp < ? LIMIT ?)",
                    (cutoff, chunk),
                )
                self._db.commit()
                n = cur.rowcount or 0
            removed += n
            if n < chunk:
                return removed

    def _query(self, event_type: str | None, limit: int) -> list[dict]:
        """Synchronous DB read — run via asyncio.to_thread, never on the loop."""
        with self._db_lock:
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
            {
                "id": r[0],
                "type": r[1],
                "data": json.loads(r[2] or "{}"),
                "source": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    async def history(self, event_type: str | None = None, limit: int = 50) -> list[dict]:
        """Query persisted event history."""
        if not self._db:
            return []
        return await asyncio.to_thread(self._query, event_type, limit)
