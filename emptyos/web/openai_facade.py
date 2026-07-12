"""OpenAI-compatible Chat Completions facade.

Exposes EmptyOS's ``think`` capability as ``/v1/chat/completions`` so any
LiteLLM-based tool (CodeBoarding, Continue.dev, Cursor, custom agents) can
point at the daemon and inherit the full provider chain: claude-cli first
(free via Max subscription), ollama fallback, openai-mini, etc. — plus the
cloud consent gate and any registered middlewares (Headroom compression,
future redaction).

Architecture:

    LiteLLM-based tool
        ↓ Authorization: Bearer <emptyos_auth_token>
        ↓ POST /v1/chat/completions {messages: [...], model: ...}
    Auth middleware (existing — same gate as the rest of the daemon)
        ↓
    Facade route
        ↓ kernel.capability("think").execute(messages=[...], temperature=...)
    Capability runtime
        ↓ provider chain (claude-cli → ollama → openai-mini → ...)
        ↓ + middleware (Headroom compress, ...)
        ↓ + consent gate
    Response wrapped in OpenAI Chat Completions shape

What's NOT covered (yet):

- **Streaming (SSE)**: most batch tools (CodeBoarding included) call with
  ``stream=false``. When a real consumer needs streaming, wire it through
  ``execute_stream()`` returning SSE chunks.
- **Function/tool calling**: out of scope until a consumer needs it.
- **Multimodal**: text-only for now.
- **Real token counting**: ``usage`` returns zeros. Tools that bill by
  token will under-count; cost tracking lives in the billing app, not
  the facade.

The model name in the request is ignored for provider selection (the chain
order decides). It's echoed back in the response's ``model`` field so
client-side logging looks correct, and exposed via a ``_eos_provider``
extension field so callers can see which provider actually fulfilled the
call.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def register_routes(server, kernel):
    """Mount the facade routes on the FastAPI server. Idempotent at register time."""

    @server.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"message": "invalid JSON body", "type": "invalid_request"}},
                status_code=400,
            )

        if not isinstance(body, dict):
            return JSONResponse(
                {"error": {"message": "body must be an object", "type": "invalid_request"}},
                status_code=400,
            )

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return JSONResponse(
                {
                    "error": {
                        "message": "`messages` must be a non-empty array",
                        "type": "invalid_request",
                    }
                },
                status_code=400,
            )

        model_name_echo = body.get("model", "eos-default")
        temperature = body.get("temperature")
        max_tokens = body.get("max_tokens")
        stream = bool(body.get("stream", False))

        if stream:
            # SSE not implemented yet — tools that genuinely require streaming
            # should fall back to non-streaming. Returning 501 instead of
            # silently dropping ``stream`` so the caller knows.
            return JSONResponse(
                {
                    "error": {
                        "message": "streaming not supported by this facade; retry with stream=false",
                        "type": "unsupported",
                    }
                },
                status_code=501,
            )

        # ThinkCapability.execute requires `prompt` (and accepts `system`)
        # as keyword args. We satisfy the signature by extracting a leading
        # system message and using the last user turn as prompt — but we
        # ALSO pass the full `messages` array through kwargs so the
        # openai_compat provider's _chat_messages() can use the full multi-
        # turn history if present (it prefers `messages` over `prompt`).
        system_msg = ""
        prompt_text = ""
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content", "")
            if not isinstance(content, str):
                # Multimodal content arrays — flatten the text parts so
                # the prompt-shape fallback path stays sensible.
                try:
                    content = " ".join(
                        p.get("text", "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                except Exception:
                    content = ""
            if role == "system" and not system_msg:
                system_msg = content
            elif role == "user":
                prompt_text = content  # last user turn wins for the prompt fallback
        if not prompt_text:
            # No user turn at all — synthesize a no-op so the signature is
            # satisfied. The provider's messages array still has the real
            # conversation.
            prompt_text = ""

        think_kwargs: dict[str, Any] = {
            "prompt": prompt_text,
            "system": system_msg,
            "messages": messages,
        }
        if temperature is not None:
            think_kwargs["temperature"] = float(temperature)
        if max_tokens is not None:
            try:
                think_kwargs["max_tokens"] = int(max_tokens)
            except (TypeError, ValueError):
                pass

        try:
            result = await kernel.capability("think").execute(**think_kwargs)
        except RuntimeError as e:
            # Standard shape when the capability chain has no available
            # provider (cloud consent denied, all providers offline, etc.).
            return JSONResponse(
                {"error": {"message": str(e), "type": "service_unavailable"}},
                status_code=503,
            )
        except Exception as e:  # noqa: BLE001 — surface unexpected errors as 500
            return JSONResponse(
                {"error": {"message": f"think failed: {e!r}", "type": "internal_error"}},
                status_code=500,
            )

        # The think capability may return either a raw string or a richer
        # object (e.g. ToolCapableProvider's AgentTurn). Coerce to string for
        # the response body — tools speaking plain Chat Completions just want
        # text content. Tool-use cases would need a different facade endpoint.
        raw = getattr(result, "value", "")
        content = raw if isinstance(raw, str) else (
            str(getattr(raw, "text", "") or raw or "")
        )

        return {
            "id": f"chatcmpl-eos-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name_echo,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                # Real token counts would require running a tokenizer on
                # both prompt and completion. Tools that bill by token will
                # see zeros — the billing app is the source of truth.
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            # Extension fields (OpenAI ignores unknown fields, our clients
            # can opt in to read them for observability).
            "_eos_provider": getattr(result, "provider", ""),
            "_eos_is_cloud": bool(getattr(result, "is_cloud", False)),
        }

    @server.get("/v1/models")
    async def list_models():
        """List available models in OpenAI list-models shape.

        Each entry corresponds to a provider in the think capability's
        default chain. The ``id`` is what the caller would pass as ``model``
        in a chat-completions call — though for now the facade ignores it
        (chain order decides). Future per-provider routing could honor it.
        """
        try:
            cap = kernel.capability("think")
        except Exception:
            return {"object": "list", "data": []}

        data = []
        seen = set()
        for provider in cap.providers:
            name = getattr(provider, "name", "")
            if not name or name in seen:
                continue
            seen.add(name)
            data.append(
                {
                    "id": name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "emptyos",
                    "_eos_is_cloud": bool(getattr(provider, "is_cloud", False)),
                }
            )
        return {"object": "list", "data": data}
