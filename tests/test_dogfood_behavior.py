"""Unit tests for dogfood-agent's behavior parsing module.

Daemon-free. Exercises the pure-function transcript parsers in
``apps/dogfood-agent/behavior.py`` directly, so a regression in
stream-json parsing, friction tagging, or abandonment detection
surfaces without booting the daemon.

Complements ``test_dogfood_picker.py``, which already covers
``B.merge_rollup`` (2 tests) and ``B.score_journey_rules`` (7 tests).
This file covers the 9 remaining behavior functions:

  * parse_stream_events
  * extract_actions  (+ _summarize_tool_call, _extract_target)
  * detect_re_entries
  * parse_log_friction
  * parse_wrap
  * detect_abandonments
  * build_behavior (composition)
"""

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def server_health():
    """Override the conftest fixture that skips when EmptyOS isn't running.
    These tests are pure-Python and have no daemon dependency."""
    return None


# Add the dogfood-agent dir to path so `behavior` imports directly.
APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "dogfood-agent"
sys.path.insert(0, str(APP_DIR))
import behavior as B  # noqa: E402


# ─── stream-json line builders ───────────────────────────────────────

def _assistant_tool_use(tool: str, inp: dict, tid: str) -> str:
    """Serialize a single claude-cli assistant event with one tool_use."""
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": tool, "input": inp, "id": tid}]},
    })


def _user_tool_result(tid: str, *, is_error: bool = False, content: str = "ok") -> str:
    """Serialize the matching user/tool_result event."""
    return json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]},
    })


# ─── parse_stream_events ─────────────────────────────────────────────

class TestParseStreamEvents:
    def test_empty_string_returns_empty_list(self):
        assert B.parse_stream_events("") == []

    def test_three_json_lines_parsed_in_order(self):
        raw = "\n".join([
            json.dumps({"type": "system", "phase": "init"}),
            _assistant_tool_use("Bash", {"command": "ls"}, "id-1"),
            _user_tool_result("id-1"),
        ])
        events = B.parse_stream_events(raw)
        assert len(events) == 3
        assert events[0]["type"] == "system"
        assert events[1]["type"] == "assistant"
        assert events[2]["type"] == "user"

    def test_malformed_line_is_skipped_not_raised(self):
        raw = "{not real json\n" + json.dumps({"type": "ok"})
        events = B.parse_stream_events(raw)
        assert len(events) == 1
        assert events[0]["type"] == "ok"

    def test_non_brace_lines_dropped(self):
        # Blank lines, plain text — should not raise, should be skipped.
        raw = "\n\nplain log line\n" + json.dumps({"type": "good"}) + "\n"
        events = B.parse_stream_events(raw)
        assert len(events) == 1


# ─── extract_actions ─────────────────────────────────────────────────

class TestExtractActions:
    def test_tool_use_becomes_action_row(self):
        events = B.parse_stream_events("\n".join([
            _assistant_tool_use("Bash", {"command": "echo hi"}, "id-1"),
            _user_tool_result("id-1"),
        ]))
        actions = B.extract_actions(events)
        assert len(actions) == 1
        assert actions[0]["tool"] == "Bash"
        assert actions[0]["turn"] == 1
        assert actions[0]["success"] is True
        assert actions[0]["error"] is None

    def test_text_only_events_drop(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking"}]}},
            {"type": "system", "phase": "result"},
        ]
        assert B.extract_actions(events) == []

    def test_turn_counter_increments_per_tool_use(self):
        events = B.parse_stream_events("\n".join([
            _assistant_tool_use("Read", {"file_path": "/a.md"}, "id-1"),
            _assistant_tool_use("Bash", {"command": "ls"}, "id-2"),
            _assistant_tool_use("Write", {"file_path": "/b.md"}, "id-3"),
        ]))
        actions = B.extract_actions(events)
        assert [a["turn"] for a in actions] == [1, 2, 3]

    def test_tool_result_with_error_marks_failure(self):
        events = B.parse_stream_events("\n".join([
            _assistant_tool_use("Bash", {"command": "false"}, "id-1"),
            _user_tool_result("id-1", is_error=True, content="exit code 1"),
        ]))
        actions = B.extract_actions(events)
        assert actions[0]["success"] is False
        assert "exit code 1" in actions[0]["error"]

    def test_tool_result_content_as_list_is_joined(self):
        # claude-cli sometimes sends content as a list of text parts.
        ev = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result", "tool_use_id": "id-1", "is_error": True,
                "content": [{"text": "part-a"}, {"text": "part-b"}],
            }]},
        }
        events = [
            json.loads(_assistant_tool_use("Bash", {"command": "x"}, "id-1")),
            ev,
        ]
        actions = B.extract_actions(events)
        assert "part-a" in actions[0]["error"]
        assert "part-b" in actions[0]["error"]


