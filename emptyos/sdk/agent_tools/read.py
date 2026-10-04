"""Read tool — read a file from repo or vault.

Auto-permission: reading is non-destructive. Adds `cat -n` line numbers and
truncates very long files with a tail marker so the model doesn't burn context
on boilerplate.
"""

from __future__ import annotations

import hashlib

from emptyos.sdk.agent_tools.base import Tool, ToolResult, feature_enabled, resolve_path

MAX_LINES = 2000
MAX_BYTES = 500_000


def line_hash(line: str, n: int = 12) -> str:
    """Short deterministic content hash of one line (no trailing newline).

    blake2b with a 6-byte digest → 12 hex chars: short enough to sit in a
    Read column, collision-resistant enough to anchor an Edit. The Edit
    tool recomputes this over the file's current line content and rejects
    on mismatch, so a stale anchor is caught instead of silently editing
    the wrong span. Shared by ``read.py`` (emit) and ``edit.py`` (verify).
    """
    return hashlib.blake2b(line.encode("utf-8"), digest_size=n // 2).hexdigest()


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
            "include_hashes": {
                "type": "boolean",
                "description": (
                    "Prepend a short per-line content hash column. Use before a "
                    "line-anchored Edit (pass the hash as Edit's `expected_hash`)."
                ),
            },
        },
        "required": ["path"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        # Gated: only emit the hash column when the dark flag is on. Off → the
        # output is byte-identical to the historical `cat -n` format.
        include_hashes = bool(kwargs.get("include_hashes")) and feature_enabled(app, "hash-edit")
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

        if include_hashes:
            numbered = "\n".join(
                f"{line_hash(line)}  {start + i + 1:6d}\t{line}" for i, line in enumerate(shown)
            )
        else:
            numbered = "\n".join(f"{start + i + 1:6d}\t{line}" for i, line in enumerate(shown))
        header = f"{path}  (lines {start + 1}-{end} of {total})\n"
        if end < total:
            numbered += f"\n... {total - end} more lines"

        return ToolResult(
            ok=True,
            content=header + numbered,
            display={"path": path, "lines_shown": len(shown), "total_lines": total},
        )
