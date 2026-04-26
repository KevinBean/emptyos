"""Real-time service — EventBus to WebSocket bridge.

Pushes kernel events to connected browser clients.
Clients can subscribe to specific event types.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class RealtimeManager:
    """Manages WebSocket connections and event broadcasting."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel
        self._clients: dict[WebSocket, set[str]] = {}  # ws -> subscribed event types
        self._unsub = None

    async def start(self):
        """Subscribe to all kernel events for broadcasting."""
        self._unsub = self.kernel.events.on_any(self._broadcast)
        print(f"[Realtime] WebSocket bridge active")

    async def stop(self):
        """Unsubscribe and disconnect all clients."""
        if self._unsub:
            self._unsub()
        for ws in list(self._clients.keys()):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()

    async def handle_connection(self, ws: WebSocket):
        """Handle a single WebSocket connection lifecycle."""
        await ws.accept()
        self._clients[ws] = set()  # empty = subscribe to all

        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                    # Client can send: {"subscribe": ["vault:changed", "task:*"]}
                    if "subscribe" in msg:
                        self._clients[ws] = set(msg["subscribe"])
                    # Client can send: {"unsubscribe": true} to get all events
                    elif "unsubscribe" in msg:
                        self._clients[ws] = set()
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._clients.pop(ws, None)

    async def _broadcast(self, event):
        """Broadcast an event to all matching WebSocket clients."""
        if not self._clients:
            return

        payload = json.dumps({
            "type": event.type,
            "data": event.data,
            "source": event.source,
            "timestamp": event.timestamp,
        })

        dead = []
        for ws, subscriptions in self._clients.items():
            if subscriptions and not self._matches(event.type, subscriptions):
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._clients.pop(ws, None)

    @staticmethod
    def _matches(event_type: str, subscriptions: set[str]) -> bool:
        """Check if event type matches any subscription pattern."""
        for sub in subscriptions:
            if sub == event_type:
                return True
            # Wildcard: "vault:*" matches "vault:changed"
            if sub.endswith("*") and event_type.startswith(sub[:-1]):
                return True
        return False

    @property
    def client_count(self) -> int:
        return len(self._clients)
