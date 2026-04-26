"""Python tool — run a snippet and capture its output.

Runs in a subprocess (`sys.executable`) to isolate execution from the daemon
process. stdout + stderr are captured; the exit code is reported. Imports,
side-effects, and writes are all possible — `permission` is always "ask".

Useful for: quick calculations, data transformations, prototyping a snippet
before writing it to a file, verifying a regex or JSON parse, running a test
inline.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import textwrap
from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, repo_root

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_CHARS = 20_000


class PythonTool(Tool):
    name = "Python"
    description = (
        "Execute a Python snippet and return its stdout/stderr. Runs in an isolated "
        "subprocess — safe for quick calculations, parsing, probing behavior, or "
        "data transforms. Use print() to produce output; the return value of the "
        "last expression is NOT automatically printed (add print() explicitly). "
        "Always asks permission — Python can do arbitrary things."
    )
    permission = "ask"
    readonly = False
    input_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source to execute. Multi-line is fine.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {DEFAULT_TIMEOUT}).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (default: repo root).",
            },
            "description": {
                "type": "string",
                "description": "One-line summary of what this snippet does.",
            },
        },
        "required": ["code"],
    }

    def permission_summary(self, input: dict) -> str:
        code = input.get("code", "").strip()
        desc = input.get("description", "")
        first_line = code.split("\n")[0][:80]
        label = desc or first_line
        return f"Python: {label}"

    async def run(self, app, **kwargs) -> ToolResult:
        code = kwargs.get("code", "").strip()
        if not code:
            return ToolResult(ok=False, content="error: code is required")

        t_raw = kwargs.get("timeout")
        try:
            timeout = int(t_raw) if t_raw is not None else DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        cwd_raw = kwargs.get("cwd", "") or None
        if cwd_raw:
            cwd_path = Path(cwd_raw)
            cwd = str(cwd_path if cwd_path.is_absolute() else (repo_root(app) / cwd_path).resolve())
        else:
            cwd = str(repo_root(app))

        # Write to a temp file so multi-line code and special characters survive
        # the subprocess argv boundary without escaping.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(textwrap.dedent(code))
            tmp_path = tmp.name

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(ok=False, content=f"error: timed out after {timeout}s")
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        exit_code = proc.returncode or 0

        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        parts.append(f"[exit {exit_code}]")
        content = "\n".join(parts)

        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS] + f"\n… (truncated at {MAX_OUTPUT_CHARS} chars)"

        return ToolResult(
            ok=exit_code == 0,
            content=content,
            display={"exit_code": exit_code, "stdout_chars": len(out), "stderr_chars": len(err)},
        )
