"""Capability middleware — transform payloads between the consent gate and the provider.

A **middleware** sits in the per-call pipeline of `Capability.execute()` /
`execute_stream()`. After the consent gate has approved a call and outbound
redaction (`_preprocess_outbound_kwargs`) has scrubbed PII, the middleware
chain runs to transform the kwargs one more time before they reach the
provider — typically to shrink them (compression), enrich them (cache hints),
or annotate them (telemetry).

Designed for ordering, capability-scope, and request/response direction from
day one so the first downstream consumer (a redaction middleware backed by
`.eos-personal`) can plug into the same slot without reshaping the protocol.
At any moment the middleware chain may be empty — registration is opt-in by
plugins, and an empty chain has zero per-call overhead.

Registration happens in a plugin's `connect()`:

    from emptyos.capabilities.middleware import register, Middleware

    class RedactMiddleware:
        name = "my-redact"
        capability_scope = {"think"}
        direction = "request"
        order = 100

        async def apply(self, cap_name, payload, context):
            # payload is the kwargs dict for "request"; the Result for "response"
            ...
            return payload

    register(RedactMiddleware())

    Note: any middleware that may block — IO, ML inference, regex on large
    text — MUST hop to a worker thread (``await asyncio.to_thread(fn, ...)``).
    Every ``apply()`` is wrapped in ``asyncio.wait_for`` with a wall-clock
    budget: ``timeout_s`` on the middleware if set, else
    ``DEFAULT_MIDDLEWARE_TIMEOUT_S`` (5s). A middleware that exceeds its
    budget is dropped from that call and the prior payload flows through
    unchanged; the timeout is logged to syslog as
    ``capability.middleware.timeout``. This is hard enforcement of the
    rule the retired ``headroom`` plugin broke three times before being
    blacklisted (see plugins/BLACKLIST.toml).

Higher `order` runs later in the request chain (and earlier in the response
chain, since response is logically the reverse direction).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol, runtime_checkable

Direction = Literal["request", "response", "both"]

# Default wall-clock budget per middleware.apply() call. A middleware may
# override by setting `timeout_s` on itself; this constant is the floor for
# anything that doesn't declare one. Five seconds is generous for any
# transform that's not actually wedged — anything legitimately slower
# (large ML inference, network calls) should already be hopping to a
# worker thread AND should declare a higher budget explicitly so the
# operator can see it in `list_middlewares()`.
DEFAULT_MIDDLEWARE_TIMEOUT_S = 5.0


@runtime_checkable
class Middleware(Protocol):
    """Capability middleware protocol — duck-typed by attribute presence.

    Anything with these attributes and an awaitable `apply()` is a valid
    middleware. No required base class so plugins don't have to import this
    module just to expose one.

    Optional attribute (deliberately NOT in the Protocol so existing
    middlewares stay isinstance-valid): set ``timeout_s`` on the middleware
    to override ``DEFAULT_MIDDLEWARE_TIMEOUT_S``. Set higher only when the
    middleware genuinely needs the budget (e.g. ML inference on a worker
    thread); a high timeout on a sync-in-loop middleware just means a
    slower wedge.
    """

    name: str
    capability_scope: set[str] | None  # None = applies to every capability
    direction: Direction
    order: int

    async def apply(self, cap_name: str, payload: Any, context: dict) -> Any: ...


def _budget_of(m: Middleware) -> float:
    """Effective wall-clock budget for one apply() call. Falls back to default
    when the middleware doesn't declare a `timeout_s`, or declares a falsy one."""
    return float(getattr(m, "timeout_s", DEFAULT_MIDDLEWARE_TIMEOUT_S) or DEFAULT_MIDDLEWARE_TIMEOUT_S)


# Module-level registry. Plugins append on connect(), remove on disconnect().
# Kept simple on purpose — there's exactly one chain per daemon and middlewares
# are not hot-swapped at runtime; the daemon reboots when a plugin's middleware
# graph changes.
_CHAIN: list[Middleware] = []


def register(middleware: Middleware) -> None:
    """Add a middleware to the chain. Idempotent on `name`."""
    if not isinstance(middleware, Middleware):
        raise TypeError(
            f"register() got {type(middleware).__name__}; needs name + "
            f"capability_scope + direction + order + async apply()"
        )
    # Replace any prior entry with the same name — plugin reloads should not
    # double-register. Acceptable because there's no "instance identity" we
    # need to preserve; the latest registration wins.
    _CHAIN[:] = [m for m in _CHAIN if m.name != middleware.name]
    _CHAIN.append(middleware)


def unregister(name: str) -> None:
    """Remove a middleware by name. Quiet no-op if not present."""
    _CHAIN[:] = [m for m in _CHAIN if m.name != name]


def list_middlewares() -> list[dict]:
    """Snapshot of the chain for debug surfaces (system inspector, tests)."""
    return [
        {
            "name": m.name,
            "capability_scope": (
                sorted(m.capability_scope) if m.capability_scope else None
            ),
            "direction": m.direction,
            "order": m.order,
            "timeout_s": _budget_of(m),
        }
        for m in _CHAIN
    ]


def _matches(m: Middleware, cap_name: str, direction: Direction) -> bool:
    if m.direction != direction and m.direction != "both":
        return False
    if m.capability_scope is not None and cap_name not in m.capability_scope:
        return False
    return True


async def run_chain(
    cap_name: str,
    payload: Any,
    *,
    direction: Direction,
    context: dict | None = None,
) -> Any:
    """Run every matching middleware in order, threading payload through.

    Returns the (possibly transformed) payload. A middleware that raises does
    NOT block the call — its exception is swallowed and the prior payload is
    used. This mirrors the "graceful enhancement" principle: a broken
    compressor must not break the kernel. Failures still land in syslog so an
    operator can spot them.

    For direction="request", payload is the kwargs dict the provider will be
    called with. For direction="response", payload is the `Result` the
    provider returned. Middlewares declare which side they want.

    `context` carries per-call metadata the middleware may need for gating —
    typically ``{"provider_name": "openai", "is_cloud": True}``. The dict is
    read-only by convention; middlewares that need to thread state should put
    it on the payload, not the context.
    """
    if not _CHAIN:
        return payload
    ctx = context or {}
    # Sort once per call. Cheap (chain length is ~single digits) and avoids a
    # global resort hook when register/unregister is called from plugin code.
    ordered = sorted(
        (m for m in _CHAIN if _matches(m, cap_name, direction)),
        key=lambda m: m.order,
    )
    if direction == "response":
        # Response chain runs in reverse — the middleware that wrapped last on
        # the way out should unwrap first on the way back.
        ordered.reverse()
    for m in ordered:
        budget = _budget_of(m)
        try:
            payload = await asyncio.wait_for(m.apply(cap_name, payload, ctx), timeout=budget)
        except asyncio.TimeoutError:
            # Hard enforcement of the docstring rule. A wedged middleware
            # gets dropped from this call; the prior payload flows through.
            try:
                from emptyos.kernel.syslog import emit_syslog  # noqa: PLC0415

                emit_syslog(
                    "capability.middleware.timeout",
                    {
                        "middleware": m.name,
                        "capability": cap_name,
                        "timeout_s": budget,
                    },
                )
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001 — broken middleware must not break the kernel
            # Best-effort log; don't import syslog at module import time (would
            # create a circular dep through kernel boot).
            try:
                from emptyos.kernel.syslog import emit_syslog  # noqa: PLC0415

                emit_syslog(
                    "capability.middleware.error",
                    {"middleware": m.name, "capability": cap_name, "error": repr(e)},
                )
            except Exception:
                pass
    return payload
