"""EOS Operation execution contract.

An Operation is one bounded unit of execution: it carries the actor, target,
inputs, safety class, timeout/retry policy, audit settings, and optional undo
metadata. This is Effect-inspired, but named in EmptyOS language so apps do not
inherit a TypeScript/FP framework vocabulary.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Awaitable, Callable, Literal

OperationSafety = Literal["stable", "gated", "never"]
OperationCloudPolicy = Literal["local_only", "ask", "allow"]
OperationErrorKind = Literal[
    "validation",
    "permission_denied",
    "cloud_consent_required",
    "timeout",
    "transient_provider_error",
    "provider_exhausted",
    "not_found",
    "tool_error",
    "unsafe",
    "conflict",
    "bug",
]


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for one Operation.

    ``max_attempts`` is retries after the first try. By default Operations do
    not retry; callers opt in only for transient shapes.
    """

    max_attempts: int = 0
    backoff_s: float = 0.0
    max_backoff_s: float | None = None
    retry_on: tuple[OperationErrorKind, ...] = ("timeout", "transient_provider_error")


@dataclass(frozen=True)
class UndoSpec:
    """Metadata for a compensating action.

    The MVP records the handle; durable undo storage is owned by the caller or
    a later operation-ledger layer.
    """

    kind: str
    description: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditSpec:
    """Controls Operation audit events."""

    enabled: bool = True
    event_prefix: str = "operation"
    include_input: bool = False
    include_value: bool = False
    redact_keys: tuple[str, ...] = (
        "api_key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    )


@dataclass(frozen=True)
class OperationSpec:
    """Declarative contract for a bounded execution unit."""

    name: str
    actor: str = "system"
    input: dict[str, Any] = field(default_factory=dict)
    capability: str | None = None
    app: str | None = None
    method: str | None = None
    safety: OperationSafety = "stable"
    readonly: bool = True
    cloud_policy: OperationCloudPolicy = "ask"
    timeout_s: float | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency_key: str | None = None
    undo: UndoSpec | None = None
    audit: AuditSpec = field(default_factory=AuditSpec)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationError:
    """Structured error returned to humans, UIs, and LLM agents."""

    kind: OperationErrorKind
    message: str
    recoverable: bool = False
    suggested_next: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "recoverable": self.recoverable,
            "suggested_next": self.suggested_next,
        }


@dataclass(frozen=True)
class OperationResult:
    """Outcome of an Operation."""

    ok: bool
    value: Any = None
    error: OperationError | None = None
    provider: str | None = None
    duration_ms: int = 0
    audit_id: str | None = None
    undo_id: str | None = None
    attempts: int = 1
    operation: str = ""

    def to_dict(self, *, include_value: bool = True) -> dict[str, Any]:
        out = {
            "ok": self.ok,
            "operation": self.operation,
            "provider": self.provider,
            "duration_ms": self.duration_ms,
            "audit_id": self.audit_id,
            "undo_id": self.undo_id,
            "attempts": self.attempts,
            "error": self.error.to_dict() if self.error else None,
        }
        if include_value:
            out["value"] = _jsonable(self.value)
        return out