# ─── _summarize_tool_call ────────────────────────────────────────────

class TestSummarizeToolCall:
    def test_webfetch_includes_url(self):
        s = B._summarize_tool_call("WebFetch", {"url": "http://localhost:9000/api/health"})
        assert s.startswith("GET ")
        assert "localhost:9000/api/health" in s

    def test_bash_includes_command(self):
        s = B._summarize_tool_call("Bash", {"command": "ls -la /tmp"})
        assert s == "$ ls -la /tmp"

    def test_edit_includes_file_path(self):
        s = B._summarize_tool_call("Edit", {"file_path": "/x/y.md"})
        assert s == "edit /x/y.md"

    def test_unknown_tool_falls_back_to_tool_name(self):
        assert B._summarize_tool_call("FancyNewTool", {"weird": "input"}) == "FancyNewTool"


# ─── _extract_target ─────────────────────────────────────────────────

class TestExtractTarget:
    def test_kernel_api_endpoint_buckets_as_kernel(self):
        t = B._extract_target("Bash", {"command": "curl http://localhost:9000/api/health"})
        assert t == "kernel:health"

    def test_app_endpoint_returns_app_colon_endpoint(self):
        t = B._extract_target("WebFetch", {"url": "http://localhost:9000/task/api/list"})
        assert t == "task:list"

    def test_app_no_endpoint_returns_app(self):
        t = B._extract_target("WebFetch", {"url": "http://localhost:9000/journal/"})
        assert t == "journal"

    def test_vault_file_collapses_to_top_folder(self):
        t = B._extract_target("Read", {"file_path": "30_Resources/People/alice.md"})
        assert t == "vault:30_Resources"

    def test_unknown_tool_falls_back_to_tool_bucket(self):
        t = B._extract_target("FancyTool", {"random": "stuff"})
        assert t == "tool:fancytool"

    def test_empty_input_returns_empty_string(self):
        assert B._extract_target("", {}) == ""


# ─── detect_re_entries ───────────────────────────────────────────────

class TestDetectReEntries:
    def test_target_hit_twice_is_flagged(self):
        actions = [
            {"target": "journal"},
            {"target": "task"},
            {"target": "journal"},
        ]
        out = B.detect_re_entries(actions)
        assert len(out) == 1
        assert out[0] == {"target": "journal", "count": 2}

    def test_no_re_entries_returns_empty(self):
        actions = [{"target": "a"}, {"target": "b"}, {"target": "c"}]
        assert B.detect_re_entries(actions) == []

    def test_empty_target_strings_ignored(self):
        actions = [{"target": ""}, {"target": ""}, {"target": "task"}]
        assert B.detect_re_entries(actions) == []

    def test_three_hits_returns_count_three(self):
        actions = [{"target": "x"}] * 3
        out = B.detect_re_entries(actions)
        assert out[0]["count"] == 3


# ─── parse_log_friction ──────────────────────────────────────────────

class TestParseLogFriction:
    def test_bug_tag_extracted(self):
        log = "Today I tried the journal. The save button #bug broke."
        out = B.parse_log_friction(log)
        assert len(out) == 1
        assert out[0]["kind"] == "bug"
        assert "save button" in out[0]["text"]

    def test_all_three_kinds_detected(self):
        log = "\n".join([
            "T1 The task page #bug",
            "T2 The hub layout #confusing",
            "T3 The settings tab #missing",
        ])
        out = B.parse_log_friction(log)
        kinds = sorted(f["kind"] for f in out)
        assert kinds == ["bug", "confusing", "missing"]

    def test_turn_marker_parsed(self):
        out = B.parse_log_friction("T7 The cables tab #bug broken layout")
        assert len(out) == 1
        assert out[0]["turn"] == 7

    def test_no_turn_marker_yields_none(self):
        out = B.parse_log_friction("The cables tab #bug broken layout")
        assert out[0]["turn"] is None

    def test_untagged_lines_drop(self):
        log = "T1 Just a normal observation about the app.\nT2 #bug it broke"
        out = B.parse_log_friction(log)
        assert len(out) == 1

    def test_header_lines_skipped(self):
        # `## Wrap` headers and other `#`-prefixed lines must NOT be parsed
        # as a friction item even though they contain `#`.
        log = "## Wrap\nSome wrap text.\n## Notes\nT1 friction here #bug"
        out = B.parse_log_friction(log)
        assert len(out) == 1
        assert out[0]["kind"] == "bug"


