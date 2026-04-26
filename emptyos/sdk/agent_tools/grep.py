"""Grep tool — search file contents via the search capability.

Auto-permission. Routes through `kernel.capability("search")` (`GrepSearchProvider`),
so plugins that enhance search at startup participate too. Returns matching
file paths by default; pass `mode="content"` for matching lines with context.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult, resolve_path


MAX_RESULTS = 200
CONTENT_MAX_LINES = 400


class GrepTool(Tool):
    name = "Grep"
    description = (
        "Search file contents for a regex pattern. "
        "Use `mode=files_with_matches` (default) to list files, `mode=content` for matching lines with line numbers. "
        "Filter by glob (e.g. '*.py') or type ('py', 'md'). Case-insensitive by default."
    )
    permission = "auto"
    readonly = True  # plan-mode safe
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Path to search in (default: current working directory)"},
            "mode": {"type": "string", "enum": ["files_with_matches", "content"], "description": "Output mode"},
            "glob": {"type": "string", "description": "Glob filter, e.g. '*.py' or '**/*.md'"},
            "type": {"type": "string", "description": "File type filter (ripgrep --type), e.g. 'py', 'md'"},
            "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (default true)"},
            "context": {"type": "integer", "description": "Lines of context around each match (mode=content only)"},
        },
        "required": ["pattern"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        if not pattern:
            return ToolResult(ok=False, content="error: pattern is required")
        if app is None or not hasattr(app, "kernel"):
            return ToolResult(ok=False, content="error: agent app reference unavailable")

        mode = kwargs.get("mode", "files_with_matches")
        if mode not in ("files_with_matches", "content"):
            return ToolResult(ok=False, content=f"error: unknown mode {mode!r}")

        case_insensitive = kwargs.get("case_insensitive")
        if case_insensitive is None:
            case_insensitive = True

        ctx_raw = kwargs.get("context")
        try:
            context = int(ctx_raw) if ctx_raw is not None else 0
        except (TypeError, ValueError):
            context = 0

        limit = CONTENT_MAX_LINES if mode == "content" else MAX_RESULTS

        # Resolve path (defaults to repo root so `apps/foo` behaves sanely)
        raw_path = kwargs.get("path", "") or ""
        search_path = str(resolve_path(app, raw_path)) if raw_path else ""

        try:
            result = await app.kernel.capability("search").execute(
                query=pattern,
                path=search_path,
                mode=mode,
                case_insensitive=bool(case_insensitive),
                glob=kwargs.get("glob", "") or "",
                type=kwargs.get("type", "") or "",
                context=context,
                limit=limit,
            )
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        records = result.value if hasattr(result, "value") else result
        if not records:
            return ToolResult(ok=True, content="(no matches)", display={"matches": 0, "mode": mode})

        if mode == "files_with_matches":
            out = "\n".join(r["path"] for r in records)
            return ToolResult(ok=True, content=out, display={"matches": len(records), "mode": mode})

        # content mode
        lines = [f"{r['path']}:{r['line_number']}:{r['text']}" for r in records]
        out = "\n".join(lines)
        return ToolResult(ok=True, content=out, display={"matches": len(records), "mode": mode})