class OperationRunner:
    """Run Operations with timeout, retry, structured errors, and audit events."""

    def __init__(self, *, app: Any = None, kernel: Any = None):
        self.app = app
        self.kernel = kernel or getattr(app, "kernel", None)

    async def run(
        self,
        spec: OperationSpec,
        func: Callable[[], Any | Awaitable[Any]],
    ) -> OperationResult:
        """Execute ``func`` according to ``spec``.

        The callable is zero-argument by design; callers close over their local
        variables so the spec remains the complete metadata envelope.
        """

        audit_id = _new_id("op")
        if not spec.name:
            err = OperationError(
                kind="validation",
                message="operation name is required",
                recoverable=False,
                suggested_next="set OperationSpec.name",
            )
            return OperationResult(
                ok=False,
                error=err,
                audit_id=audit_id,
                operation=spec.name,
            )

        if spec.safety == "never":
            err = OperationError(
                kind="unsafe",
                message=f"operation {spec.name!r} is marked safety='never'",
                recoverable=True,
                suggested_next="route this through an explicit human-gated workflow",
            )
            result = OperationResult(
                ok=False,
                error=err,
                audit_id=audit_id,
                operation=spec.name,
            )
            await self._audit("blocked", spec, audit_id, result=result)
            return result

        await self._audit("started", spec, audit_id)
        max_attempts = max(0, int(spec.retry.max_attempts or 0)) + 1
        last_error: OperationError | None = None
        total_start = time.monotonic()

        for attempt in range(1, max_attempts + 1):
            try:
                value = await self._invoke(func, timeout_s=spec.timeout_s)
                duration_ms = round((time.monotonic() - total_start) * 1000)
                result = OperationResult(
                    ok=True,
                    value=value,
                    provider=_provider_from(value),
                    duration_ms=duration_ms,
                    audit_id=audit_id,
                    undo_id=_new_id("undo") if spec.undo else None,
                    attempts=attempt,
                    operation=spec.name,
                )
                await self._audit("completed", spec, audit_id, result=result)
                return result
            except Exception as exc:  # noqa: BLE001 - normalize for agents/UIs
                last_error = operation_error_from_exception(exc)
                should_retry = (
                    attempt < max_attempts
                    and last_error.kind in spec.retry.retry_on
                )
                if should_retry:
                    await self._audit(
                        "retrying",
                        spec,
                        audit_id,
                        error=last_error,
                        attempt=attempt,
                    )
                    delay = _retry_delay(spec.retry, attempt)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue

                duration_ms = round((time.monotonic() - total_start) * 1000)
                result = OperationResult(
                    ok=False,
                    error=last_error,
                    duration_ms=duration_ms,
                    audit_id=audit_id,
                    attempts=attempt,
                    operation=spec.name,
                )
                await self._audit("failed", spec, audit_id, result=result)
                return result

        err = last_error or OperationError(
            kind="bug",
            message="operation exited without result",
            recoverable=False,
        )
        return OperationResult(
            ok=False,
            error=err,
            audit_id=audit_id,
            attempts=max_attempts,
            operation=spec.name,
        )

    async def call_app(
        self,
        app_id: str,
        method: str,
        /,
        *,
        actor: str = "system",
        safety: OperationSafety = "gated",
        readonly: bool = False,
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
        **kwargs,
    ) -> OperationResult:
        """Run ``BaseApp.call_app`` as an Operation."""

        if self.app is None or not hasattr(self.app, "call_app"):
            err = OperationError(
                kind="validation",
                message="OperationRunner.call_app requires app=BaseApp",
                recoverable=False,
            )
            return OperationResult(ok=False, error=err, operation=f"{app_id}.{method}")
        spec = OperationSpec(
            name=f"{app_id}.{method}",
            actor=actor,
            app=app_id,
            method=method,
            input=kwargs,
            safety=safety,
            readonly=readonly,
            timeout_s=timeout_s,
            retry=retry or RetryPolicy(),
        )
        return await self.run(spec, lambda: self.app.call_app(app_id, method, **kwargs))

    async def think(
        self,
        prompt: str,
        /,
        *,
        actor: str = "system",
        timeout_s: float | None = None,
        retry: RetryPolicy | None = None,
        **kwargs,
    ) -> OperationResult:
        """Run ``BaseApp.think`` as an Operation."""

        if self.app is None or not hasattr(self.app, "think"):
            err = OperationError(
                kind="validation",
                message="OperationRunner.think requires app=BaseApp",
                recoverable=False,
            )
            return OperationResult(ok=False, error=err, operation="think")
        spec = OperationSpec(
            name="think",
            actor=actor,
            capability="think",
            input={"prompt": prompt, **kwargs},
            safety="stable",
            readonly=True,
            cloud_policy="ask",
            timeout_s=timeout_s,
            retry=retry or RetryPolicy(),
        )
        return await self.run(spec, lambda: self.app.think(prompt, **kwargs))

    async def emit(
        self,
        event_type: str,
        data: dict | None = None,
        /,
        *,
        actor: str = "system",
        timeout_s: float | None = None,
    ) -> OperationResult:
        """Run ``BaseApp.emit`` as an Operation."""

        if self.app is None or not hasattr(self.app, "emit"):
            err = OperationError(
                kind="validation",
                message="OperationRunner.emit requires app=BaseApp",
                recoverable=False,
            )
            return OperationResult(ok=False, error=err, operation=f"emit.{event_type}")
        payload = data or {}
        spec = OperationSpec(
            name=f"emit.{event_type}",
            actor=actor,
            input=payload,
            safety="stable",
            readonly=False,
            timeout_s=timeout_s,
        )
        return await self.run(spec, lambda: self.app.emit(event_type, payload))

    async def _invoke(
        self,
        func: Callable[[], Any | Awaitable[Any]],
        *,
        timeout_s: float | None,
    ) -> Any:
        async def call():
            value = func()
            if inspect.isawaitable(value):
                return await value
            return value

        if timeout_s is not None and timeout_s > 0:
            return await asyncio.wait_for(call(), timeout=timeout_s)
        return await call()

    async def _audit(
        self,
        suffix: str,
        spec: OperationSpec,
        audit_id: str,
        *,
        result: OperationResult | None = None,
        error: OperationError | None = None,
        attempt: int | None = None,
    ) -> None:
        if not spec.audit.enabled:
            return
        events = getattr(self.kernel, "events", None)
        emit = getattr(events, "emit", None)
        if emit is None:
            return

        payload = _audit_payload(spec, audit_id, result=result, error=error, attempt=attempt)
        try:
            maybe = emit(
                f"{spec.audit.event_prefix}:{suffix}",
                payload,
                source="operation",
            )
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:
            return


