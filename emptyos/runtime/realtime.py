"""Real-time service — EventBus to WebSocket bridge.

Pushes kernel events to connected browser clients.
Clients can subscribe to specific event types.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


BROWSER_PROTOCOL = 1
BROWSER_SESSION_TTL_S = 60 * 60
BROWSER_RECONNECT_GRACE_S = 30
MAX_BROWSER_RESULT_BYTES = 6 * 1024 * 1024
BROWSER_ACTIONS = frozenset({
    "list_tabs", "snapshot", "screenshot", "wait_for", "open_tab", "focus_tab",
    "navigate", "back", "forward", "reload", "close", "click", "fill", "press",
    "select", "scroll", "show_card", "show_badge", "show_toast",
})


@dataclass
class BrowserClient:
    client_id: str
    websocket: WebSocket
    extension_version: str = ""
    commands: set[str] = field(default_factory=set)
    session_id: str = ""


@dataclass
class BrowserSession:
    session_id: str
    client_id: str
    tabs: dict[int, dict]
    expires_at: float
    websocket: WebSocket | None
    disconnected_at: float | None = None


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
        self._browser_clients: dict[WebSocket, BrowserClient] = {}
        self._browser_sessions: dict[str, BrowserSession] = {}
        self._pending_browser: dict[str, tuple[str, asyncio.Future]] = {}
        self._browser_disconnect_tasks: dict[str, asyncio.Task] = {}

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
        for task in self._browser_disconnect_tasks.values():
            task.cancel()
        self._browser_disconnect_tasks.clear()
        for _rid, (_sid, future) in list(self._pending_browser.items()):
            if not future.done():
                future.set_exception(RuntimeError("browser session disconnected"))
        self._pending_browser.clear()
        self._browser_clients.clear()
        self._browser_sessions.clear()

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
                    elif str(msg.get("type", "")).startswith("browser."):
                        await self._handle_browser_message(ws, msg)
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self._clients.pop(ws, None)
            client = self._browser_clients.pop(ws, None)
            if client and client.session_id:
                session = self._browser_sessions.get(client.session_id)
                if session and session.websocket is ws:
                    session.websocket = None
                    session.disconnected_at = time.monotonic()
                    task = asyncio.create_task(self._disarm_after_grace(session.session_id))
                    self._browser_disconnect_tasks[session.session_id] = task

    def browser_session_enabled(self) -> bool:
        if self.kernel is None:
            return True
        try:
            return bool(self.kernel.config.get("apps.agent.feature.browser-session.enabled", False))
        except Exception:
            return False

    @staticmethod
    def _safe_tabs(value) -> dict[int, dict]:
        tabs: dict[int, dict] = {}
        if not isinstance(value, list) or len(value) > 64:
            return tabs
        for raw in value:
            if not isinstance(raw, dict) or not isinstance(raw.get("tab_id"), int):
                continue
            tab_id = raw["tab_id"]
            url = str(raw.get("url") or "")[:2048]
            if not url.startswith(("http://", "https://")):
                continue
            tabs[tab_id] = {"tab_id": tab_id, "url": url, "title": str(raw.get("title") or "")[:300]}
        return tabs

    async def _send_browser(self, ws: WebSocket, payload: dict) -> None:
        await asyncio.wait_for(ws.send_text(json.dumps(payload)), timeout=2.0)

    async def _audit_browser(self, event: str, **data) -> None:
        """Emit metadata-only audit events; never include page or input bodies."""
        if self.kernel is None:
            return
        try:
            await self.kernel.events.emit(
                "browser:session",
                {"event": event, **data},
                source="realtime",
            )
        except Exception:
            pass

    async def _handle_browser_message(self, ws: WebSocket, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "browser.hello":
            protocol = msg.get("protocol")
            client_id = str(msg.get("client_id") or "")[:128]
            if protocol != BROWSER_PROTOCOL or not client_id:
                await self._send_browser(ws, {"type": "browser.ready", "protocol": BROWSER_PROTOCOL, "enabled": False, "error": "protocol_mismatch"})
                return
            commands = {str(x) for x in (msg.get("commands") or []) if str(x) in BROWSER_ACTIONS}
            client = BrowserClient(client_id, ws, str(msg.get("extension_version") or "")[:64], commands)
            self._browser_clients[ws] = client
            resumed = ""
            resume_id = str(msg.get("session_id") or "")
            session = self._browser_sessions.get(resume_id)
            if session and session.client_id == client_id and session.expires_at > time.time():
                session.websocket = ws
                session.disconnected_at = None
                client.session_id = session.session_id
                resumed = session.session_id
                task = self._browser_disconnect_tasks.pop(session.session_id, None)
                if task:
                    task.cancel()
            await self._send_browser(ws, {"type": "browser.ready", "protocol": BROWSER_PROTOCOL, "enabled": self.browser_session_enabled(), "session_id": resumed})
            return

        client = self._browser_clients.get(ws)
        if not client:
            return
        if kind == "browser.ping":
            if client.session_id:
                session = self._browser_sessions.get(client.session_id)
                if session and session.expires_at <= time.time():
                    await self._disarm(session.session_id, "expired")
                    return
                if session and session.expires_at - time.time() <= 300:
                    await self._send_browser(ws, {"type": "browser.session_expiring", "protocol": BROWSER_PROTOCOL, "session_id": session.session_id, "expires_at": session.expires_at})
            await self._send_browser(ws, {"type": "browser.pong", "protocol": BROWSER_PROTOCOL})
            return
        if kind == "browser.arm":
            if not self.browser_session_enabled():
                await self._send_browser(ws, {"type": "browser.disarmed", "protocol": BROWSER_PROTOCOL, "reason": "disabled"})
                return
            tabs = self._safe_tabs(msg.get("tabs"))
            if not tabs:
                await self._send_browser(ws, {"type": "browser.disarmed", "protocol": BROWSER_PROTOCOL, "reason": "no_shared_tabs"})
                return
            if client.session_id:
                await self._disarm(client.session_id, "rearmed")
            sid = uuid.uuid4().hex
            session = BrowserSession(sid, client.client_id, tabs, time.time() + BROWSER_SESSION_TTL_S, ws)
            self._browser_sessions[sid] = session
            client.session_id = sid
            await self._audit_browser(
                "armed", session_id=sid, client_id=client.client_id,
                hosts=sorted({urlsplit(tab["url"]).hostname or "" for tab in tabs.values()}),
            )
            await self._send_browser(ws, {"type": "browser.armed", "protocol": BROWSER_PROTOCOL, "session_id": sid, "expires_at": session.expires_at, "tabs": list(tabs.values())})
            return
        sid = str(msg.get("session_id") or "")
        session = self._browser_sessions.get(sid)
        if not session or session.client_id != client.client_id:
            return
        if session.expires_at <= time.time():
            await self._disarm(sid, "expired")
            return
        if kind == "browser.tabs":
            tabs = self._safe_tabs(msg.get("tabs"))
            if tabs:
                session.tabs = tabs
            return
        if kind == "browser.extend":
            session.expires_at = time.time() + BROWSER_SESSION_TTL_S
            await self._send_browser(ws, {"type": "browser.armed", "protocol": BROWSER_PROTOCOL, "session_id": sid, "expires_at": session.expires_at, "tabs": list(session.tabs.values())})
            return
        if kind == "browser.disarm":
            await self._disarm(sid, "user")
            return
        if kind == "browser.event":
            name = str(msg.get("event") or "")[:64]
            if name:
                await self._audit_browser("extension_event", session_id=sid, name=name)
            return
        if kind == "browser.result":
            rid = str(msg.get("request_id") or "")
            pending = self._pending_browser.get(rid)
            if pending and pending[0] == sid and not pending[1].done():
                if len(json.dumps(msg, default=str).encode("utf-8")) > MAX_BROWSER_RESULT_BYTES:
                    msg = {"type": "browser.result", "session_id": sid, "request_id": rid, "ok": False, "error": "payload_too_large"}
                pending[1].set_result(msg)

    async def _disarm_after_grace(self, session_id: str) -> None:
        try:
            await asyncio.sleep(BROWSER_RECONNECT_GRACE_S)
            session = self._browser_sessions.get(session_id)
            if session and session.websocket is None:
                await self._disarm(session_id, "disconnected")
        except asyncio.CancelledError:
            pass
        finally:
            self._browser_disconnect_tasks.pop(session_id, None)

    async def _disarm(self, session_id: str, reason: str) -> None:
        session = self._browser_sessions.pop(session_id, None)
        if not session:
            return
        task = self._browser_disconnect_tasks.pop(session_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        for client in self._browser_clients.values():
            if client.session_id == session_id:
                client.session_id = ""
        for _rid, (sid, future) in list(self._pending_browser.items()):
            if sid == session_id and not future.done():
                future.set_exception(RuntimeError(f"browser session {reason}"))
        if session.websocket:
            try:
                await self._send_browser(session.websocket, {"type": "browser.disarmed", "protocol": BROWSER_PROTOCOL, "session_id": session_id, "reason": reason})
            except Exception:
                pass
        await self._audit_browser("disarmed", session_id=session_id, reason=reason)

    def active_browser_session(self, session_id: str = "") -> BrowserSession | None:
        candidates = [self._browser_sessions.get(session_id)] if session_id else list(self._browser_sessions.values())
        now = time.time()
        return next((s for s in candidates if s and s.expires_at > now and s.websocket), None)

    async def request_browser_action(self, action: str, *, session_id: str = "", tab_id: int = 0, timeout: float = 30.0, **args) -> dict:
        if action not in BROWSER_ACTIONS:
            raise RuntimeError("unsupported_action")
        session = self.active_browser_session(session_id)
        if not session:
            raise RuntimeError("browser_session_unavailable")
        client = self._browser_clients.get(session.websocket)
        if not client or action not in client.commands:
            raise RuntimeError("unsupported_action")
        if action not in {"list_tabs", "open_tab"} and tab_id not in session.tabs:
            raise RuntimeError("tab_not_shared")
        if len(json.dumps(args, default=str)) > 32_000:
            raise RuntimeError("payload_too_large")
        rid = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending_browser[rid] = (session.session_id, future)
        host = ""
        if tab_id in session.tabs:
            host = urlsplit(session.tabs[tab_id].get("url", "")).hostname or ""
        try:
            await self._send_browser(session.websocket, {"type": "browser.command", "protocol": BROWSER_PROTOCOL, "request_id": rid, "session_id": session.session_id, "action": action, "tab_id": int(tab_id or 0), "args": args, "deadline": time.time() + min(max(float(timeout), 0.1), 60.0)})
            response = await asyncio.wait_for(future, timeout=min(timeout, 60.0))
        except asyncio.TimeoutError:
            await self._audit_browser("action", session_id=session.session_id, request_id=rid, action=action, host=host, outcome="deadline_exceeded", confirmed=False)
            raise RuntimeError("deadline_exceeded") from None
        finally:
            self._pending_browser.pop(rid, None)
        if not response.get("ok"):
            error = str(response.get("error") or "browser_command_failed")[:64]
            await self._audit_browser("action", session_id=session.session_id, request_id=rid, action=action, host=host, outcome=error, confirmed=False)
            detail = response.get("detail") if isinstance(response.get("detail"), dict) else {}
            if error == "permission_required" and detail.get("origin"):
                raise RuntimeError(f"permission_required:{str(detail['origin'])[:2048]}")
            raise RuntimeError(error)
        result = response.get("result") or {"ok": True}
        await self._audit_browser("action", session_id=session.session_id, request_id=rid, action=action, host=host, outcome="ok", confirmed=bool(result.get("confirmed")))
        return result

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
