"""Tests for emptyos.sdk.claude_run_stream.

Pure SDK tests — no daemon, no claude-cli, no fixtures larger than a tmp_path.
Covers the line-level transform (assistant text / tool_use / user tool_result /
malformed) and the file-tail async generator (writes lines, flips status,
asserts events drain).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from emptyos.sdk.claude_run_stream import (
    stream_claude_run_events,
    transform_stream_json_line,
)


# ── transform_stream_json_line ────────────────────────────────────────────


def test_transform_assistant_text_yields_chunk():
    t0 = time.time()
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Hello"}]},
    })
    out = transform_stream_json_line(line, t0)
    assert len(out) == 1
    assert out[0]["type"] == "chunk"
    assert out[0]["text"] == "Hello"
    assert out[0]["elapsed_ms"] >= 0


def test_transform_assistant_tool_use_yields_tool_use():
    t0 = time.time()
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "id": "toolu_abc",
            "name": "Read",
            "input": {"file_path": "/foo/bar.py"},
        }]},
    })
    out = transform_stream_json_line(line, t0)
    assert len(out) == 1
    assert out[0] == {
        "type": "tool_use",
        "tool": "Read",
        "input": {"file_path": "/foo/bar.py"},
        "id": "toolu_abc",
        "elapsed_ms": out[0]["elapsed_ms"],
    }


def test_transform_assistant_multiple_blocks():
    t0 = time.time()
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "Reading file…"},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "x"}},
        ]},
    })
    out = transform_stream_json_line(line, t0)
    assert [e["type"] for e in out] == ["chunk", "tool_use"]


def test_transform_user_tool_result_string_content():
    t0 = time.time()
    line = json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_abc",
            "content": "file contents here",
        }]},
    })
    out = transform_stream_json_line(line, t0)
    assert len(out) == 1
    assert out[0]["type"] == "tool_result"
    assert out[0]["tool_use_id"] == "toolu_abc"
    assert out[0]["preview"] == "file contents here"


def test_transform_user_tool_result_list_content():
    """Anthropic tool_result.content can be a list of blocks; transform flattens."""
    t0 = time.time()
    line = json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}],
        }]},
    })
    out = transform_stream_json_line(line, t0)
    assert len(out) == 1
    assert out[0]["preview"] == "part1 part2"


def test_transform_user_tool_result_preview_truncates_at_300():
    t0 = time.time()
    big = "x" * 500
    line = json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "t1", "content": big,
        }]},
    })
    out = transform_stream_json_line(line, t0)
    assert len(out[0]["preview"]) == 300


def test_transform_malformed_json_returns_empty():
    assert transform_stream_json_line("not json {{", time.time()) == []
    assert transform_stream_json_line("", time.time()) == []


def test_transform_unknown_type_returns_empty():
    """`result` / `system` / future event types are skipped — only assistant/user yield."""
    t0 = time.time()
    assert transform_stream_json_line(json.dumps({"type": "result", "subtype": "success"}), t0) == []
    assert transform_stream_json_line(json.dumps({"type": "system"}), t0) == []


def test_transform_empty_assistant_text_skipped():
    """Empty `text` block doesn't emit a chunk event (would be a no-op for the UI)."""
    t0 = time.time()
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": ""}]},
    })
    assert transform_stream_json_line(line, t0) == []


def test_transform_elapsed_anchored_to_t0_wall():
    """elapsed_ms is wall-clock since t0, not since now — replays of old runs
    report the run's real duration, not 0."""
    t0_past = time.time() - 5.0   # pretend the run started 5s ago
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi"}]},
    })
    out = transform_stream_json_line(line, t0_past)
    # Some scheduling jitter is fine; assert the lower bound.
    assert out[0]["elapsed_ms"] >= 4900


