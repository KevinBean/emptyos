"""Regression: openai_compat streaming must RAISE when upstream returns 200
with an empty body / no content chunks. Otherwise the capability chain never
falls through and the consumer sees a silent empty response.

Covers both paths:
- `execute_stream` (OpenAI-compat SSE)
- `_stream_ollama_native` (Ollama native NDJSON)
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


def _patch_session(monkeypatch, module_path, lines):
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
async def test_openai_stream_raises_on_empty_sse(monkeypatch):
    """HTTP 200 with zero SSE data lines → must raise so fallback triggers."""
    _patch_session(monkeypatch, "openai_compat", lines=[])

    provider = OpenAICompatThinkProvider(
        host="https://api.openai.com",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        provider_name="openai",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="no content"):
        async for _ in provider.execute_stream(prompt="hi"):
            pass


@pytest.mark.asyncio
async def test_openai_stream_raises_when_only_done_no_content(monkeypatch):
    """[DONE] marker with zero content deltas → still raises? No — [DONE] means
    upstream explicitly finished. It's empty but not broken. Should NOT raise.
    This test locks in that behavior."""
    _patch_session(monkeypatch, "openai_compat", lines=["data: [DONE]\n"])

    provider = OpenAICompatThinkProvider(
        host="https://api.openai.com",
        model="gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        provider_name="openai",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    chunks = []
    async for c in provider.execute_stream(prompt="hi"):
        chunks.append(c)
    # Terminal chunk only, no exception
    assert any(c.get("done") is True for c in chunks)


@pytest.mark.asyncio
async def test_openai_stream_yields_normal_content(monkeypatch):
    """Sanity: normal SSE stream still works end-to-end."""
    sse = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}\n',
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
        "data: [DONE]\n",
    ]
    _patch_session(monkeypatch, "openai_compat", lines=sse)

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


@pytest.mark.asyncio
async def test_ollama_native_stream_raises_on_empty(monkeypatch):
    """Native Ollama NDJSON path: zero lines → must raise."""
    _patch_session(monkeypatch, "openai_compat", lines=[])

    provider = OpenAICompatThinkProvider(
        host="http://localhost:11434",
        model="qwen3.5:latest",
        api_key_env="",
        provider_name="ollama",
    )

    with pytest.raises(RuntimeError, match="no content"):
        async for _ in provider.execute_stream(prompt="hi"):
            pass
