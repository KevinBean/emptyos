"""rooms — module-level pure helpers shared across helper modules.

Extracted so helper modules (chat, scheduling, agents) can import these
directly without cycling through the spine `.app` module or importing each
other (multi-module-apps rule 4; this file is its rule-6 exception).

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations


def coerce_timeout_s(value) -> int | None:
    """An agent's `timeout_s` as a positive whole number of seconds, or None.

    Normalised at the write boundary so a stored record never carries a shape
    the provider would misread: `True` would otherwise become a 1-second
    budget, and "600.0" would silently fall back to the default.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def responder_think_kwargs(responder: dict) -> dict:
    """The per-agent routing fields a `self.think()` turn forwards.

    `strict_provider` pins exactly one provider with no fall-through, and
    `timeout_s` gives that provider longer than the chain's global timeout.
    An agent whose answer is only honest if it searched (Job Scout) needs
    both: a tool-using run is killed at the default 30 s, and the fallback
    providers have no web tools, so they make the answer up. `provider` is
    the legacy soft pin; the two are mutually exclusive in `think()`, so the
    strict one wins when a record carries both.

    Honoured by the non-streaming `think()` paths only (`_chat` and the
    scheduled fire). `think_stream` has no strict pin, so a streamed UI turn
    still routes through the chain.
    """
    kwargs: dict = {}
    if responder.get("strict_provider"):
        kwargs["strict_provider"] = responder["strict_provider"]
    elif responder.get("provider"):
        kwargs["provider"] = responder["provider"]
    if responder.get("model"):
        kwargs["model"] = responder["model"]
    if responder.get("temperature") is not None:
        kwargs["temperature"] = responder["temperature"]
    # Forwarded to claude-cli as --effort when the resolved provider is
    # claude-cli. Other providers ignore unknown kwargs.
    if responder.get("effort"):
        kwargs["effort"] = responder["effort"]
    timeout_s = coerce_timeout_s(responder.get("timeout_s"))
    if timeout_s:
        kwargs["timeout_s"] = timeout_s
    return kwargs
