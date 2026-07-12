"""Tests for emptyos.sdk.operation.

The Operation layer is intentionally pure SDK: no daemon and no real providers
are required. These tests pin the execution contract agents will consume.
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.operation import AuditSpec, OperationRunner, OperationSpec, RetryPolicy


class _Events:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, etype, data, source="operation"):
        self.events.append((etype, data))


class _Kernel:
    def __init__(self):
        self.events = _Events()


def _run(coro):
    return asyncio.run(coro)


def test_operation_success_audits_and_redacts_input():
    kernel = _Kernel()
    runner = OperationRunner(kernel=kernel)
    spec = OperationSpec(
        name="demo.success",
        actor="test",
        input={"token": "secret-value", "plain": "ok"},
        audit=AuditSpec(include_input=True),
    )

    result = _run(runner.run(spec, lambda: {"provider": "fake", "answer": 42}))

    assert result.ok is True
    assert result.provider == "fake"
    assert [e[0] for e in kernel.events.events] == [
        "operation:started",
        "operation:completed",
    ]
    started = kernel.events.events[0][1]
    assert started["input"]["token"] == "[redacted]"
    assert started["input"]["plain"] == "ok"


def test_operation_timeout_returns_structured_error():
    runner = OperationRunner(kernel=_Kernel())

    async def slow():
        await asyncio.sleep(0.05)
        return "late"

    result = _run(
        runner.run(
            OperationSpec(name="demo.timeout", timeout_s=0.001),
            slow,
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.error.recoverable is True


def test_operation_retries_transient_provider_errors():
    kernel = _Kernel()
    runner = OperationRunner(kernel=kernel)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("provider unavailable")
        return "ok"

    result = _run(
        runner.run(
            OperationSpec(
                name="demo.retry",
                retry=RetryPolicy(max_attempts=1),
            ),
            flaky,
        )
    )

    assert result.ok is True
    assert result.attempts == 2
    assert attempts["n"] == 2
    assert "operation:retrying" in [e[0] for e in kernel.events.events]


def test_operation_never_safety_blocks_without_calling_function():
    runner = OperationRunner(kernel=_Kernel())
    called = {"value": False}

    def should_not_run():
        called["value"] = True
        return "bad"

    result = _run(
        runner.run(
            OperationSpec(name="demo.never", safety="never"),
            should_not_run,
        )
    )

    assert called["value"] is False
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == "unsafe"
