"""Regression: openai_compat streaming must RAISE when the SSE / NDJSON
stream closes WITHOUT a completion marker, even when some content already
arrived.

Friction: `/research <query>` in the assistant completed the whole pipeline
(search -> read -> synthesize -> citations) but the persisted answer was
just a lone ``#`` heading with the body missing. The synthesis stream was
truncated mid-generation (connection dropped after the first content chunk,
before the upstream ``[DONE]`` / ``{"done": true}`` marker), and the guard
only raised when content was ALSO completely empty — so a partial reply
sailed through as if the model had legitimately finished.
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider


class _FakeResp:
    """aiohttp.ClientResponse-ish stub for streaming."""

    def __init__(self, lines, status=200):
        self._lines = lines
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            import aiohttp

            raise aiohttp.ClientResponseError(None, (), status=self.status, message="err")

    @property
    def content(self):
        async def gen():
            for line in self._lines:
                yield line if isinstance(line, bytes) else line.encode("utf-8")

        return gen()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def post(self, *a, **kw):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_session(monkeypatch, lines):
    import emptyos.capabilities.providers.openai_compat as mod

    class _Ctor:
        def __init__(self, *a, **kw):
            self._resp = _FakeResp(lines)

        async def __aenter__(self):
            return _FakeSession(self._resp)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(mod.aiohttp, "ClientSession", _Ctor)


@pytest.mark.asyncio
async def test_openai_stream_raises_when_content_but_no_done(monkeypatch):
    """A stream that yields real content, then closes with no [DONE]
    sentinel, is a TRUNCATED reply — not a complete one. Must raise so a
    caller (or the capability chain) never mistakes a cut-off answer for a
    finished one."""
    sse = [
        'data: {"choices":[{"delta":{"content":"#"}}]}\n',
        # connection drops here — no further data, no [DONE]
    ]
    _patch_session(monkeypatch, sse)

    provider = OpenAICompatThinkProvider(
        host="https://api.openai.com",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        provider_name="openai",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    chunks = []
    with pytest.raises(RuntimeError, match="completion marker"):
        async for c in provider.execute_stream(prompt="hi"):
            chunks.append(c)

    # The truncated chunk still arrives before the raise — the bug was that
    # this was ALL a caller ever saw, silently reported as a finished reply.
    assert any(c.get("text") == "#" for c in chunks)


@pytest.mark.asyncio
async def test_ollama_native_stream_raises_when_content_but_no_done(monkeypatch):
    """Same class of bug on the native-Ollama NDJSON streaming path."""
    ndjson = [
        '{"message":{"content":"#"}}\n',
        # connection drops here — no further lines, no {"done": true}
    ]
    _patch_session(monkeypatch, ndjson)

    provider = OpenAICompatThinkProvider(
        host="http://localhost:11434",
        model="qwen3.5:latest",
        api_key_env="",
        provider_name="ollama",
    )

    chunks = []
    with pytest.raises(RuntimeError, match="completion marker"):
        async for c in provider._stream_ollama_native(prompt="hi"):
            chunks.append(c)

    assert any(c.get("text") == "#" for c in chunks)


@pytest.mark.asyncio
async def test_openai_stream_with_done_marker_still_succeeds(monkeypatch):
    """Sanity: a normal, complete stream (ends with [DONE]) is unaffected."""
    sse = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}\n',
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
        "data: [DONE]\n",
    ]
    _patch_session(monkeypatch, sse)

    provider = OpenAICompatThinkProvider(
        host="https://api.openai.com",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        provider_name="openai",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    text = ""
    async for c in provider.execute_stream(prompt="hi"):
        if "text" in c and c.get("text"):
            text += c["text"]
    assert text == "hello"
