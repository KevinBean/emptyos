"""Write tool — write a file to an absolute path.

Permission: always `ask`. Write is destructive — it overwrites in full.
Parent directory must already exist; use Bash(mkdir) if not. The permission
summary surfaces whether the write creates a new file or overwrites an
existing one, and the byte count, so the user can judge blast radius.
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, resolve_path, unified_diff

MAX_BYTES = 5_000_000  # 5 MB — well above any reasonable text file
PREVIEW_HEAD_LINES = 40


class WriteTool(Tool):
    name = "Write"
    description = (
        "Write content to a file at an absolute path. Overwrites existing files in full. "
        "Parent directories are created automatically if missing. For targeted edits to "
        "an existing file, prefer Edit — Write is for creating new files or full rewrites."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute file path"},
            "content": {"type": "string", "description": "File contents (UTF-8 text)"},
        },
        "required": ["path", "content"],
    }

    def permission_summary(self, input: dict) -> str:
        path = input.get("path", "")
        content = input.get("content", "") or ""
        # Permission summary runs without an app ref, so we can't resolve to
        # repo root here — fall back to raw path for the preview only.
        exists = Path(path).exists() if path and Path(path).is_absolute() else False
        verb = "Overwrite" if exists else "Create"
        return f"Write: {verb} {path}  ({len(content)} bytes)"

    async def run(self, app, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        content = kwargs.get("content")
        if not path:
            return ToolResult(ok=False, content="error: path is required")
        if content is None:
            return ToolResult(ok=False, content="error: content is required")
        if not isinstance(content, str):
            return ToolResult(ok=False, content="error: content must be a string")

        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            return ToolResult(
                ok=False,
                content=f"error: content too large ({len(data)} bytes, max {MAX_BYTES})",
            )

        p = resolve_path(app, path)
        # Create missing parent directories — mirrors Claude Code's Write behaviour
        # and saves the model a round-trip through Bash(mkdir).
        if not p.parent.exists():
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return ToolResult(
                    ok=False,
                    content=f"error: could not create parent directory {p.parent}: {e}",
                )
        if p.exists() and not p.is_file():
            return ToolResult(ok=False, content=f"error: not a file: {path}")

        existed = p.exists()
        prev_bytes = p.stat().st_size if existed else 0
        prev_text = ""
        if existed:
            try:
                prev_text = p.read_bytes().decode("utf-8")
            except Exception:
                prev_text = ""  # binary or unreadable — skip diff

        try:
            p.write_bytes(data)
        except Exception as e:
            return ToolResult(ok=False, content=f"error: {e}")

        diff = unified_diff(prev_text, content, path) if existed and prev_text else ""
        preview = ""
        if not existed:
            head = content.splitlines()[:PREVIEW_HEAD_LINES]
            preview = "\n".join(head)
            if len(content.splitlines()) > PREVIEW_HEAD_LINES:
                preview += f"\n... ({len(content.splitlines()) - PREVIEW_HEAD_LINES} more lines)"

        verb = "Overwrote" if existed else "Created"
        return ToolResult(
            ok=True,
            content=f"{verb} {path}  ({len(data)} bytes, was {prev_bytes})",
            display={
                "path": str(p),  # absolute path — /revert needs this to find the file
                "action": "overwrite" if existed else "create",
                "bytes": len(data),
                "previous_bytes": prev_bytes,
                "previous_content": prev_text,  # raw pre-edit bytes, for /revert
                "diff": diff,
                "preview": preview,
            },
        )
