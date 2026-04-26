"""Tool base class.

Tools are the verbs the agent loop can invoke. Each tool declares a JSONSchema
input shape, a permission class (auto/ask/deny), and an async run() that
dispatches the actual work — usually by delegating to a BaseApp primitive
(self.read, self.write, kernel.capability("search"), etc.).

The same Tool produces different wire formats for different providers:
- `to_anthropic()` → `{"name","description","input_schema"}`
- `to_openai()` → `{"type":"function","function":{"name","description","parameters"}}`
- `to_json()` → a line in a JSON-rubric system prompt (fallback path)

`run()` returns `{"ok": bool, "content": str, "display": dict}`:
- `content` is the string fed back to the model as tool_result
- `display` is an optional structured payload for the UI (diff, table, etc.)
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk import BaseApp


Permission = Literal["auto", "ask", "deny"]


def repo_root(app: "BaseApp") -> Path:
    """Resolve the EmptyOS repo root — the directory containing `emptyos.toml`.

    Relative paths in tool calls resolve against this, not the process CWD.
    Thin wrapper over ``app.repo_root`` so the tool-module helpers
    (``resolve_path(app, ...)``) keep the same positional-arg shape.
    Falls back to ``Path.cwd()`` when called without an app — mostly hit by
    unit tests that exercise tool primitives in isolation.
    """
    try:
        return app.repo_root
    except AttributeError:
        return Path.cwd()


def resolve_path(app: "BaseApp", path: str) -> Path:
    """Resolve a tool-provided path. Absolute paths are returned as-is; relative
    paths are joined to the repo root so the model's mental model ("apps/foo")
    matches where files actually land."""
    if not path:
        return Path(path)
    p = Path(path)
    if p.is_absolute():
        return p
    return (repo_root(app) / p).resolve()


def unified_diff(before: str, after: str, path: str, max_lines: int = 400) -> str:
    """Produce a unified diff string for a tool's display payload, bounded so
    the WebSocket event stays small even on big edits. Empty when inputs match.
    """
    b = before.splitlines(keepends=True)
    a = after.splitlines(keepends=True)
    lines = list(difflib.unified_diff(b, a, fromfile=path, tofile=path, n=3))
    if not lines:
        return ""
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(f"... (diff clipped at {max_lines} lines)\n")
    return "".join(lines)


@dataclass
class ToolResult:
    ok: bool
    content: str
    display: dict | None = None


class Tool:
    """Base class for agent tools."""

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    permission: Permission = "ask"
    # True when a tool is side-effect-free for the local machine and the wider
    # world: pure investigation. Read/Grep/Glob/Skill/TaskList/Screenshot qualify;
    # Write/Edit/Bash/RestartDaemon/CallApp do not. Fetch overrides `is_readonly()`
    # to return True only for GET. This flag gates plan mode.
    readonly: bool = False

    def is_readonly(self, input: dict) -> bool:
        """Override for tools whose read-only-ness depends on input (e.g.
        Fetch — GET is read-only, POST isn't)."""
        return self.readonly

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_json_rubric(self) -> str:
        """Line for a JSON-rubric system prompt (fallback provider)."""
        props = self.input_schema.get("properties", {})
        param_list = ", ".join(f"{k}: {v.get('type','?')}" for k, v in props.items())
        return f"- {self.name}({param_list}) — {self.description}"

    def to_wire(self, kind: str) -> dict | str:
        if kind == "anthropic":
            return self.to_anthropic()
        if kind == "openai":
            return self.to_openai()
        if kind == "json":
            return self.to_json_rubric()
        raise ValueError(f"unknown wire format: {kind}")

    def permission_summary(self, input: dict) -> str:
        """One-line human-readable summary for the permission prompt."""
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in input.items())})"

    async def run(self, app: "BaseApp", **kwargs) -> ToolResult:
        """Execute the tool. Subclasses implement."""
        raise NotImplementedError
