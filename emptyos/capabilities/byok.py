"""Per-request user-supplied API keys (BYOK — Bring Your Own Key).

For public demos and shared deployments where the server should NOT ship
its own cloud-provider keys but should let visitors paste their own.

Flow:
  1. Visitor pastes an OpenAI key in Settings (frontend stores in localStorage)
  2. Frontend adds 'X-User-OpenAI-Key: sk-...' header on every request
  3. server.py byok_middleware() extracts the header → sets a contextvar
  4. Provider's _api_key() reads the contextvar first, falls back to env

Per-request scope means one visitor's key is never visible to another
visitor's request — contextvars are bound to the asyncio task that handles
the request, automatically reset when the task ends.

Headers recognized:
  X-User-OpenAI-Key      → keys["openai"]
  X-User-Anthropic-Key   → keys["anthropic"]

Add more providers here as the demo grows. Names match what providers
read via get_byok_key(<name>).
"""

from __future__ import annotations

import contextvars

# Maps provider name → user-supplied API key. Empty by default; populated
# by the request middleware when the relevant header is present.
_byok: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("byok", default={})


def get_byok_key(provider: str) -> str:
    """Return the user-supplied key for `provider` in the current request,
    or empty string if none was supplied."""
    return _byok.get().get(provider, "")


def set_byok_keys(keys: dict[str, str]):
    """Set the BYOK dict for the current async context. Returns a token
    suitable for `_byok.reset(token)` once the request ends.

    Caller (the middleware) is responsible for calling reset to avoid
    leaking the keys into other tasks that share the parent context.
    """
    return _byok.set(keys)


def reset_byok_keys(token):
    """Reset the contextvar using the token from set_byok_keys()."""
    _byok.reset(token)


# Header → key-name mapping. Edit this dict to add support for more providers.
HEADER_MAP: dict[str, str] = {
    "x-user-openai-key": "openai",
    "x-user-anthropic-key": "anthropic",
}
