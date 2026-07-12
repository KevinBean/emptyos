"""Unit tests for emptyos.sdk.trace — agent-turn trace correlation.

Pure stdlib module (contextvars), no daemon required.

Run: python -m pytest tests/test_unit_trace.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk import trace


class TestTraceBasics:
    def test_no_trace_outside_boundary(self):
        assert trace.current_trace_id() is None

    def test_new_trace_id_shape(self):
        tid = trace.new_trace_id()
        assert tid.startswith("trace-")
        assert len(tid) == len("trace-") + 10
        assert tid != trace.new_trace_id()

    def test_set_and_reset(self):
        token = trace.set_trace_id("trace-abc")
        assert trace.current_trace_id() == "trace-abc"
        trace.reset_trace_id(token)
        assert trace.current_trace_id() is None

    def test_stamp_trace_noop_without_trace(self):
        data = {"x": 1}
        assert trace.stamp_trace(data) is data
        assert "trace_id" not in data

    def test_stamp_trace_adds_id(self):
        token = trace.set_trace_id("trace-xyz")
        try:
            data = trace.stamp_trace({"x": 1})
            assert data["trace_id"] == "trace-xyz"
        finally:
            trace.reset_trace_id(token)

    def test_stamp_trace_never_overwrites(self):
        token = trace.set_trace_id("trace-ambient")
        try:
            data = trace.stamp_trace({"trace_id": "trace-explicit"})
            assert data["trace_id"] == "trace-explicit"
        finally:
            trace.reset_trace_id(token)


class TestTraceBoundary:
    @pytest.mark.asyncio
    async def test_mints_and_resets(self):
        seen: list[str | None] = []

        @trace.trace_boundary
        async def turn():
            seen.append(trace.current_trace_id())

        await turn()
        assert seen[0] and seen[0].startswith("trace-")
        assert trace.current_trace_id() is None  # reset after exit

    @pytest.mark.asyncio
    async def test_sequential_turns_get_distinct_ids(self):
        seen: list[str | None] = []

        @trace.trace_boundary
        async def turn():
            seen.append(trace.current_trace_id())

        await turn()
        await turn()
        assert seen[0] != seen[1]

    @pytest.mark.asyncio
    async def test_nested_boundary_inherits_parent(self):
        """A SubAgent turn inside a traced turn shares the parent's trace —
        the trace spans the whole task, not one call."""
        seen: list[str | None] = []

        @trace.trace_boundary
        async def inner():
            seen.append(trace.current_trace_id())

        @trace.trace_boundary
        async def outer():
            seen.append(trace.current_trace_id())
            await inner()
            seen.append(trace.current_trace_id())  # survives inner's exit

        await outer()
        assert seen[0] == seen[1] == seen[2]

    @pytest.mark.asyncio
    async def test_resets_on_exception(self):
        @trace.trace_boundary
        async def boom():
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            await boom()
        assert trace.current_trace_id() is None

    @pytest.mark.asyncio
    async def test_concurrent_tasks_are_isolated(self):
        """Each asyncio task gets its own contextvar copy — two concurrent
        turns never see each other's trace id."""
        results: dict[str, str | None] = {}

        @trace.trace_boundary
        async def turn(name: str):
            await asyncio.sleep(0.01)
            results[name] = trace.current_trace_id()

        await asyncio.gather(turn("a"), turn("b"))
        assert results["a"] != results["b"]
        assert results["a"] and results["b"]
