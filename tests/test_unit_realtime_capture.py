"""Unit tests for realtime request_capture send path + vault_watcher scan.

Pins the async-wedge fixes:
- request_capture() sends to clients concurrently with a per-send timeout
  (mirrors _broadcast), so a dead peer can't stall the loop ~30s and broken
  peers are pruned.
- vault_watcher._scan_mtimes is a pure blocking helper (offloaded via to_thread
  in _poll_loop); test it directly.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from emptyos.runtime.realtime import RealtimeManager
from emptyos.runtime.vault_watcher import VaultWatcher


class _FakeWS:
    def __init__(self, *, delay=0.0, raises=False):
        self.delay = delay
        self.raises = raises
        self.sent = []

    async def send_text(self, payload):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise ConnectionError("broken pipe")
        self.sent.append(payload)


def test_request_capture_sends_concurrently_and_prunes_broken():
    mgr = RealtimeManager(kernel=None)
    live = _FakeWS(delay=0.4)
    live2 = _FakeWS(delay=0.4)
    broken = _FakeWS(raises=True)
    for ws in (live, live2, broken):
        mgr._clients[ws] = set()

    async def run():
        t0 = time.perf_counter()
        # tiny capture timeout — no browser will answer, so it times out fast;
        # we only care about the SEND behaviour before the await.
        with pytest.raises(asyncio.TimeoutError):
            await mgr.request_capture(capability="listen", mode="x", timeout=0.01)
        return time.perf_counter() - t0

    elapsed = asyncio.run(run())
    # Concurrent: two 0.4s sends overlap (~0.4s), not serial (~0.8s+).
    assert elapsed < 0.7, f"sends look serial: {elapsed:.2f}s"
    # Both live peers received the payload; broken peer was pruned.
    assert live.sent and live2.sent
    assert broken not in mgr._clients
    assert live in mgr._clients and live2 in mgr._clients


def test_request_capture_no_clients_raises():
    mgr = RealtimeManager(kernel=None)

    async def run():
        with pytest.raises(RuntimeError):
            await mgr.request_capture(capability="see", mode="x", timeout=0.01)

    asyncio.run(run())


def test_scan_mtimes(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("y", encoding="utf-8")
    (tmp_path / "c.txt").write_text("z", encoding="utf-8")  # not .md → excluded

    out = VaultWatcher._scan_mtimes(tmp_path)
    names = {p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in out}
    assert "a.md" in names and "b.md" in names
    assert "c.txt" not in names
    assert all(isinstance(v, float) for v in out.values())
