"""SubAgent tool — delegate a focused subtask to a nested agent turn.

Spawns a fresh AgentSession and calls run_turn() with the given task.
Events are NOT forwarded to the parent turn's WebSocket — the sub-turn is
silent from the UI's perspective. The tool returns the final assistant text
plus a summary of which tools were used.

Useful for:
  • Parallelizable research subtasks ("find all routes that touch X", "summarise file Y")
  • Isolated write tasks that need their own tool loop
  • Breaking a large task into independently verifiable steps

Design notes:
  • Uses the parent app's default provider — no provider selection at call time.
  • Uses the same tool_consent manager as the parent turn (risky tools still ask).
  • The sub-turn shares the parent app's tool hooks (audit log, task persist).
  • max_iters defaults to 10 — subagents should be focused; long loops mean the
    task should be done by the parent instead.
"""

from __future__ import annotations

import asyncio
import time

from emptyos.sdk.agent_tools.base import Tool, ToolResult

MAX_OUTPUT_CHARS = 12_000


class SubAgentTool(Tool):
    name = "SubAgent"
    description = (
        "Delegate a focused subtask to a nested agent turn. The subagent has access "
        "to the same tools (Read, Grep, Glob, Bash, Write, Edit, Python, Fetch, etc.) "
        "and runs its own tool loop until the task is complete. Returns the final "
        "answer text and a list of tools it used. "
        "Best for isolated, well-scoped subtasks (research, single-file edits, "
        "data transforms). Don't nest subagents more than 1 level deep. Always "
        "asks permission before spawning."
    )
    permission = "ask"
    readonly = False
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the subagent — be specific and self-contained.",
            },
            "system": {
                "type": "string",
                "description": "Optional extra system-prompt context (appended after the default).",
            },
            "max_iters": {
                "type": "integer",
                "description": "Max tool-use iterations (default 10; cap 20).",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict the subagent to these tool names. Omit to give the full tool set."
                ),
            },
        },
        "required": ["task"],
    }

    def permission_summary(self, input: dict) -> str:
        task = input.get("task", "")[:80]
        return f"SubAgent: {task}"

    async def run(self, app, **kwargs) -> ToolResult:
        task = (kwargs.get("task") or "").strip()
        if not task:
            return ToolResult(ok=False, content="error: task is required")

        extra_system = (kwargs.get("system") or "").strip()
        max_iters_raw = kwargs.get("max_iters", 10)
        try:
            max_iters = min(int(max_iters_raw), 20)
        except (TypeError, ValueError):
            max_iters = 10
        allowed_tools = kwargs.get("tools")  # list[str] | None

        # Lazy imports — agent_loop is heavy; avoid loading at tool-registry build time.
        from emptyos.sdk.agent_loop import (
            DEFAULT_SYSTEM_PROMPT,
            DEFAULT_TEMPERATURE,
            AgentSession,
            run_turn,
        )
        from emptyos.sdk.agent_tools import build_registry

        # Resolve provider — use the app's default (same as the parent turn).
        provider_name = app._default_provider_name()
        provider = app._resolve_provider(provider_name)
        if provider is None:
            return ToolResult(
                ok=False,
                content=f"error: no tool-capable provider available (tried {provider_name!r})",
            )

        tool_consent = app.service("tool_consent")

        # Build tool registry for the sub-turn (optionally filtered).
        tools = build_registry(enabled=allowed_tools)

        sub_sid = f"sub_{int(time.time() * 1000)}"
        sub_session = AgentSession(id=sub_sid)

        system = DEFAULT_SYSTEM_PROMPT
        if extra_system:
            system = system + "\n\n" + extra_system

        # Minimal event collector — captures tool names without touching the WS.
        events_log: list[tuple[str, dict]] = []

        class _MinimalBus:
            async def emit(self, event_type: str, data: dict = None, **_):
                events_log.append((event_type, data or {}))

        minimal_bus = _MinimalBus()

        try:
            final_turn = await asyncio.wait_for(
                run_turn(
                    session=sub_session,
                    user_text=task,
                    provider=provider,
                    tools=tools,
                    tool_consent=tool_consent,
                    events=minimal_bus,
                    app_ref=app,
                    system=system,
                    max_iters=max_iters,
                    temperature=DEFAULT_TEMPERATURE,
                ),
                timeout=180.0,
            )
        except TimeoutError:
            return ToolResult(ok=False, content="error: subagent timed out after 180s")
        except Exception as e:
            return ToolResult(ok=False, content=f"error: subagent failed — {e}")

        # Extract final text from the turn
        from emptyos.capabilities.providers._tool_capable import TextBlock

        text_parts = [
            b.text for b in (final_turn.assistant_blocks or []) if isinstance(b, TextBlock)
        ]
        result_text = "".join(text_parts).strip()

        # Summarise tools from events
        tools_used: list[str] = []
        for event_type, data in events_log:
            if event_type == "agent:tool_call":
                name = data.get("name", "")
                if name and name not in tools_used:
                    tools_used.append(name)

        iters = len([e for e, _ in events_log if e == "agent:iter_start"])

        summary = f"[SubAgent: {iters} iter(s), tools: {', '.join(tools_used) or 'none'}]\n\n"
        content = summary + result_text

        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS] + "\n… (truncated)"

        return ToolResult(
            ok=bool(result_text),
            content=content,
            display={
                "task": task[:120],
                "iters": iters,
                "tools_used": tools_used,
                "chars": len(result_text),
            },
        )
