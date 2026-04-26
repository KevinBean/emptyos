"""Command Runner — execute shell commands and scripts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from emptyos.sdk import BaseApp, cli_command, web_route


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int

    def to_dict(self):
        return {"stdout": self.stdout, "stderr": self.stderr, "exit_code": self.exit_code}


class RunApp(BaseApp):

    async def execute(self, command: str, timeout: int = 60, cwd: str = "") -> RunResult:
        """Run a shell command. Returns stdout, stderr, exit code."""
        work_dir = cwd or self.kernel.config.get("notes.path", None)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return RunResult(stdout="", stderr=f"Timed out after {timeout}s", exit_code=-1)

        result = RunResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=proc.returncode or 0,
        )
        await self.emit("run:completed", {
            "command": command,
            "exit_code": result.exit_code,
            "stdout_len": len(result.stdout),
        })
        return result

    @cli_command("run", help="Execute a shell command")
    async def cmd_run(self, command: str, timeout: int = 60):
        result = await self.execute(command, timeout)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            self.print_rich(f"[red]{result.stderr}[/red]")
        if result.exit_code != 0:
            self.print_rich(f"[dim]Exit code: {result.exit_code}[/dim]")

    @web_route("POST", "/api/execute")
    async def api_execute(self, request):
        data = await request.json()
        result = await self.execute(data["command"], data.get("timeout", 60))
        return result.to_dict()

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        """Recent command executions from event history."""
        events = await self.kernel.events.history(event_type="run:completed", limit=50)
        return [
            {"command": e["data"].get("command", ""), "exit_code": e["data"].get("exit_code", 0), "timestamp": e["timestamp"]}
            for e in events
        ]

    @web_route("POST", "/api/suggest")
    async def api_suggest(self, request):
        """AI suggests a shell command for a task description."""
        data = await request.json()
        task = data.get("task", "")
        if not task:
            return {"error": "task description required"}
        result = await self.think(
            f"Suggest a Windows/bash shell command to accomplish this task. "
            f"Return ONLY the command, no explanation.\n\nTask: {task}",
            domain="code", temperature=0.3,
        )
        return {"task": task, "command": result.strip().strip('`')}
