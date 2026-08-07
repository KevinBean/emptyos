"""Unit test for `emptyos.cli.chat._stream_ndjson`.

The NDJSON parser is the load-bearing piece of the eos chat REPL — it
buffers raw bytes from `response.content.iter_any()` and yields parsed
dicts line-by-line. Boundary cases worth pinning: a single line split
across two chunks, multiple lines in one chunk, a tail without a
trailing newline, and malformed JSON lines being skipped.

The test mocks aiohttp's response object with a minimal stand-in — no
network, no daemon, no event loop coupling.
"""

from __future__ import annotations

import pytest

from emptyos.cli.chat import _stream_ndjson


class _FakeContent:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_any(self):
        for c in self._chunks:
            yield c


class _FakeResp:
    def __init__(self, status: int, chunks: list[bytes], text: str = ""):
        self.status = status
        self.content = _FakeContent(chunks)
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    def post(self, url, json=None):
        return self._resp


async def _collect(stream):
    out = []
    async for chunk in stream:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_single_chunk_multiple_lines():
    """Three complete JSON lines arrive in one chunk."""
    body = b'{"text":"a","done":false}\n{"text":"b","done":false}\n{"done":true}\n'
    session = _FakeSession(_FakeResp(200, [body]))
    out = await _collect(_stream_ndjson(session, "/x", {}))
    assert out == [
        {"text": "a", "done": False},
        {"text": "b", "done": False},
        {"done": True},
    ]


@pytest.mark.asyncio
async def test_line_split_across_chunks():
    """A single JSON object is split across two chunk boundaries."""
    chunks = [b'{"text":"hel', b'lo","do', b'ne":false}\n']
    session = _FakeSession(_FakeResp(200, chunks))
    out = await _collect(_stream_ndjson(session, "/x", {}))
    assert out == [{"text": "hello", "done": False}]


@pytest.mark.asyncio
async def test_tail_without_trailing_newline():
    """Last JSON object has no trailing \\n — must still be yielded."""
    chunks = [b'{"a":1}\n', b'{"b":2}']  # second has no \n
    session = _FakeSession(_FakeResp(200, chunks))
    out = await _collect(_stream_ndjson(session, "/x", {}))
    assert out == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_blank_lines_skipped():
    """Blank lines between objects are skipped, not yielded as None."""
    chunks = [b'{"a":1}\n\n\n{"b":2}\n']
    session = _FakeSession(_FakeResp(200, chunks))
    out = await _collect(_stream_ndjson(session, "/x", {}))
    assert out == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_malformed_line_skipped():
    """Garbage between valid lines doesn't crash the stream."""
    chunks = [b'{"a":1}\nNOT JSON\n{"b":2}\n']
    session = _FakeSession(_FakeResp(200, chunks))
    out = await _collect(_stream_ndjson(session, "/x", {}))
    assert out == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
async def test_non_200_raises():
    """Non-200 status raises RuntimeError with the body text."""
    session = _FakeSession(_FakeResp(500, [], text="boom"))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await _collect(_stream_ndjson(session, "/x", {}))