def operation_error_from_exception(exc: BaseException) -> OperationError:
    """Normalize arbitrary Python exceptions into agent-readable errors."""

    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, asyncio.TimeoutError):
        return OperationError(
            kind="timeout",
            message=message,
            recoverable=True,
            suggested_next="retry with a larger timeout or smaller input",
        )
    if isinstance(exc, PermissionError):
        return OperationError(
            kind="permission_denied",
            message=message,
            recoverable=True,
            suggested_next="ask for permission or choose a read-only operation",
        )
    if isinstance(exc, FileNotFoundError):
        return OperationError(
            kind="not_found",
            message=message,
            recoverable=True,
            suggested_next="verify the target id or path before retrying",
        )
    if isinstance(exc, (ValueError, TypeError)):
        return OperationError(
            kind="validation",
            message=message,
            recoverable=True,
            suggested_next="fix the operation input shape",
        )
    if isinstance(exc, ConnectionError):
        return OperationError(
            kind="transient_provider_error",
            message=message,
            recoverable=True,
            suggested_next="retry after the provider or service recovers",
        )
    if isinstance(exc, RuntimeError) and "No available provider" in message:
        return OperationError(
            kind="provider_exhausted",
            message=message,
            recoverable=True,
            suggested_next="try a different provider, reduce requirements, or ask the user",
        )
    return OperationError(
        kind="bug",
        message=message,
        recoverable=False,
        suggested_next="inspect logs and fix the caller or implementation",
    )


def operation_tool_display(result: OperationResult) -> dict[str, Any]:
    """Compact Operation metadata safe to place inside agent ToolResult.display."""

    return result.to_dict(include_value=False)


def _audit_payload(
    spec: OperationSpec,
    audit_id: str,
    *,
    result: OperationResult | None,
    error: OperationError | None,
    attempt: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "audit_id": audit_id,
        "name": spec.name,
        "actor": spec.actor,
        "capability": spec.capability,
        "app": spec.app,
        "method": spec.method,
        "safety": spec.safety,
        "readonly": spec.readonly,
        "cloud_policy": spec.cloud_policy,
        "idempotency_key": spec.idempotency_key,
        "metadata": _redact(spec.metadata, spec.audit.redact_keys),
    }
    if attempt is not None:
        payload["attempt"] = attempt
    if spec.audit.include_input:
        payload["input"] = _redact(spec.input, spec.audit.redact_keys)
    if spec.undo:
        payload["undo"] = _redact(asdict(spec.undo), spec.audit.redact_keys)
    if error:
        payload["error"] = error.to_dict()
    if result:
        payload["result"] = result.to_dict(include_value=spec.audit.include_value)
    return _jsonable(payload)


def _retry_delay(policy: RetryPolicy, attempt: int) -> float:
    base = max(0.0, float(policy.backoff_s or 0.0))
    if base <= 0:
        return 0.0
    delay = base * (2 ** max(0, attempt - 1))
    if policy.max_backoff_s is not None:
        delay = min(delay, max(0.0, float(policy.max_backoff_s)))
    return delay


def _provider_from(value: Any) -> str | None:
    if hasattr(value, "provider"):
        provider = getattr(value, "provider")
        return str(provider) if provider else None
    if isinstance(value, dict) and value.get("provider"):
        return str(value.get("provider"))
    return None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _redact(value: Any, keys: tuple[str, ...]) -> Any:
    redacted = {k.lower() for k in keys}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in redacted:
                out[key] = "[redacted]"
            else:
                out[key] = _redact(item, keys)
        return out
    if isinstance(value, list):
        return [_redact(item, keys) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, keys) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000] + f"... (truncated from {len(value)} chars)"
        return value
    return repr(value)
