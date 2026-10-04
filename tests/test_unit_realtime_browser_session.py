from __future__ import annotations

import asyncio
import json

import pytest

import emptyos.runtime.realtime as realtime_module
from emptyos.runtime.realtime import BROWSER_ACTIONS, BROWSER_PROTOCOL, RealtimeManager


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, payload: str):
        self.sent.append(json.loads(payload))


def test_browser_session_directed_round_trip_and_duplicate_ignored():
    async def run():
        manager = RealtimeManager(kernel=None)
        ws = FakeWS()
        await manager._handle_browser_message(ws, {
            "type": "browser.hello", "protocol": BROWSER_PROTOCOL,
            "client_id": "extension-1", "extension_version": "0.11.0",
            "commands": sorted(BROWSER_ACTIONS),
        })
        await manager._handle_browser_message(ws, {
            "type": "browser.arm", "tabs": [{"tab_id": 7, "url": "https://example.com", "title": "Example"}],
        })
        sid = ws.sent[-1]["session_id"]
        task = asyncio.create_task(manager.request_browser_action("snapshot", session_id=sid, tab_id=7))
        await asyncio.sleep(0)
        command = ws.sent[-1]
        assert command["type"] == "browser.command"
        assert command["session_id"] == sid and command["tab_id"] == 7
        result = {"type": "browser.result", "session_id": sid, "request_id": command["request_id"], "ok": True, "result": {"text": "safe"}}
        await manager._handle_browser_message(ws, result)
        await manager._handle_browser_message(ws, result)
        assert await task == {"text": "safe"}

    asyncio.run(run())


def test_browser_session_rejects_unshared_tab_and_unsupported_action():
    async def run():
        manager = RealtimeManager(kernel=None)
        ws = FakeWS()
        await manager._handle_browser_message(ws, {"type": "browser.hello", "protocol": 1, "client_id": "c", "commands": sorted(BROWSER_ACTIONS)})
        await manager._handle_browser_message(ws, {"type": "browser.arm", "tabs": [{"tab_id": 1, "url": "https://example.com"}]})
        sid = ws.sent[-1]["session_id"]
        with pytest.raises(RuntimeError, match="tab_not_shared"):
            await manager.request_browser_action("snapshot", session_id=sid, tab_id=2)
        with pytest.raises(RuntimeError, match="unsupported_action"):
            await manager.request_browser_action("eval", session_id=sid, tab_id=1)

    asyncio.run(run())


def test_browser_session_protocol_mismatch_and_no_tabs_fail_closed():
    async def run():
        manager = RealtimeManager(kernel=None)
        ws = FakeWS()
        await manager._handle_browser_message(ws, {"type": "browser.hello", "protocol": 99, "client_id": "c"})
        assert ws.sent[-1]["error"] == "protocol_mismatch"
        ws2 = FakeWS()
        await manager._handle_browser_message(ws2, {"type": "browser.hello", "protocol": 1, "client_id": "c2", "commands": []})
        await manager._handle_browser_message(ws2, {"type": "browser.arm", "tabs": []})
        assert ws2.sent[-1]["reason"] == "no_shared_tabs"

    asyncio.run(run())


def test_browser_session_timeout_wrong_session_and_expiry():
    async def run():
        manager = RealtimeManager(kernel=None)
        ws = FakeWS()
        await manager._handle_browser_message(ws, {"type": "browser.hello", "protocol": 1, "client_id": "c", "commands": sorted(BROWSER_ACTIONS)})
        await manager._handle_browser_message(ws, {"type": "browser.arm", "tabs": [{"tab_id": 1, "url": "https://example.com"}]})
        sid = ws.sent[-1]["session_id"]
        task = asyncio.create_task(manager.request_browser_action("snapshot", session_id=sid, tab_id=1, timeout=0.01))
        await asyncio.sleep(0)
        command = ws.sent[-1]
        await manager._handle_browser_message(ws, {"type": "browser.result", "session_id": "wrong", "request_id": command["request_id"], "ok": True, "result": {}})
        with pytest.raises(RuntimeError, match="deadline_exceeded"):
            await task
        manager._browser_sessions[sid].expires_at = 0
        await manager._handle_browser_message(ws, {"type": "browser.ping", "session_id": sid})
        assert sid not in manager._browser_sessions
        assert ws.sent[-1]["reason"] == "expired"

    asyncio.run(run())


def test_browser_session_resume_within_grace_then_disconnect(monkeypatch):
    async def run():
        monkeypatch.setattr(realtime_module, "BROWSER_RECONNECT_GRACE_S", 0.01)
        manager = RealtimeManager(kernel=None)
        ws1 = FakeWS()
        await manager._handle_browser_message(ws1, {"type": "browser.hello", "protocol": 1, "client_id": "stable", "commands": sorted(BROWSER_ACTIONS)})
        await manager._handle_browser_message(ws1, {"type": "browser.arm", "tabs": [{"tab_id": 3, "url": "https://example.com"}]})
        sid = ws1.sent[-1]["session_id"]
        session = manager._browser_sessions[sid]
        session.websocket = None
        session.disconnected_at = 1
        manager._browser_disconnect_tasks[sid] = asyncio.create_task(manager._disarm_after_grace(sid))
        ws2 = FakeWS()
        await manager._handle_browser_message(ws2, {"type": "browser.hello", "protocol": 1, "client_id": "stable", "session_id": sid, "commands": sorted(BROWSER_ACTIONS)})
        assert ws2.sent[-1]["session_id"] == sid
        assert manager._browser_sessions[sid].websocket is ws2
        session.websocket = None
        await manager._disarm_after_grace(sid)
        assert sid not in manager._browser_sessions

    asyncio.run(run())
