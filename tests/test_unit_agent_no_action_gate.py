"""No-action gate: an empty finish gets exactly one nudge to act.

Behavioural — drives the real ``run_turn`` with a scripted provider, because a
source grep for the gate would pass on the explanatory comment alone
(`.claude/rules/audits.md`, failure mode 3). Pure: no daemon, no LLM.

The failure it closes was measured on model-bench engineer-calc: the local 9.7B
model read the spec and ended its turn with empty content, so the file was never
written (0/2).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from emptyos.capabilities.providers._tool_capable import AgentTurn, TextBlock, ToolUse, ToolUseBlock
from emptyos.sdk.agent_loop import NO_ACTION_GATE_MSG, AgentSession, run_turn


class _Config:
    def __init__(self, flags: dict):
        self._flags = flags

    def get(self, key, default=None):
        return self._flags.get(key, default)


def _app(gate_on: bool):
    return SimpleNamespace(
        id="agent",
        kernel=SimpleNamespace(config=_Config({"apps.agent.feature.no-action-gate.enabled": gate_on})),
    )


class _ScriptedProvider:
    """Returns the scripted turns in order and records what it was sent."""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, turns, kind="openai"):
        self._turns = list(turns)
        self.kind = kind
        self.calls = 0
        self.sent = []  # a snapshot of the history at each call

    async def execute_tools(self, *, messages, system, tools, temperature):
        self.calls += 1
        self.sent.append([dict(m) for m in messages])
        return self._turns.pop(0)


def _empty():
    return AgentTurn(assistant_blocks=[], stop_reason="end_turn")


def _reply(text):
    return AgentTurn(assistant_blocks=[TextBlock(text=text)], stop_reason="end_turn")


def _tool_call(stop_reason="tool_use"):
    # An unregistered tool: run_turn answers it with an error tool_result and
    # carries on, which is enough to put tool activity earlier in the turn.
    tu = ToolUse(id="t1", name="Nope", input={})
    return AgentTurn(assistant_blocks=[ToolUseBlock(id="t1", name="Nope", input={})],
                     tool_uses=[tu], stop_reason=stop_reason)


async def _run(provider, gate_on):
    sess = AgentSession(id="t", messages=[], provider_kind="openai")
    turn = await run_turn(session=sess, user_text="do the task", provider=provider, tools={},
                          tool_consent=None, events=None, app_ref=_app(gate_on))
    return sess, turn


def _nudges(sess):
    return [m for m in sess.messages if m.get("role") == "user" and m.get("content") == NO_ACTION_GATE_MSG]


@pytest.mark.asyncio
async def test_empty_finish_is_nudged_and_the_turn_continues():
    provider = _ScriptedProvider([_empty(), _reply("done")])
    sess, turn = await _run(provider, gate_on=True)
    assert provider.calls == 2
    assert len(_nudges(sess)) == 1
    assert turn.assistant_blocks[0].text == "done"


@pytest.mark.asyncio
async def test_gate_fires_at_most_once_per_turn():
    # A model that stays silent must not loop forever on the nudge.
    provider = _ScriptedProvider([_empty(), _empty(), _reply("never reached")])
    sess, _ = await _run(provider, gate_on=True)
    assert provider.calls == 2
    assert len(_nudges(sess)) == 1


@pytest.mark.asyncio
async def test_a_reply_in_words_is_not_nudged():
    # Answering a question without touching a file is a legitimate finish.
    provider = _ScriptedProvider([_reply("12 and 10 are correct; nothing to fix")])
    sess, _ = await _run(provider, gate_on=True)
    assert provider.calls == 1
    assert _nudges(sess) == []


@pytest.mark.asyncio
async def test_whitespace_only_reply_counts_as_empty():
    provider = _ScriptedProvider([_reply("  \n "), _reply("done")])
    sess, _ = await _run(provider, gate_on=True)
    assert provider.calls == 2
    assert len(_nudges(sess)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_empty_assistant_message_is_not_sent_back(kind):
    # Anthropic rejects a non-final message with empty content; resending the
    # empty finish alongside the nudge would turn the gate into a 400.
    provider = _ScriptedProvider([_empty(), _reply("done")], kind=kind)
    await _run(provider, gate_on=True)
    retry = provider.sent[1]
    assistants = [m for m in retry if m["role"] == "assistant"]
    assert all(m["content"] not in ("", []) for m in assistants)
    assert retry[-1]["content"] == NO_ACTION_GATE_MSG


@pytest.mark.asyncio
async def test_empty_finish_after_tool_work_is_still_nudged():
    # The measured failure: one Read, then an empty finish. Earlier tool calls
    # must not exempt the turn.
    provider = _ScriptedProvider([_tool_call(), _empty(), _reply("done")])
    sess, _ = await _run(provider, gate_on=True)
    assert provider.calls == 3
    assert len(_nudges(sess)) == 1


def test_nudge_does_not_claim_nothing_was_done():
    # Earlier tool calls may already have changed files; telling a weak model
    # "nothing was done" invites it to repeat a non-idempotent step.
    assert "nothing was done" not in NO_ACTION_GATE_MSG.lower()


@pytest.mark.asyncio
async def test_a_tool_call_with_end_turn_stop_is_not_an_empty_finish():
    provider = _ScriptedProvider([_tool_call(stop_reason="end_turn")])
    sess, _ = await _run(provider, gate_on=True)
    assert provider.calls == 1
    assert _nudges(sess) == []


@pytest.mark.asyncio
async def test_flag_off_leaves_behaviour_unchanged():
    provider = _ScriptedProvider([_empty(), _reply("unreached")])
    sess, _ = await _run(provider, gate_on=False)
    assert provider.calls == 1
    assert _nudges(sess) == []
