"""NDJSON streaming helpers for web endpoints.

Two apps already stream from think_stream() over NDJSON with different
envelopes (gpts uses {text, done}; speaking uses {type: text|audio|done|error}).
The transport plumbing is shared; the envelope is not — each app composes
its own event dicts and passes them through.

Usage:
    @web_route("POST", "/api/chat/stream")
    async def api_chat_stream(self, request):
        data = await request.json()
        async def gen():
            async for chunk in self.think_stream(data["text"], domain="text"):
                yield {"text": chunk.get("text", ""), "done": bool(chunk.get("done"))}
        return ndjson_response(gen())
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from starlette.responses import StreamingResponse

NDJSON_MEDIA = "application/x-ndjson"


def ndjson_response(gen: AsyncIterator[dict | list]) -> StreamingResponse:
    """Wrap an async generator of JSON-serializable dicts as an NDJSON response.

    Each yielded item is serialized as one line (``json.dumps(item) + "\\n"``)
    and sent with ``Content-Type: application/x-ndjson``. Non-dict/list items
    are silently skipped so a caller can ``yield None`` as a no-op without
    corrupting the stream.
    """
    async def _lines():
        async for ev in gen:
            if not isinstance(ev, (dict, list)):
                continue
            yield json.dumps(ev, ensure_ascii=False) + "\n"
    return StreamingResponse(_lines(), media_type=NDJSON_MEDIA)
