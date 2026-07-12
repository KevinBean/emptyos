"""Unit tests for emptyos.sdk.streaming — ndjson_response helper.

Pure in-process: no daemon, no network. Drains the StreamingResponse's
body_iterator and checks both the serialization format and the filter that
drops non-dict/list items.
"""

from __future__ import annotations

import json

import pytest

from emptyos.sdk.streaming import NDJSON_MEDIA, ndjson_response


async def _drain(resp) -> list[str]:
    """Collect the streamed body as decoded string lines."""
    out: list[str] = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, (bytes, bytearray)):
            chunk = chunk.decode("utf-8")
        out.append(chunk)
    # Split on newlines because each yielded chunk is one JSON line + \n
    joined = "".join(out)
    return [ln for ln in joined.split("\n") if ln]


@pytest.mark.asyncio
async def test_ndjson_response_serializes_each_dict_as_one_line():
    async def gen():
        yield {"type": "text", "delta": "hi"}
        yield {"type": "audio", "url": "/foo.mp3"}
        yield {"type": "done"}

    resp = ndjson_response(gen())
    assert resp.media_type == NDJSON_MEDIA

    lines = await _drain(resp)
    assert len(lines) == 3
    assert [json.loads(ln) for ln in lines] == [
        {"type": "text", "delta": "hi"},
        {"type": "audio", "url": "/foo.mp3"},
        {"type": "done"},
    ]


@pytest.mark.asyncio
async def test_ndjson_response_skips_non_dict_items():
    """Callers can yield None as a no-op without corrupting the stream."""
    async def gen():
        yield {"type": "text", "delta": "a"}
        yield None
        yield "not a dict"
        yield {"type": "text", "delta": "b"}

    resp = ndjson_response(gen())
    lines = await _drain(resp)
    assert [json.loads(ln) for ln in lines] == [
        {"type": "text", "delta": "a"},
        {"type": "text", "delta": "b"},
    ]


@pytest.mark.asyncio
async def test_ndjson_response_preserves_unicode():
    async def gen():
        yield {"text": "你好 — Emma"}

    resp = ndjson_response(gen())
    lines = await _drain(resp)
    # ensure_ascii=False, so non-ASCII chars stay literal
    assert lines == ['{"text": "你好 — Emma"}']


@pytest.mark.asyncio
async def test_ndjson_response_empty_generator_yields_nothing():
    async def gen():
        if False:
            yield {}  # pragma: no cover

    resp = ndjson_response(gen())
    lines = await _drain(resp)
    assert lines == []


@pytest.mark.asyncio
async def test_ndjson_response_list_items_ok():
    """Top-level lists are valid NDJSON and should pass through."""
    async def gen():
        yield [1, 2, 3]
        yield {"ok": True}

    resp = ndjson_response(gen())
    lines = await _drain(resp)
    assert [json.loads(ln) for ln in lines] == [[1, 2, 3], {"ok": True}]
