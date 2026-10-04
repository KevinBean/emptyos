"""An outer cancellation must reap the claude subprocess, not just abandon it.

`ClaudeCLIThinkProvider.execute` already killed the child on its OWN timeout
(`asyncio.wait_for(proc.communicate(), self.timeout)`), but a caller's budget —
the hub's per-panel `resolve_panels(timeout_s=…)`, or any `wait_for` wrapped
around `think()` — arrives inside the provider as `CancelledError`, not
`TimeoutError`. Until 2026-09-12 that path released the semaphore and left the
`claude` process running until its stdout pipe filled.

Offline: the subprocess is a stub whose `communicate()` blocks until cancelled.
"""

from __future__ import annotations

import asyncio

import pytest

from emptyos.capabilities.providers.claude_cli import ClaudeCLIThinkProvider


class _HangingProc:
    """communicate() never returns on its own; kill() is recorded."""

    def __init__(self):
        self.killed = False
        self.returncode = None
        self._block = asyncio.Event()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._block.set()

    async def communicate(self, input=None):
        if self.killed:
            return (b"", b"")
        await self._block.wait()
        return (b"", b"")

    async def wait(self):
        await self._block.wait()
        return self.returncode


@pytest.mark.asyncio
async def test_outer_cancel_kills_the_child(monkeypatch):
    provider = ClaudeCLIThinkProvider(model="opus")
    provider._claude_path = "/fake/claude"
    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(provider.execute(prompt="hi"))
    await asyncio.sleep(0.05)          # let it reach communicate()
    assert not proc.killed
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed, "cancelled think left the claude subprocess running"


@pytest.mark.asyncio
async def test_budget_wait_for_around_execute_reaps(monkeypatch):
    """The real caller shape: a wait_for budget around the provider call."""
    provider = ClaudeCLIThinkProvider(model="opus")
    provider._claude_path = "/fake/claude"
    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider.execute(prompt="hi"), 0.05)
    assert proc.killed
