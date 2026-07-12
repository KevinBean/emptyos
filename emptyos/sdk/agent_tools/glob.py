"""Glob tool — find files by pattern.

Auto-permission. Accepts either a relative pattern + `path` root, OR an
absolute pattern (in which case `path` is ignored). Sorted by modification
time (newest first).

Why both shapes: `pathlib.Path.glob()` raises on absolute patterns, but
the bench's system prompt overlay (and most concrete user phrasing) tells
the model to pass absolute paths to be unambiguous about location. Without
absolute support, a model that follows the overlay correctly gets a hard
"Non-relative patterns are unsupported" error and gives up. We route
absolute patterns through stdlib `glob.glob()` which handles them
natively.
"""

from __future__ import annotations

import glob as _glob
import os
from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, repo_root

MAX_RESULTS = 500


def _looks_absolute(pattern: str) -> bool:
    """True if the pattern is an absolute path (POSIX or Windows).

    `pathlib.PurePath` would also work but `os.path.isabs` is faster and
    matches the underlying glob library's notion of absoluteness.
    """
    if not pattern:
        return False
    # POSIX absolute or Windows drive-letter (e.g. "D:/...") or UNC ("//host/...")
    return os.path.isabs(pattern) or (len(pattern) >= 2 and pattern[1] == ":")


class GlobTool(Tool):
    name = "Glob"
    description = (
        "Find files matching a glob pattern like '**/*.py', 'apps/*/manifest.toml', "
        "or an absolute pattern like '/abs/dir/**/*.py'. Returns absolute paths "
        "sorted by modification time (newest first). When the pattern is relative, "
        "`path` (default: cwd) is the root."
    )
    permission = "auto"
    readonly = True  # plan-mode safe
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern. Absolute or relative."},
            "path": {
                "type": "string",
                "description": "Root to search from when pattern is relative. Ignored when pattern is absolute. Defaults to current working directory.",
            },
        },
        "required": ["pattern"],
    }

    async def run(self, app, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        if not pattern:
            return ToolResult(ok=False, content="error: pattern is required")

        root_str = kwargs.get("path", "") or ""

        try:
            if _looks_absolute(pattern):
                # stdlib glob handles absolute patterns natively; pathlib does not.
                # `recursive=True` enables `**` (matches pathlib's default behavior).
                matches = [Path(m) for m in _glob.glob(pattern, recursive=True)]
                root = Path(pattern).anchor or repo_root(app)
            else:
                # Relative roots (or default) resolve against the EmptyOS repo,
                # not the process CWD, so `apps/**/*.py` works from anywhere.
                if root_str:
                    candidate = Path(root_str)
                    root = (
                        candidate.resolve()
                        if candidate.is_absolute()
                        else (repo_root(app) / candidate).resolve()
                    )
                else:
                    root = repo_root(app)
                if not root.exists() or not root.is_dir():
                    return ToolResult(ok=False, content=f"error: not a directory: {root}")
                matches = list(root.glob(pattern))
        except Exception as e:
            return ToolResult(ok=False, content=f"error: bad glob: {e}")

        files = [p for p in matches if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        truncated = len(files) > MAX_RESULTS
        files = files[:MAX_RESULTS]

        if not files:
            return ToolResult(ok=True, content="(no matches)", display={"matches": 0})

        lines = [str(p).replace("\\", "/") for p in files]
        out = "\n".join(lines)
        if truncated:
            out += f"\n... {len(files)} of many (truncated at {MAX_RESULTS})"

        return ToolResult(ok=True, content=out, display={"matches": len(files), "root": str(root)})
