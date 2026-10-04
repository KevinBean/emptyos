"""Tool registry for the agent app.

The registry is built lazily at app setup time so that individual tools can
import BaseApp lazily without circular-import pain. Call `build_registry()`
once per AgentApp instance; pass the result into the loop.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.artifact import CreateArtifactTool
from emptyos.sdk.agent_tools.base import Tool, ToolResult
from emptyos.sdk.agent_tools.bash import BashTool
from emptyos.sdk.agent_tools.browse import BrowseTool
from emptyos.sdk.agent_tools.call_app import CallAppTool
from emptyos.sdk.agent_tools.context_ref import ContextRefTool
from emptyos.sdk.agent_tools.delete_function import DeleteFunctionTool
from emptyos.sdk.agent_tools.edit import EditTool
from emptyos.sdk.agent_tools.fetch import FetchTool
from emptyos.sdk.agent_tools.glob import GlobTool
from emptyos.sdk.agent_tools.grep import GrepTool
from emptyos.sdk.agent_tools.locate import LocateTool
from emptyos.sdk.agent_tools.python import PythonTool
from emptyos.sdk.agent_tools.read import ReadTool
from emptyos.sdk.agent_tools.restart_daemon import RestartDaemonTool
from emptyos.sdk.agent_tools.screenshot import ScreenshotTool
from emptyos.sdk.agent_tools.skill import SkillTool
from emptyos.sdk.agent_tools.subagent import SubAgentTool
from emptyos.sdk.agent_tools.task_list import TaskListTool
from emptyos.sdk.agent_tools.vault_query import VaultQueryTool
from emptyos.sdk.agent_tools.web_search import WebSearchTool
from emptyos.sdk.agent_tools.write import WriteTool

# Full tool set: read (Read/Grep/Glob/Bash/Python/WebSearch/VaultQuery) +
# write (Write/Edit/DeleteFunction) + cross-app dispatch (CallApp) +
# Claude-Code-compatible Skill loader + SubAgent for delegated subtasks.
V1_TOOLS: list[type[Tool]] = [
    ReadTool,
    GrepTool,
    GlobTool,
    LocateTool,
    BashTool,
    PythonTool,
    WebSearchTool,
    WriteTool,
    EditTool,
    DeleteFunctionTool,
    CallAppTool,
    SkillTool,
    TaskListTool,
    FetchTool,
    VaultQueryTool,
    RestartDaemonTool,
    ScreenshotTool,
    SubAgentTool,
    ContextRefTool,
    CreateArtifactTool,
]


def build_registry(
    enabled: list[str] | None = None, extra_tools: list[Tool] | None = None
) -> dict[str, Tool]:
    """Instantiate the v1 tool registry, optionally filtered by name.

    Returns `{tool.name: tool_instance}`. Filter is case-sensitive and applied
    against `Tool.name` (e.g. "Read", "Bash").

    `extra_tools` are appended verbatim (already-instantiated `Tool`s) — used by
    the inbound-MCP wiring to add `MCPProxyTool`s for external MCP servers. They
    bypass the `enabled` name filter (the caller already decided to include them)
    and overwrite a native tool only if names collide (MCP names are namespaced
    `mcp__server__tool`, so they don't in practice).
    """
    registry: dict[str, Tool] = {}
    for cls in V1_TOOLS:
        inst = cls()
        if enabled is None or inst.name in enabled:
            registry[inst.name] = inst
    for inst in extra_tools or []:
        registry[inst.name] = inst
    return registry


__all__ = [
    "Tool",
    "ToolResult",
    "ReadTool",
    "GrepTool",
    "GlobTool",
    "LocateTool",
    "BashTool",
    "PythonTool",
    "WebSearchTool",
    "WriteTool",
    "EditTool",
    "DeleteFunctionTool",
    "CallAppTool",
    "SkillTool",
    "TaskListTool",
    "FetchTool",
    "VaultQueryTool",
    "RestartDaemonTool",
    "ScreenshotTool",
    "BrowseTool",
    "SubAgentTool",
    "ContextRefTool",
    "CreateArtifactTool",
    "V1_TOOLS",
    "build_registry",
]
