"""Regression: Claude CLI must RAISE on rate-limit so the capability chain
falls through to the next provider (openai/ollama). Prior bug: the streaming
path yielded the "You've hit your limit · resets 3pm" text as if it were the
assistant's answer, breaking every app that uses think() or think_stream().
"""

from __future__ import annotations

import asyncio
import pytest

from emptyos.capabilities.providers.claude_cli import (
    ClaudeCLIThinkProvider,
    _looks_like_limit_error,
)


@pytest.mark.parametrize("text,expected", [
    ("You've hit your limit · resets 3pm (Australia/Sydney)", True),
    ("Your 5-hour limit has been reached", True),
    ("Weekly limit · resets next Monday", True),
    ("Quota exceeded for this API key", True),
    ("usage limit reached, resets at 3pm", True),
    ("Hello! How can I help?", False),
    ("The answer is 42.", False),
    ("", False),
    ("   ", False),
])
def test_looks_like_limit_error(text, expected):
    assert _looks_like_limit_error(text) is expected


class _FakeProc:
    """Minimal asyncio.subprocess-like stub."""
    def __init__(self, stdout_bytes: bytes, returncode: int = 0, stderr_bytes: bytes = b""):
        self._stdout = stdout_bytes
        self.returncode = returncode
        self._stderr = stderr_bytes
        self.stderr = _FakeStream(stderr_bytes)
        self.stdout = _FakeStream(stdout_bytes)

    async def wait(self):
        return self.returncode

    def kill(self):
        pass

    async def communicate(self):
        return (self._stdout, self._stderr)


class _FakeStream:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    async def read(self, n: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if n < 0:
            chunk = self._data[self._offset:]
            self._offset = len(self._data)
            return chunk
        chunk = self._data[self._offset:self._offset + n]
        self._offset += len(chunk)
        return chunk

    async def readline(self) -> bytes:
        if self._offset >= len(self._data):
            return b""
        nl = self._data.find(b"\n", self._offset)
        if nl < 0:
            chunk = self._data[self._offset:]
            self._offset = len(self._data)
            return chunk
        chunk = self._data[self._offset:nl + 1]
        self._offset = nl + 1
        return chunk

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await self.read(4096)
        if not chunk:
            raise StopAsyncIteration
        return chunk


@pytest.mark.asyncio
async def test_execute_raises_on_limit_with_exit_zero(monkeypatch):
    """Limit message on stdout with exit 0 must still raise so fallback fires."""
    provider = ClaudeCLIThinkProvider(model="opus")
    provider._claude_path = "/fake/claude"

    limit_msg = b"You've hit your limit \xc2\xb7 resets 3pm (Australia/Sydney)\n"

    async def fake_exec(*args, **kwargs):
        return _FakeProc(limit_msg, returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="usage limit"):
        await provider.execute(prompt="hi")


@pytest.mark.asyncio
async def test_execute_stream_raises_on_limit_before_yielding(monkeypatch):
    """Stream path must NOT yield the limit text as if it were an answer."""
    provider = ClaudeCLIThinkProvider(model="opus")
    provider._claude_path = "/fake/claude"

    limit_msg = b"You've hit your limit \xc2\xb7 resets 3pm\n"

    async def fake_exec(*args, **kwargs):
        return _FakeProc(limit_msg, returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    chunks = []
    with pytest.raises(RuntimeError, match="usage limit"):
        async for chunk in provider.execute_stream(prompt="hi"):
            chunks.append(chunk)

    # Critical: the limit text must not have reached the consumer as content
    joined = "".join(c.get("text", "") for c in chunks)
    assert "hit your limit" not in joined.lower()


@pytest.mark.asyncio
async def test_capability_chain_falls_through_from_claude_to_next(monkeypatch):
    """End-to-end: claude-cli hits limit → capability chain transparently switches
    to the next provider, consumer sees the next provider's answer only, and the
    final chunk carries provider_used naming the actual answerer."""
    from emptyos.capabilities import Capability

    claude = ClaudeCLIThinkProvider(model="opus")
    claude._claude_path = "/fake/claude"

    limit_msg = b"You've hit your limit \xc2\xb7 resets 3pm\n"

    async def fake_exec(*args, **kwargs):
        return _FakeProc(limit_msg, returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    class _StubProvider:
        name = "openai"
        capacity = 0
        _current_load = 0
        is_cloud = False
        at_capacity = False
        async def available(self):
            return True
        async def execute_stream(self, **kw):
            yield {"text": "fallback-answer", "done": False}
            yield {"text": "", "done": True}
        async def execute(self, **kw):
            return "fallback-answer"

    cap = Capability()
    cap.add_provider(claude)
    cap.add_provider(_StubProvider())

    seen = []
    async for chunk in cap.execute_stream(prompt="hi"):
        seen.append(chunk)

    text = "".join(c.get("text", "") for c in seen if "text" in c)
    assert text == "fallback-answer"
    assert "hit your limit" not in text.lower()
    markers = [c["provider_used"] for c in seen if "provider_used" in c]
    assert markers == ["openai"]
