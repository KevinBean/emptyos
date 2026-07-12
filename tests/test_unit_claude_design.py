"""Unit tests for the claude-design connector's pure transport helpers.

`extract_designsync_result` parses the raw `DesignSync` tool_result out of a
claude-cli stream-json transcript (the daemon→agent-runtime→claude-cli bridge).
It must return the *full* tool result (not a truncated preview), tolerate
heartbeat / non-JSON lines, and ignore tool_results from other tools. Pure
function — no daemon, no CLI.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_TP = Path(__file__).resolve().parent.parent / "plugins" / "claude-design" / "transport.py"
_spec = importlib.util.spec_from_file_location("claude_design_transport", _TP)
transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transport)
extract = transport.extract_designsync_result


def _tool_use(tu_id: str, name: str = "DesignSync") -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tu_id, "name": name, "input": {"method": "list_projects"}}]}})


def _tool_result(tu_id: str, payload) -> str:
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tu_id,
         "content": [{"type": "text", "text": json.dumps(payload)}]}]}})


def test_extracts_full_designsync_result():
    payload = {"method": "list_projects", "projects": [{"name": "EmptyOS Apps", "projectId": "abc"}]}
    out = extract([_tool_use("tu_1"), _tool_result("tu_1", payload), '{"type":"result"}'])
    assert out == payload  # full content, not a preview


def test_full_content_not_truncated():
    # a get_file-style large result must survive intact (the streamPane helper would truncate)
    big = {"method": "get_file", "content": "x" * 5000}
    out = extract([_tool_use("tu_1"), _tool_result("tu_1", big)])
    assert len(out["content"]) == 5000


def test_ignores_non_designsync_tool_results():
    other = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tu_x", "name": "Bash", "input": {}}]}})
    res = _tool_result("tu_x", {"stdout": "nope"})
    assert extract([other, res]) == {}  # no DesignSync tool_use → nothing


def test_tolerates_heartbeat_and_garbage_lines():
    payload = {"method": "list_files", "paths": ["a.html"]}
    lines = ["", "not json", "{partial", _tool_use("tu_1"),
             '{"type":"chunk","text":"thinking"}', _tool_result("tu_1", payload)]
    assert extract(lines) == payload


def test_last_designsync_result_wins():
    a = _tool_result("tu_1", {"v": 1})
    b = _tool_result("tu_2", {"v": 2})
    lines = [_tool_use("tu_1"), a, _tool_use("tu_2"), b]
    assert extract(lines)["v"] == 2


def test_empty_on_no_lines():
    assert extract([]) == {}


def test_non_json_tool_result_returns_raw_wrapper():
    res = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_1",
         "content": [{"type": "text", "text": "plain text not json"}]}]}})
    out = extract([_tool_use("tu_1"), res])
    assert out.get("ok") is False and "plain text" in out.get("raw", "")


def test_prompt_constants_format():
    assert "list_projects" in transport.CALL_PROMPT.format(method="list_projects", args="{}")
    pushed = transport.PUSH_PROMPT.format(project_name="P", writes="[]", deletes="[]", local_dir='"d"', files="[]")
    assert "create_project(name='P')" in pushed and "write_files" in pushed
