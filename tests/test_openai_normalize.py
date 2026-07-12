"""Unit tests for OpenAICompatThinkProvider._normalize_messages_for_openai.

The OpenAI API requires every assistant message carrying `tool_calls` to be
followed by matching `role=tool` messages — one per `tool_call_id`. History
replays can produce orphans when a session was truncated mid-turn or when an
older row was persisted before tool_results landed. These tests pin the
normalizer to a wire-shape-valid output.

Run: python -m pytest tests/test_openai_normalize.py -v
"""

from __future__ import annotations

import pytest

from emptyos.capabilities.providers.openai_compat import OpenAICompatThinkProvider


@pytest.fixture
def provider() -> OpenAICompatThinkProvider:
    # Constructor needs no network — model + host are just config strings.
    return OpenAICompatThinkProvider(host="https://api.openai.com", model="gpt-test")


def _assert_openai_valid(msgs: list[dict]) -> None:
    """Every assistant tool_call must be followed by a matching role=tool message."""
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            expected_ids = {tc["id"] for tc in m["tool_calls"]}
            seen_ids: set[str] = set()
            j = i + 1
            while j < len(msgs) and msgs[j].get("role") == "tool":
                tcid = msgs[j].get("tool_call_id")
                if tcid:
                    seen_ids.add(tcid)
                j += 1
            missing = expected_ids - seen_ids
            assert not missing, f"orphan tool_call ids at index {i}: {missing}"
            i = j
        else:
            if m.get("role") == "tool":
                pytest.fail(f"role=tool at index {i} has no preceding assistant tool_calls")
            i += 1


class TestNormalizeOrphanToolCalls:
    def test_strips_orphan_tool_calls_when_no_tool_results_follow(self, provider):
        """Assistant with tool_calls but no following tool messages → tool_calls dropped."""
        msgs = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "I'll do it",
                "tool_calls": [
                    {"id": "call_abc", "type": "function",
                     "function": {"name": "Read", "arguments": "{}"}}
                ],
            },
            {"role": "user", "content": "next question"},
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        # Assistant message survives (it has text content), but tool_calls is gone.
        assistant = [m for m in out if m.get("role") == "assistant"]
        assert len(assistant) == 1
        assert "tool_calls" not in assistant[0]
        assert assistant[0].get("content") == "I'll do it"

    def test_drops_assistant_entirely_when_no_text_and_orphan_tool_calls(self, provider):
        """Assistant with only tool_calls (no text) and no tool results → drop entirely."""
        msgs = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_xyz", "type": "function",
                     "function": {"name": "Bash", "arguments": "{}"}}
                ],
            },
            {"role": "user", "content": "what happened?"},
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        assert not any(m.get("role") == "assistant" for m in out)

    def test_keeps_matched_tool_calls(self, provider):
        """Assistant with tool_calls + matching tool result → keep all."""
        msgs = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "Read", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
            {"role": "assistant", "content": "done"},
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        # All three roles present, tool_calls preserved.
        assistants = [m for m in out if m.get("role") == "assistant"]
        with_calls = [m for m in assistants if m.get("tool_calls")]
        assert len(with_calls) == 1
        assert with_calls[0]["tool_calls"][0]["id"] == "call_1"
        tools = [m for m in out if m.get("role") == "tool"]
        assert len(tools) == 1

    def test_partial_match_keeps_only_matched(self, provider):
        """Two tool_calls, only one matched → keep matched, drop orphan id."""
        msgs = [
            {"role": "user", "content": "do two things"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_keep", "type": "function",
                     "function": {"name": "Read", "arguments": "{}"}},
                    {"id": "call_drop", "type": "function",
                     "function": {"name": "Bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_keep", "content": "ok"},
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        assistants = [m for m in out if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistants) == 1
        kept = assistants[0]["tool_calls"]
        assert len(kept) == 1
        assert kept[0]["id"] == "call_keep"

    def test_orphan_tool_message_dropped_first_pass(self, provider):
        """Stray role=tool without preceding assistant → dropped in first pass."""
        msgs = [
            {"role": "tool", "tool_call_id": "ghost", "content": "stray"},
            {"role": "user", "content": "hi"},
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        assert not any(m.get("role") == "tool" for m in out)

    def test_system_message_prepended(self, provider):
        msgs = [{"role": "user", "content": "hi"}]
        out = provider._normalize_messages_for_openai(msgs, "you are helpful")
        assert out[0]["role"] == "system"
        assert out[0]["content"] == "you are helpful"

    def test_content_block_list_tool_result_round_trip(self, provider):
        """Anthropic-shaped content-block list with tool_result → flat role=tool emitted."""
        msgs = [
            {"role": "user", "content": "do thing"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "call_anth", "type": "function",
                     "function": {"name": "Read", "arguments": "{}"}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_anth", "content": "file body"},
                ],
            },
        ]
        out = provider._normalize_messages_for_openai(msgs, "sys")
        _assert_openai_valid(out)
        tools = [m for m in out if m.get("role") == "tool"]
        assert len(tools) == 1
        assert tools[0]["tool_call_id"] == "call_anth"
