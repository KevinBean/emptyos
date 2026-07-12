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
        # Browser-side capture: request_capture() sends a {capture_request} message
        # over the WS, the browser captures via Web Speech API or getUserMedia and
        # POSTs back a {capture_response, id, ...}. The Future for each in-flight
        # request is keyed by id here so the response handler can resolve it.
        self._pending_captures: dict[str, asyncio.Future] = {}

    async def start(self):
        """Subscribe to all kernel events for broadcasting.

        Registered as a sync shim that schedules _broadcast as a task —
        EventBus.emit() iterates handlers serially with `await`, so a
        slow broadcast would block every subsequent handler. Fire-and-
        forget keeps the bus responsive even if WS sends take their full
        2s timeout against a half-dead peer.
        """
        def _shim(event):
            asyncio.create_task(self._broadcast(event))

        self._unsub = self.kernel.events.on_any(_shim)
        print("[Realtime] WebSocket bridge active")

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
                    # Client responds to a capture_request with the captured data
                    elif msg.get("type") == "capture_response":
                        rid = msg.get("id")
                        fut = self._pending_captures.get(rid)
                        if fut and not fut.done():
                            fut.set_result(msg)
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._clients.pop(ws, None)

    async def _broadcast(self, event):
        """Broadcast an event to all matching WebSocket clients.

        Fire-and-forget: scheduled as a background task so EventBus.emit()
        never blocks on a half-dead peer. Per-client sends are run
        concurrently with a 2s timeout — a peer whose TCP connection went
        silent during PC sleep would otherwise stall here for the Windows
        retransmit window (~30s+) and wedge the entire event bus.
        """
        if not self._clients:
            return

        payload = json.dumps(
            {
                "type": event.type,
                "data": event.data,
                "source": event.source,
                "timestamp": event.timestamp,
            }
        )

        targets = [
            ws
            for ws, subs in list(self._clients.items())
            if not subs or self._matches(event.type, subs)
        ]
        if not targets:
            return

        async def _send(ws):
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(_send(ws) for ws in targets), return_exceptions=False)
        for ws in results:
            if ws is not None:
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

    async def request_capture(
        self,
        capability: str,
        mode: str = "speech",
        timeout: float = 30.0,
        **kwargs,
    ) -> dict:
        """Ask a connected browser to capture something via its native APIs.

        Sends {type: "capture_request", id, capability, mode, ...kwargs} over
        the WebSocket and awaits the matching {capture_response} message.

        Returns the response dict (typically {text: "..."} for listen,
        {image: "data:image/png;base64,..."} for see). Raises RuntimeError
        if no browser is connected, asyncio.TimeoutError if no response
        within timeout seconds.
        """
        import uuid

        if not self._clients:
            raise RuntimeError("no browser connected — open the EmptyOS web UI in a browser tab")

        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_captures[request_id] = future

        payload = json.dumps(
            {
                "type": "capture_request",
                "id": request_id,
                "capability": capability,
                "mode": mode,
                **kwargs,
            }
        )

        # Send to all clients; first to respond wins. This handles the case
        # where the user has multiple tabs open without forcing us to track
        # which tab is "active". Browsers ignore captures targeting other
        # capabilities so this is safe.
        #
        # Concurrent + per-send timeout (mirrors _broadcast): a dead TCP peer
        # would otherwise stall send_text for the OS retransmit window (~30s+),
        # blocking the loop and every other capture request behind it.
        async def _send(ws):
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(_send(ws) for ws in list(self._clients.keys())))
        for ws in results:
            if ws is not None:
                self._clients.pop(ws, None)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        finally:
            self._pending_captures.pop(request_id, None)