# ── stream_claude_run_events ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_drains_terminal_run(tmp_path):
    """Terminal run: events from stream.jsonl drain once, then the generator returns."""
    stream_p = tmp_path / "stream.jsonl"
    meta_p = tmp_path / "run.json"

    stream_p.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "A"}]}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "x"}}]}}) + "\n" +
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}) + "\n",
        encoding="utf-8",
    )
    meta_p.write_text(json.dumps({
        "status": "ok",
        "started_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")

    events = []
    async for ev in stream_claude_run_events(
        stream_path=stream_p,
        meta_path=meta_p,
        terminal_statuses={"ok", "error"},
        started_at_field="started_at",
        poll_interval_s=0.01,
    ):
        events.append(ev)

    types = [e["type"] for e in events]
    assert types == ["chunk", "tool_use", "tool_result"]


@pytest.mark.asyncio
async def test_stream_tails_running_run_then_terminates(tmp_path):
    """Running run: tails the file, picks up new lines, returns when status flips."""
    stream_p = tmp_path / "stream.jsonl"
    meta_p = tmp_path / "run.json"

    # Start with one line + status=running.
    stream_p.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}) + "\n",
        encoding="utf-8",
    )
    meta_p.write_text(json.dumps({"status": "running", "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

    events = []

    async def consume():
        async for ev in stream_claude_run_events(
            stream_path=stream_p,
            meta_path=meta_p,
            terminal_statuses={"ok"},
            poll_interval_s=0.02,
        ):
            events.append(ev)

    task = asyncio.create_task(consume())
    # Let the consumer drain the initial line.
    await asyncio.sleep(0.1)
    # Append another line while "running".
    with stream_p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}}) + "\n")
    await asyncio.sleep(0.1)
    # Flip status terminal.
    meta_p.write_text(json.dumps({"status": "ok", "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    await asyncio.wait_for(task, timeout=2.0)

    texts = [e["text"] for e in events if e["type"] == "chunk"]
    assert texts == ["first", "second"]


@pytest.mark.asyncio
async def test_stream_partial_line_held_until_complete(tmp_path):
    """Partial trailing line is not parsed until the newline arrives."""
    stream_p = tmp_path / "stream.jsonl"
    meta_p = tmp_path / "run.json"

    # Write a partial JSON object (no trailing newline) and a complete one before it.
    complete = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n"
    partial = '{"type": "assistant", "message": {"content": [{"type":'
    stream_p.write_text(complete + partial, encoding="utf-8")
    meta_p.write_text(json.dumps({"status": "running", "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

    events = []

    async def consume():
        async for ev in stream_claude_run_events(
            stream_path=stream_p, meta_path=meta_p,
            terminal_statuses={"ok"}, poll_interval_s=0.02,
        ):
            events.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    # After this pass: complete line yielded; partial held back.
    assert [e.get("text") for e in events if e["type"] == "chunk"] == ["done"]

    # Complete the partial line, then flip terminal.
    rest = '"text", "text": "tail"}]}}\n'
    with stream_p.open("a", encoding="utf-8") as f:
        f.write(rest)
    await asyncio.sleep(0.1)
    meta_p.write_text(json.dumps({"status": "ok", "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    await asyncio.wait_for(task, timeout=2.0)

    texts = [e["text"] for e in events if e["type"] == "chunk"]
    assert texts == ["done", "tail"]


@pytest.mark.asyncio
async def test_stream_missing_started_at_falls_back_to_now(tmp_path):
    """Meta without started_at field doesn't crash — elapsed anchored to now."""
    stream_p = tmp_path / "stream.jsonl"
    meta_p = tmp_path / "run.json"
    stream_p.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}) + "\n",
        encoding="utf-8",
    )
    meta_p.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

    events = []
    async for ev in stream_claude_run_events(
        stream_path=stream_p, meta_path=meta_p,
        terminal_statuses={"ok"}, poll_interval_s=0.01,
    ):
        events.append(ev)

    # Elapsed should be small (consumer just started); the point is no crash.
    assert events and events[0]["type"] == "chunk"
    assert events[0]["elapsed_ms"] < 5000
