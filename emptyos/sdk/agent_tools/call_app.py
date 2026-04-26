"""CallApp tool — invoke another app's method in-process.

This is the tool that pulls EmptyOS ahead of a generic coding agent: the
agent can do `task.add_task("fix the thing")`, `journal.add_entry(...)`,
`projects.add_task_to_project(project_id, text)` — anything exposed on
any loaded app. No HTTP, no serialization overhead, just Python calls
routed through the kernel's app registry.

Permission: always `ask`. Side effects are unbounded — an app method can
write vault files, mutate DB state, or emit events that ripple through
reactors. Surface the call in the permission summary so the user can
judge.

Safety rails:
- Private methods (starting with `_`) are rejected
- Lifecycle methods (setup, teardown, shutdown, unload, reload) are rejected
- Unknown app / method returns a helpful error with the inventory
"""

from __future__ import annotations

import inspect
import json

from emptyos.sdk.agent_tools.base import Tool, ToolResult


BLOCKED_METHODS = {
    "setup", "teardown", "shutdown", "unload", "reload",
    "init", "__init__", "__del__",
}

MAX_RESULT_CHARS = 30_000


class CallAppTool(Tool):
    name = "CallApp"
    description = (
        "Call a method on another EmptyOS app (e.g. task.add_task, journal.add_entry, "
        "projects.add_task_to_project). Pass arguments as a JSON object. "
        "Call with no app_id to list available apps. Call with app_id only to list "
        "that app's public methods. Use this to wire the agent into the rest of "
        "the OS — creating tasks, journaling, querying projects, etc."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "app_id": {
                "type": "string",
                "description": "App ID (e.g. 'task', 'journal', 'projects'). Omit to list apps.",
            },
            "method": {
                "type": "string",
                "description": "Method name on the app. Omit to list methods on the app.",
            },
            "arguments": {
                "type": "object",
                "description": "Keyword arguments for the method (default: {})",
            },
        },
        "required": [],
    }

    def permission_summary(self, input: dict) -> str:
        app_id = input.get("app_id") or "?"
        method = input.get("method") or "?"
        args = input.get("arguments") or {}
        try:
            arg_str = json.dumps(args, default=str)
        except Exception:
            arg_str = str(args)
        if len(arg_str) > 120:
            arg_str = arg_str[:117] + "..."
        return f"CallApp: {app_id}.{method}({arg_str})"

    async def run(self, app, **kwargs) -> ToolResult:
        if app is None:
            return ToolResult(ok=False, content="error: no kernel context (app_ref was None)")

        app_id = (kwargs.get("app_id") or "").strip()
        method = (kwargs.get("method") or "").strip()
        arguments = kwargs.get("arguments") or {}
        if not isinstance(arguments, dict):
            return ToolResult(ok=False, content="error: arguments must be a JSON object")

        kernel = getattr(app, "kernel", None)
        if kernel is None or not hasattr(kernel, "apps"):
            return ToolResult(ok=False, content="error: kernel app registry not available")

        instances = kernel.apps.instances

        # No app_id → list apps
        if not app_id:
            apps_list = sorted(instances.keys())
            return ToolResult(
                ok=True,
                content="Available apps:\n" + "\n".join(f"  - {a}" for a in apps_list),
                display={"apps": apps_list},
            )

        # Resolve — auto-load if not running yet
        inst = instances.get(app_id)
        if inst is None:
            try:
                inst = await kernel.apps.load(app_id)
            except Exception as e:
                available = sorted(instances.keys())
                return ToolResult(
                    ok=False,
                    content=(
                        f"error: app {app_id!r} not found or failed to load: {e}\n"
                        f"available: {', '.join(available)}"
                    ),
                )

        # No method → list methods
        if not method:
            methods = _public_methods(inst)
            return ToolResult(
                ok=True,
                content=f"Methods on {app_id}:\n" + "\n".join(f"  - {m}" for m in methods),
                display={"app_id": app_id, "methods": methods},
            )

        # Safety gates
        if method.startswith("_"):
            return ToolResult(
                ok=False,
                content=f"error: private method {method!r} is not callable",
            )
        if method in BLOCKED_METHODS:
            return ToolResult(
                ok=False,
                content=f"error: lifecycle method {method!r} is not callable",
            )

        fn = getattr(inst, method, None)
        if fn is None or not callable(fn):
            methods = _public_methods(inst)
            return ToolResult(
                ok=False,
                content=(
                    f"error: {app_id}.{method} not found.\n"
                    f"available: {', '.join(methods)}"
                ),
            )

        # Dispatch
        try:
            result = fn(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except TypeError as e:
            return ToolResult(
                ok=False,
                content=f"error: bad arguments to {app_id}.{method}: {e}",
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                content=f"error: {app_id}.{method} raised {type(e).__name__}: {e}",
            )

        # Serialize the return value for the model
        try:
            content = json.dumps(result, default=str, indent=2, ensure_ascii=False)
        except Exception:
            content = str(result)
        if len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + f"\n... (truncated at {MAX_RESULT_CHARS} chars)"

        return ToolResult(
            ok=True,
            content=content,
            display={
                "app_id": app_id,
                "method": method,
                "result_chars": len(content),
            },
        )


def _public_methods(instance) -> list[str]:
    """Return sorted list of public, callable method names on an app instance."""
    out = []
    for name in dir(instance):
        if name.startswith("_"):
            continue
        if name in BLOCKED_METHODS:
            continue
        try:
            member = getattr(instance, name)
        except Exception:
            continue
        if callable(member):
            out.append(name)
    out.sort()
    return out
