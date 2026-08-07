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


# ── Codex dialect (`codex exec --json`) ────────────────────────────────
#
# Every event below is verbatim from a real `codex exec --json` run captured
# 2026-07-31 (codex-cli 0.144.1) — not hand-written — so the shapes are the
# ones the parser will actually meet.

CODEX_MSG = {
    "type": "item.completed",
    "item": {"id": "item_1", "type": "agent_message",
             "text": "I’ll open the repository README directly."},
}
CODEX_CMD_STARTED = {
    "type": "item.started",
    "item": {"id": "item_2", "type": "command_execution",
             "command": "powershell.exe -Command 'Get-Content README.md'",
             "aggregated_output": "", "exit_code": None, "status": "in_progress"},
}
CODEX_CMD_FAILED = {
    "type": "item.completed",
    "item": {"id": "item_2", "type": "command_execution",
             "command": "powershell.exe -Command 'Get-Content README.md'",
             "aggregated_output": "execution error: windows sandbox: "
                                  "helper_unknown_error: apply deny-read ACLs",
             "exit_code": -1, "status": "failed"},
}
CODEX_CMD_OK = {
    "type": "item.completed",
    "item": {"id": "item_3", "type": "command_execution",
             "command": "powershell.exe -Command 'Get-Content README.md'",
             "aggregated_output": "# EmptyOS\r\n\r\nAn AI-native OS.",
             "exit_code": 0, "status": "completed"},
}


def test_codex_agent_message_is_a_chunk():
    ev = transform_stream_json_line(json.dumps(CODEX_MSG), T0)
    assert _types(ev) == ["chunk"]
    assert "README" in ev[0]["text"]


def test_codex_command_pairs_tool_use_to_result_by_item_id():
    started = transform_stream_json_line(json.dumps(CODEX_CMD_STARTED), T0)
    done = transform_stream_json_line(json.dumps(CODEX_CMD_OK), T0)
    assert _types(started) == ["tool_use"]
    assert started[0]["tool"] == "Bash"
    assert "Get-Content" in started[0]["input"]["command"]
    assert _types(done) == ["tool_result"]
    # The pairing the run drawer renders on is the shared item id.
    assert done[0]["tool_use_id"] == "item_3"


def test_codex_failed_command_surfaces_exit_code():
    """A failed attempt must stay visible: codex's Windows sandbox fails open,
    reporting the helper error here and then silently re-running unsandboxed.
    Dropping it would hide the only trace of that retry."""
    ev = transform_stream_json_line(json.dumps(CODEX_CMD_FAILED), T0)
    assert _types(ev) == ["tool_result"]
    assert ev[0]["preview"].startswith("[exit -1]")
    assert "deny-read ACLs" in ev[0]["preview"]


def test_codex_envelope_events_are_ignored():
    for evt in ({"type": "thread.started", "thread_id": "t1"},
                {"type": "turn.started"},
                {"type": "turn.completed", "usage": {"input_tokens": 10}}):
        assert transform_stream_json_line(json.dumps(evt), T0) == []


def test_codex_preview_is_capped():
    big = dict(CODEX_CMD_OK)
    big["item"] = dict(CODEX_CMD_OK["item"], aggregated_output="x" * 5000)
    ev = transform_stream_json_line(json.dumps(big), T0)
    assert 0 < len(ev[0]["preview"]) <= 300
