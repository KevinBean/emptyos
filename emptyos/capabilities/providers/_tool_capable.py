"""Tool-capable provider contract.

A `ToolCapableProvider` extends the base `Provider` with `execute_tools()` —
a single model round-trip that accepts tool schemas, returns assistant blocks
(text and/or tool_use calls), and a stop_reason the agent loop uses to decide
whether to dispatch tools and continue, or finish the turn.

Provider adapters (Anthropic SDK, OpenAI function calling, JSON fallback) all
produce the same `AgentTurn` shape regardless of the underlying wire format.
The loop in `emptyos/sdk/agent_loop.py` stays provider-agnostic.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Literal

from emptyos.capabilities import Provider


StopReason = Literal["tool_use", "end_turn", "max_tokens", "stop_sequence", "cancelled", "error"]


@dataclass
class ToolUse:
    """A model-emitted tool invocation."""
    id: str
    name: str
    input: dict


@dataclass
class TextBlock:
    """A model-emitted text block."""
    text: str

    @property
    def type(self) -> str:
        return "text"


@dataclass
class ToolUseBlock:
    """A model-emitted tool_use block (assistant message content)."""
    id: str
    name: str
    input: dict

    @property
    def type(self) -> str:
        return "tool_use"


@dataclass
class AgentTurn:
    """One model round-trip.

    `assistant_blocks` is the ordered list of text and tool_use blocks the
    model produced — stored verbatim for the next turn's message history so
    tool_use/tool_result IDs round-trip losslessly.

    `tool_uses` is a convenience view of the tool_use blocks for dispatch.

    `stop_reason == "tool_use"` means the loop should dispatch tools and
    continue. Any other value terminates the turn.
    """
    assistant_blocks: list[TextBlock | ToolUseBlock] = field(default_factory=list)
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: StopReason = "end_turn"
    usage: dict = field(default_factory=dict)
    raw: Any = None


class ToolCapableProvider(Provider):
    """Provider that speaks tool-use natively.

    Adapters (anthropic_sdk, openai_compat.execute_tools, json_fallback) subclass
    this and implement `execute_tools()`. The `kind` attribute tells the Tool
    registry which wire format to serialize to.

    Used by `run_turn()` — our own loop drives the provider round-by-round,
    dispatching our tools, injecting our tool_results.
    """

    kind: Literal["anthropic", "openai", "json"] = "openai"

    async def execute_tools(
        self,
        *,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AgentTurn:
        """One model round-trip with tool schemas available.

        `messages` follows the provider's native content-block shape for the
        adapter — the caller (agent loop) stores messages verbatim, so if this
        provider returns Anthropic-style blocks they go back in as Anthropic
        messages on the next turn. Cross-provider session switching is not
        supported in v1.

        `tools` is the already-serialized list (via Tool.to_anthropic/to_openai).
        """
        raise NotImplementedError

    async def execute_tools_stream(
        self,
        *,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs,
    ) -> AsyncGenerator[dict, None]:
        """Stream incremental events, then yield a final `{"turn": AgentTurn}` chunk.

        Chunk shapes:
            {"text_delta": str}               — assistant text chunk
            {"tool_use_start": {"id","name"}}  — model began a tool_use block
            {"tool_use_delta": {"id","partial_input": str}} — args arriving
            {"tool_use_end": {"id"}}           — tool_use block complete
            {"turn": AgentTurn}                — final turn, must be last

        Default implementation wraps execute_tools() in a single `turn` chunk.
        Adapters override for real streaming.
        """
        turn = await self.execute_tools(
            messages=messages, system=system, tools=tools or [], **kwargs
        )
        yield {"turn": turn}


class NativelyAgenticProvider(Provider):
    """Provider that runs its own agent loop internally.

    The canonical example is the `claude` CLI subprocess: we hand it a prompt,
    it thinks, calls its own tools (with its own permission model), and streams
    back text + tool narration. There is no protocol for us to inject `tool_result`
    blocks back — the whole turn is delegated.

    Subclasses already implement `execute_stream()` in the agent-narration shape:
        {"text": str, "done": bool}                           — assistant text
        {"tool_status": str, "tool": str, "done": False}       — tool narration
        {"text": "", "done": True}                             — turn complete

    Used by `run_native_turn()` in the agent app — our loop does not drive the
    provider; instead, we pass events through to the UI and persist the final
    assistant text. Custom tool registry and permission gate don't apply here —
    the CLI enforces its own.
    """

    is_natively_agentic: bool = True

    # Native agents run their own tool loop with their own gate, so EmptyOS
    # permission prompts don't fire. Apps / UIs can check this flag to show
    # a banner like "tool calls handled by claude, not gated by EmptyOS".
    native_tool_summary: str = "this provider's built-in tools"
