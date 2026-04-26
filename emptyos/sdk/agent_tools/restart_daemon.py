"""RestartDaemon tool — let the agent pick up its own Python edits.

The running daemon holds old bytecode until restarted. This tool spawns
`restart.bat` (Windows) or a detached respawn (Unix) so the agent can
close the edit → restart → verify loop without asking the user.

Permission: always `ask`. A restart interrupts any in-flight HTTP requests
to :9000, so the user should know. The spawn is detached — our own
process (the `eos chat` client) is NOT killed; only the daemon process
is replaced.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from emptyos.sdk.agent_tools.base import Tool, ToolResult, repo_root


class RestartDaemonTool(Tool):
    name = "RestartDaemon"
    description = (
        "Restart the EmptyOS daemon to pick up Python code changes. Required "
        "after editing any .py file — the running daemon holds old bytecode "
        "until restart. On Windows, runs `restart.bat` detached; on Unix, uses "
        "`python -m emptyos start` in a new process group. The current `eos chat` "
        "session is not affected (it's a separate process). After calling, wait "
        "~3s for the daemon to come up, then Fetch the affected endpoint to verify."
    )
    permission = "ask"
    input_schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short note on why — ends up in the permission prompt so the user can judge.",
            },
        },
        "required": [],
    }

    def permission_summary(self, input: dict) -> str:
        reason = (input.get("reason") or "").strip()
        return f"RestartDaemon{' (' + reason + ')' if reason else ''}"

    async def run(self, app, **kwargs) -> ToolResult:
        reason = (kwargs.get("reason") or "").strip()
        repo = repo_root(app)

        if sys.platform == "win32":
            bat = repo / "restart.bat"
            if not bat.exists():
                return ToolResult(
                    ok=False,
                    content=f"error: restart.bat not found at {bat}. Manually restart: Ctrl+C + `python -m emptyos start`.",
                )
            # Detach so our own process doesn't wait on the new daemon.
            # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS: no console handle
            # shared, no signal inheritance. This survives our exit.
            try:
                subprocess.Popen(
                    ["cmd.exe", "/c", str(bat)],
                    cwd=str(repo),
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NO_WINDOW
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except Exception as e:
                return ToolResult(ok=False, content=f"error: could not spawn restart.bat: {e}")
            msg = (
                "Spawned restart.bat (detached). The daemon will be down for ~3–5 seconds while "
                "it respawns. After that, Fetch http://localhost:9000/api/health or the endpoint "
                "you care about to verify the restart completed and your edits took effect."
            )
            if reason:
                msg = f"{msg}\n(reason: {reason})"
            return ToolResult(
                ok=True,
                content=msg,
                display={"name": "RestartDaemon", "method": "restart.bat", "reason": reason},
            )

        # Unix: respawn `python -m emptyos start` in a new session. Caller is
        # expected to have a running daemon; we don't know its PID here, so we
        # rely on the new process claiming port 9000 (the old one will error
        # out if it's still there — user sees it in their terminal).
        try:
            subprocess.Popen(
                [sys.executable, "-m", "emptyos", "start"],
                cwd=str(repo),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as e:
            return ToolResult(ok=False, content=f"error: could not spawn daemon: {e}")
        return ToolResult(
            ok=True,
            content=(
                "Spawned `python -m emptyos start` detached. If port 9000 is held by an older "
                "daemon, the new one will fail to bind — the user needs to Ctrl+C the old one. "
                "After the new daemon starts, Fetch /api/health to verify."
            ),
            display={"name": "RestartDaemon", "method": "python -m emptyos start", "reason": reason},
        )
