"""Tests — in-daemon runner for the pytest suite.

Discovers tests/test_*.py files, runs pytest via subprocess, tracks last-run
results per file in the app's data dir.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
DURATION_RE = re.compile(r"in ([\d.]+)s")
PER_TEST_RE = re.compile(r"([\w/.]+::[\w\[\]\-:. ]+?)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)")
MAX_OUTPUT = 20000


class TestsApp(BaseApp):

    @property
    def _history_path(self) -> Path:
        return self.data_dir / "history.json"

    def _load_history(self) -> dict:
        p = self._history_path
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_history(self, history: dict):
        self._history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    def discover(self) -> list[dict]:
        repo = self.repo_root
        tests_dir = repo / "tests"
        if not tests_dir.exists():
            return []
        history = self._load_history()
        files = []
        for p in sorted(tests_dir.glob("test_*.py")):
            rel = p.relative_to(repo).as_posix()
            files.append({
                "path": rel,
                "name": p.stem,
                "size": p.stat().st_size,
                "last_run": history.get(rel),
            })
        return files

    def _parse_summary(self, text: str) -> dict:
        result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration": 0.0}
        for line in reversed(text.splitlines()):
            if "=" in line and ("passed" in line or "failed" in line or "error" in line or "no tests" in line):
                for count, kind in COUNT_RE.findall(line):
                    key = "errors" if kind.startswith("error") else kind
                    if key in result:
                        result[key] = int(count)
                m = DURATION_RE.search(line)
                if m:
                    result["duration"] = float(m.group(1))
                break
        return result

    async def _run_pytest(self, cmd: list[str], label: str, k_filter: str, timeout: int) -> dict:
        await self.emit("tests:run_started", {"path": label, "filter": k_filter})
        started = time.time()

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(self.repo_root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")
            exit_code = proc.returncode or 0
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            output = f"Timed out after {timeout}s"
            exit_code = -1
            timed_out = True

        summary = self._parse_summary(output)
        summary["exit_code"] = exit_code
        summary["timed_out"] = timed_out
        summary["wall_time"] = round(time.time() - started, 2)
        summary["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        await self.emit("tests:run_completed", {
            "path": label,
            "passed": summary["passed"],
            "failed": summary["failed"],
            "errors": summary["errors"],
            "exit_code": exit_code,
        })

        return {"path": label, "summary": summary, "output": output[-MAX_OUTPUT:]}

    async def run_file(self, path: str, k_filter: str = "", timeout: int = 300) -> dict:
        repo = self.repo_root.resolve()
        target = (repo / path).resolve()
        try:
            target.relative_to(repo)
        except ValueError:
            return {"error": "path outside repo", "path": path}
        if not target.is_file():
            return {"error": "not a file", "path": path}

        cmd = [sys.executable, "-m", "pytest", str(target), "-v", "--tb=short", "--color=no"]
        if k_filter:
            cmd.extend(["-k", k_filter])

        result = await self._run_pytest(cmd, path, k_filter, timeout)
        history = self._load_history()
        history[path] = result["summary"]
        self._save_history(history)
        return result

    async def run_all(self, timeout: int = 1800) -> dict:
        cmd = [sys.executable, "-m", "pytest", "tests/", "--ignore=tests/personal",
               "-v", "--tb=short", "--color=no"]
        return await self._run_pytest(cmd, "tests/", "all", timeout)

    @cli_command("tests", help="List or run tests (usage: tests [list|run <file>|all])")
    async def cmd_tests(self, action: str = "list", path: str = ""):
        if action == "list":
            for f in self.discover():
                last = f["last_run"]
                tag = "—"
                if last:
                    if last.get("failed") or last.get("errors") or last.get("exit_code", 0) < 0:
                        tag = f"FAIL ({last.get('failed',0)}f/{last.get('errors',0)}e)"
                    else:
                        tag = f"PASS ({last.get('passed',0)})"
                print(f"  {f['name']:<40} {tag}")
        elif action == "run" and path:
            result = await self.run_file(path)
            s = result.get("summary") or {}
            print(f"\n{path}: {s.get('passed',0)}p / {s.get('failed',0)}f / {s.get('errors',0)}e in {s.get('wall_time',0)}s\n")
        elif action == "all":
            print("Running full suite...")
            result = await self.run_all()
            s = result["summary"]
            print(f"\nTotal: {s['passed']}p / {s['failed']}f / {s['errors']}e in {s['wall_time']}s\n")

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        return {"tests": self.discover()}

    @web_route("POST", "/api/run")
    async def api_run(self, request):
        data = await request.json()
        path = data.get("path", "")
        k_filter = data.get("filter", "")
        timeout = int(data.get("timeout", 300))
        if not path:
            return {"error": "path required"}
        return await self.run_file(path, k_filter, timeout)

    @web_route("POST", "/api/run-all")
    async def api_run_all(self, request):
        data = await request.json() if request.method == "POST" else {}
        timeout = int(data.get("timeout", 1800))
        return await self.run_all(timeout)

    @web_route("GET", "/api/history")
    async def api_history(self, request):
        return self._load_history()

    # ── Per-app test runner ──

    def _parse_per_test(self, output: str) -> list[dict]:
        """Parse verbose pytest output into per-test results."""
        tests = []
        for match in PER_TEST_RE.finditer(output):
            raw_name = match.group(1)
            status = match.group(2)
            # Strip file prefix: test_cable.py::TestFoo::test_bar → TestFoo::test_bar
            name = raw_name.split("::", 1)[1] if "::" in raw_name else raw_name
            tests.append({"name": name, "status": status})
        return tests

    def _discover_app_test_file(self, app_id: str) -> str | None:
        """Find test file for an app by id. Checks personal/ then core."""
        repo = self.repo_root
        # Normalize app_id: cable → cable, sheath-voltage → sheath_voltage
        slug = app_id.replace("-", "_")
        candidates = [
            repo / "tests" / "personal" / f"test_{slug}.py",
            repo / "tests" / f"test_sys_{slug}.py",
        ]
        for p in candidates:
            if p.is_file():
                return p.relative_to(repo).as_posix()
        return None

    @web_route("POST", "/api/run-app")
    async def api_run_app(self, request):
        """Run tests for a specific app by app_id."""
        data = await request.json()
        app_id = data.get("app_id", "")
        if not app_id:
            return {"error": "app_id required"}

        path = self._discover_app_test_file(app_id)
        if not path:
            return {"error": f"no test file found for app '{app_id}'"}

        k_filter = data.get("filter", "")
        timeout = int(data.get("timeout", 300))

        result = await self.run_file(path, k_filter, timeout)
        # Add per-test breakdown
        result["tests"] = self._parse_per_test(result.get("output", ""))
        return result