# ─── parse_wrap ──────────────────────────────────────────────────────

class TestParseWrap:
    def test_wrap_section_extracted(self):
        log = "Some content.\n## Wrap\nThis was a productive run.\n"
        assert B.parse_wrap(log) == "This was a productive run."

    def test_absent_wrap_returns_empty(self):
        assert B.parse_wrap("no wrap here") == ""

    def test_empty_string_returns_empty(self):
        assert B.parse_wrap("") == ""

    def test_multiline_wrap_preserved(self):
        log = "## Wrap\nFirst line.\nSecond line.\n"
        out = B.parse_wrap(log)
        assert "First line." in out
        assert "Second line." in out

    def test_wrap_terminates_at_next_header(self):
        log = "## Wrap\nWrap body.\n## Other\nShould not be in wrap."
        out = B.parse_wrap(log)
        assert "Wrap body." in out
        assert "Should not be in wrap" not in out


# ─── detect_abandonments ─────────────────────────────────────────────

class TestDetectAbandonments:
    def test_goal_mentioned_in_log_not_abandoned(self):
        goals = ["capture an idea about cable derating"]
        # Two content words (len>3): "capture", "idea", "about", "cable", "derating"
        log = "I tried to capture an idea about cable derating tonight."
        assert B.detect_abandonments(goals, log, friction=[]) == []

    def test_goal_unmentioned_is_abandoned(self):
        goals = ["review the assistant scheduling logic"]
        log = "I worked on the journal app today."
        out = B.detect_abandonments(goals, log, friction=[])
        assert out == goals

    def test_goal_covered_but_friction_marks_it_missing(self):
        # Goal is mentioned (≥2 content-word hits) so it counts as covered,
        # but a #missing friction item shares ≥2 content words → abandoned.
        goals = ["check tomorrow's tasks for overdue"]
        log = "I tried to check tomorrow tasks for overdue items."
        friction = [{"kind": "missing", "text": "no way to check tasks for overdue easily"}]
        assert B.detect_abandonments(goals, log, friction) == goals

    def test_goal_with_unrelated_friction_not_abandoned(self):
        goals = ["capture an idea about cable derating"]
        log = "I tried to capture an idea about cable derating tonight."
        friction = [{"kind": "bug", "text": "totally unrelated journal save button broken"}]
        assert B.detect_abandonments(goals, log, friction) == []


# ─── build_behavior (composition) ────────────────────────────────────

class TestBuildBehavior:
    def test_shape_includes_all_keys(self):
        raw = _assistant_tool_use("Bash", {"command": "ls"}, "id-1") + "\n" + _user_tool_result("id-1")
        log = "## Wrap\nFinished cleanly."
        out = B.build_behavior(raw, log, scenario_goals=[])
        assert set(out.keys()) >= {
            "actions", "friction", "re_entries", "abandonments",
            "heatmap", "wrap", "turn_count", "friction_counts",
        }

    def test_turn_count_matches_action_count(self):
        raw = "\n".join([
            _assistant_tool_use("Read", {"file_path": "/a.md"}, "id-1"),
            _assistant_tool_use("Read", {"file_path": "/b.md"}, "id-2"),
        ])
        out = B.build_behavior(raw, log_text="", scenario_goals=[])
        assert out["turn_count"] == 2
        assert len(out["actions"]) == 2

    def test_friction_counts_aggregated(self):
        log = "\n".join([
            "T1 thing one #bug",
            "T2 thing two #bug",
            "T3 thing three #confusing",
        ])
        out = B.build_behavior(raw_stream="", log_text=log, scenario_goals=[])
        assert out["friction_counts"]["bug"] == 2
        assert out["friction_counts"]["confusing"] == 1
        assert out["friction_counts"]["missing"] == 0

    def test_heatmap_collapses_by_target(self):
        raw = "\n".join([
            _assistant_tool_use("WebFetch", {"url": "http://localhost:9000/task/api/list"}, "id-1"),
            _user_tool_result("id-1"),
            _assistant_tool_use("WebFetch", {"url": "http://localhost:9000/task/api/list"}, "id-2"),
            _user_tool_result("id-2"),
        ])
        out = B.build_behavior(raw, log_text="", scenario_goals=[])
        targets = {h["target"]: h for h in out["heatmap"]}
        assert "task:list" in targets
        assert targets["task:list"]["count"] == 2
        assert targets["task:list"]["ok"] == 2
