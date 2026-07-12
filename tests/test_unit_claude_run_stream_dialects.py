"""Unit tests for transform_stream_json_line two-dialect support (P0.2).

The run-drawer reader must understand BOTH transcript writers off one
stream.jsonl:
  - Anthropic stream-json (claude-cli native runs)
  - eos-agent runner's flat agent:* lines (_RunnerEventRecorder)

Before this fix, an eos-agent run's agent:* lines were silently dropped and
the run drawer read "agent did nothing". Pure — no daemon.
"""

from __future__ import annotations

import json

from emptyos.sdk.claude_run_stream import transform_stream_json_line

T0 = 0.0  # elapsed_ms is wall-clock since t0; value is not asserted here


def _types(events):
    return [e["type"] for e in events]


# ── Anthropic stream-json dialect (unchanged behavior) ──────────────────

def test_anthropic_assistant_text():
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"}]}})
    out = transform_stream_json_line(line, T0)
    assert len(out) == 1
    assert out[0]["type"] == "chunk"
    assert out[0]["text"] == "hello"


def test_anthropic_tool_use_and_result():
    use = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}, "id": "tu_1"}]}})
    res = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "a\nb"}]}})
    u = transform_stream_json_line(use, T0)
    r = transform_stream_json_line(res, T0)
    assert u[0]["type"] == "tool_use" and u[0]["tool"] == "Bash" and u[0]["id"] == "tu_1"
    assert r[0]["type"] == "tool_result" and r[0]["tool_use_id"] == "tu_1"
    assert "a" in r[0]["preview"]


# ── eos-agent runner dialect (the new branch) ───────────────────────────

def test_agent_text_dialect():
    line = json.dumps({"type": "agent:text", "session_id": "s1", "delta": "thinking..."})
    out = transform_stream_json_line(line, T0)
    assert out == [{"type": "chunk", "text": "thinking...", "elapsed_ms": out[0]["elapsed_ms"]}]


def test_agent_tool_call_dialect():
    line = json.dumps({"type": "agent:tool_call", "session_id": "s1",
                       "id": "call_7", "name": "Edit", "input": {"path": "a.py"}})
    out = transform_stream_json_line(line, T0)
    assert len(out) == 1
    e = out[0]
    assert e["type"] == "tool_use"
    assert e["tool"] == "Edit"
    assert e["input"] == {"path": "a.py"}
    assert e["id"] == "call_7"


def test_agent_tool_result_dialect_pairs_by_id():
    """tool_call.id maps to tool_result.tool_use_id so the UI pairs them."""
    call = json.dumps({"type": "agent:tool_call", "id": "call_7", "name": "Read", "input": {}})
    result = json.dumps({"type": "agent:tool_result", "id": "call_7",
                         "is_error": False, "content": "file contents here"})
    c = transform_stream_json_line(call, T0)
    r = transform_stream_json_line(result, T0)
    assert c[0]["id"] == "call_7"
    assert r[0]["type"] == "tool_result"
    assert r[0]["tool_use_id"] == "call_7"
    assert r[0]["preview"] == "file contents here"


def test_agent_tool_result_error_snippet_fallback():
    line = json.dumps({"type": "agent:tool_result", "id": "x", "is_error": True,
                       "error_snippet": "boom"})
    out = transform_stream_json_line(line, T0)
    assert out[0]["preview"] == "boom"


def test_agent_tool_result_display_fallback():
    line = json.dumps({"type": "agent:tool_result", "id": "x",
                       "display": {"name": "Grep", "matches": 3}})
    out = transform_stream_json_line(line, T0)
    assert "Grep" in out[0]["preview"]


def test_agent_other_events_ignored():
    for etype in ("agent:turn_start", "agent:iter_start", "agent:done", "agent:compacted"):
        line = json.dumps({"type": etype, "session_id": "s1"})
        assert transform_stream_json_line(line, T0) == []


def test_garbage_line_is_safe():
    assert transform_stream_json_line("not json", T0) == []
    assert transform_stream_json_line("", T0) == []
