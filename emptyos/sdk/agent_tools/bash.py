"""Bash tool — run shell commands.

Uses `asyncio.create_subprocess_exec` with `shlex.split` — NOT shell=True.
That cuts off the shell-metacharacter attack surface: no redirects, no pipes,
no command substitution. For commands that need those, the model must use
multiple tool calls.

A small read-only allowlist auto-approves common inspection commands
(git status/log/diff, ls, cat, head, tail, rg, find). Everything else asks
for permission. The allowlist is a convenience, not a trust boundary — the
permission system is the real gate.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys
from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, repo_root

DEFAULT_TIMEOUT = 120  # seconds
MAX_OUTPUT_CHARS = 30_000

# Auto-approved prefixes (argv[0] + any required argv[1] markers).
# Written as tuples so we can match by exact-prefix on the split argv.
READ_ONLY_ALLOWLIST: list[tuple[str, ...]] = [
    ("git", "status"),
    ("git", "log"),
    ("git", "diff"),
    ("git", "show"),
    ("git", "branch"),
    ("git", "blame"),
    ("git", "rev-parse"),
    ("git", "remote"),
    ("ls",),
    ("cat",),
    ("head",),
    ("tail",),
    ("rg",),
    ("wc",),
    ("file",),
    ("pwd",),
    ("echo",),
    ("which",),
    ("whoami",),
    ("uname",),
    ("node", "--version"),
    ("python", "--version"),
    ("npm", "--version"),
    ("pip", "--version"),
    ("date",),
]


def matches_allowlist(argv: list[str]) -> bool:
    """True iff argv starts with any allowlisted prefix."""
    for prefix in READ_ONLY_ALLOWLIST:
        if len(argv) >= len(prefix) and tuple(argv[: len(prefix)]) == prefix:
            return True
    # Special: `find PATH -type f ...` (read-only discovery)
    if argv[:1] == ["find"] and "-type" in argv and "f" in argv:
        # Allow only when no `-delete`, `-exec` mutates
        if "-delete" not in argv and "-exec" not in argv:
            return True
    return False


SHELL_METACHARS = ("|", ">", "<", "&&", "||", ";", "`", "$(", "$((")


def _find_shell() -> tuple[str, list[str]] | None:
    """Locate a shell for metacharacter-using commands. Returns (executable, base_args).

    Priority:
      1. `bash` on PATH (Git Bash on Windows, system bash on Unix) — handles `ls`, `&&`, etc.
      2. `/bin/sh` on Unix
      3. `cmd.exe` on Windows (fallback — limited but always present)
    """
    bash = shutil.which("bash")
    if bash:
        return bash, ["-c"]
    if sys.platform != "win32" and Path("/bin/sh").exists():
        return "/bin/sh", ["-c"]
    if sys.platform == "win32":
        comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        return comspec, ["/c"]
    return None


def _needs_shell(command: str) -> bool:
    """Heuristic: command needs a shell if it contains any metacharacter,
    OR if argv[0] is a shell builtin / OS-specific binary that only exists
    in a shell context (`dir` on Windows, `echo` as a builtin, etc.)."""
    for m in SHELL_METACHARS:
        if m in command:
            return True
    # Try to detect argv[0] being a shell builtin or missing binary
    try:
        argv = shlex.split(command, posix=sys.platform != "win32")
    except ValueError:
        return True  # malformed → let the shell handle it
    if not argv:
        return False
    exe = argv[0]
    if sys.platform == "win32":
        # Windows builtins: dir, type, echo, copy, move, del, cls, set
        if exe.lower() in ("dir", "type", "copy", "move", "del", "cls", "set", "echo"):
            return True
    return False


class BashTool(Tool):
    name = "Bash"
    description = (
        "Run a shell command. Use for: git, build tools, test runners, inspection. "
        "Shell metacharacters (|, &&, ||, ;) and pipelines are supported via bash/sh. "
        "Relative paths resolve against the EmptyOS repo root (the directory containing "
        "emptyos.toml). 120s default timeout. Read-only commands (git status/log/diff, "
        "ls, cat, head, tail, rg, find -type f, --version checks) auto-approve."
    )
    permission = "ask"  # overridden per-call when allowlist matches
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command, e.g. 'git status' or 'ls apps/'. No pipes, redirects, or &&.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {DEFAULT_TIMEOUT})",
            },
            "cwd": {"type": "string", "description": "Working directory (default: repo root)"},
            "description": {
                "type": "string",
                "description": "Short 1-line description of what this command does",
            },
        },
        "required": ["command"],
    }

    def permission_for(self, input: dict) -> str:
        """Return 'auto' if command matches read-only allowlist, else 'ask'."""
        cmd = input.get("command", "")
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError:
            return "ask"
        if not argv:
            return "ask"
        return "auto" if matches_allowlist(argv) else "ask"

    def permission_summary(self, input: dict) -> str:
        cmd = input.get("command", "")
        desc = input.get("description", "")
        if desc:
            return f"Bash: {cmd}  # {desc}"
        return f"Bash: {cmd}"

    async def run(self, app, **kwargs) -> ToolResult:
        command = kwargs.get("command", "")
        if not command:
            return ToolResult(ok=False, content="error: command is required")

        t_raw = kwargs.get("timeout")
        try:
            timeout = int(t_raw) if t_raw is not None else DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT

        # Default cwd to the EmptyOS repo root so relative paths (apps/, tests/)
        # resolve the way the model expects, regardless of where `eos chat` ran from.
        cwd_raw = kwargs.get("cwd", "") or None
        if cwd_raw:
            cwd_path = Path(cwd_raw)
            cwd = str(cwd_path if cwd_path.is_absolute() else (repo_root(app) / cwd_path).resolve())
        else:
            cwd = str(repo_root(app))

        use_shell = _needs_shell(command)

        if use_shell:
            shell = _find_shell()
            if shell is None:
                return ToolResult(
                    ok=False,
                    content="error: command needs a shell (pipes/&&/shell-builtin) but no shell (bash, /bin/sh, cmd.exe) was found on PATH.",
                )
            shell_exe, shell_args = shell
            try:
                proc = await asyncio.create_subprocess_exec(
                    shell_exe,
                    *shell_args,
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            except FileNotFoundError:
                return ToolResult(ok=False, content=f"error: shell not found: {shell_exe}")
            except Exception as e:
                return ToolResult(ok=False, content=f"error: {e}")
        else:
            # Fast path: no shell, no metacharacters — run the argv directly.
            try:
                argv = shlex.split(command, posix=sys.platform != "win32")
            except ValueError as e:
                return ToolResult(ok=False, content=f"error: could not split command: {e}")
            if not argv:
                return ToolResult(ok=False, content="error: empty command after splitting")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            except FileNotFoundError:
                # Retry via shell — some commands exist only as shell builtins
                # or as shell-resolved executables (Git Bash coreutils on Windows).
                shell = _find_shell()
                if shell is None:
                    return ToolResult(ok=False, content=f"error: command not found: {argv[0]}")
                shell_exe, shell_args = shell
                try:
                    proc = await asyncio.create_subprocess_exec(
                        shell_exe,
                        *shell_args,
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                    )
                except Exception as e:
                    return ToolResult(ok=False, content=f"error: {e}")
            except Exception as e:
                return ToolResult(ok=False, content=f"error: {e}")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return ToolResult(ok=False, content=f"error: timed out after {timeout}s")

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        exit_code = proc.returncode or 0

        content_parts = []
        if out:
            content_parts.append(out)
        if err:
            content_parts.append(f"[stderr]\n{err}")
        content_parts.append(f"[exit {exit_code}]")
        content = "\n".join(content_parts)

        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS] + f"\n... (truncated at {MAX_OUTPUT_CHARS} chars)"

        # Emit run:completed to preserve the event-bus convention
        try:
            await app.emit(
                "run:completed",
                {
                    "command": command,
                    "exit_code": exit_code,
                    "stdout_len": len(out),
                },
            )
        except Exception:
            pass

        return ToolResult(
            ok=exit_code == 0,
            content=content,
            display={"command": command, "exit_code": exit_code, "stdout_chars": len(out)},
        )
