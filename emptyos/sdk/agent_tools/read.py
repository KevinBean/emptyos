"""Read tool — read a file from repo or vault.

Auto-permission: reading is non-destructive. Adds `cat -n` line numbers and
truncates very long files with a tail marker so the model doesn't burn context
on boilerplate.
"""

from __future__ import annotations

from emptyos.sdk.agent_tools.base import Tool, ToolResult, resolve_path

MAX_LINES = 2000
MAX_BYTES = 500_000


class ReadTool(Tool):
    name = "Read"
    description = (
        "Read a file by absolute path. Returns contents with line numbers. "
        "Use for source files, markdown notes, config. Files larger than "
        f"{MAX_LINES} lines are truncated — pass `offset` and `limit` to read specific ranges."
    )
    permission = "auto"
    readonly = True  # plan-mode safe
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "offset": {
                "type": "integer",
                "description": "Line to start reading from (1-indexed). Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines to return. Default {MAX_LINES}.",
            },
        },
        "required": ["path"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        # Models sometimes pass `null`/None explicitly for optional ints — treat as default.
        offset_raw = kwargs.get("offset")
        limit_raw = kwargs.get("limit")
        try:
            offset = int(offset_raw) if offset_raw is not None else 1
        except (TypeError, ValueError):
            offset = 1
        try:
            limit = int(limit_raw) if limit_raw is not None else MAX_LINES
        except (TypeError, ValueError):
            limit = MAX_LINES

        if not path:
            return ToolResult(ok=False, content="error: path is required")

        p = resolve_path(app, path)
        if not p.exists():
            return ToolResult(ok=False, content=f"error: file not found: {path}")
        if not p.is_file():
            return ToolResult(ok=False, content=f"error: not a file: {path}")

        try:
            raw = p.read_bytes()
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        if len(raw) > MAX_BYTES:
            return ToolResult(
                ok=False,
                content=f"error: file too large ({len(raw)} bytes, max {MAX_BYTES}). Use Grep or read specific line ranges.",
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"error: not a UTF-8 text file: {path}")

        lines = text.splitlines()
        total = len(lines)
        start = max(0, offset - 1)
        end = min(total, start + limit)
        shown = lines[start:end]

        numbered = "\n".join(f"{start + i + 1:6d}\t{line}" for i, line in enumerate(shown))
        header = f"{path}  (lines {start + 1}-{end} of {total})\n"
        if end < total:
            numbered += f"\n... {total - end} more lines"

        return ToolResult(
            ok=True,
            content=header + numbered,
            display={"path": path, "lines_shown": len(shown), "total_lines": total},
        )
