"""Git — version control for EmptyOS and vault repos."""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, web_route


class GitApp(BaseApp):

    def _project_dir(self) -> str:
        return str(self.kernel.config.path.parent)

    def _vault_dir(self) -> str | None:
        return self.kernel.config.get("notes.path", None)

    def _repos(self) -> list[dict]:
        repos = [{"id": "emptyos", "label": "EmptyOS", "path": self._project_dir()}]
        vault = self._vault_dir()
        if vault and Path(vault).exists():
            repos.append({"id": "vault", "label": "Vault", "path": vault})
        return repos

    def _resolve_repo(self, repo_id: str | None) -> str:
        if repo_id == "vault":
            return self._vault_dir() or self._project_dir()
        return self._project_dir()

    def _repo_path(self, request) -> str:
        return self._resolve_repo(request.query_params.get("repo"))

    async def _git_at(self, repo_path: str, *args: str) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode or 0

    def _parse_branches(self, output: str) -> list[dict]:
        branches = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            current = line.startswith("*")
            name = line.lstrip("* ").strip()
            branches.append({"name": name, "current": current})
        return branches

    # --- Methods used by project-tools (manifest) ---

    async def status_at(self, repo_path: str) -> str:
        out, err, _ = await self._git_at(repo_path, "status", "--short")
        return out or err

    async def log_at(self, repo_path: str, count: int = 10) -> str:
        out, _, _ = await self._git_at(repo_path, "log", "--oneline", f"-{count}")
        return out

    async def branches_at(self, repo_path: str, all: bool = False) -> list[dict]:
        args = ["branch", "--no-color"]
        if all:
            args.append("-a")
        out, _, _ = await self._git_at(repo_path, *args)
        return self._parse_branches(out)

    # --- Actions ---

    async def save(self, message: str, repo_id: str | None = None) -> str:
        path = self._resolve_repo(repo_id)
        await self._git_at(path, "add", "-A")
        out, err, code = await self._git_at(path, "commit", "-m", message)
        if code != 0:
            return err or "Nothing to commit"
        await self.emit("git:saved", {"message": message, "repo": repo_id or "emptyos"})
        return out

    async def push(self, repo_id: str | None = None) -> str:
        path = self._resolve_repo(repo_id)
        out, err, code = await self._git_at(path, "push")
        if code != 0:
            return err or "Push failed"
        await self.emit("git:pushed", {"repo": repo_id or "emptyos"})
        return out or "Pushed successfully"

    async def pull(self, repo_id: str | None = None) -> str:
        path = self._resolve_repo(repo_id)
        out, err, code = await self._git_at(path, "pull")
        if code != 0:
            return err or "Pull failed"
        await self.emit("git:pulled", {"repo": repo_id or "emptyos"})
        return out or "Already up to date"

    @cli_command("git", help="Version control operations")
    async def cmd_git(self, action: str = "status", message: str = "", count: int = 10):
        path = self._project_dir()
        if action == "status":
            print(await self.status_at(path))
        elif action == "log":
            print(await self.log_at(path, count))
        elif action == "diff":
            out, _, _ = await self._git_at(path, "diff")
            print(out)
        elif action == "save" and message:
            result = await self.save(message)
            self.print_rich(f"[green]{result}[/green]")
        else:
            self.print_rich("[dim]Usage: eos git {status|log|diff|save} [--message MSG][/dim]")

    @web_route("GET", "/api/repos")
    async def api_repos(self, request):
        return self._repos()

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        return {"status": await self.status_at(self._repo_path(request))}

    @web_route("GET", "/api/log")
    async def api_log(self, request):
        count = int(request.query_params.get("count", "10"))
        return {"log": await self.log_at(self._repo_path(request), count)}

    @web_route("GET", "/api/diff")
    async def api_diff(self, request):
        out, _, _ = await self._git_at(self._repo_path(request), "diff")
        return {"diff": out}

    @web_route("POST", "/api/push")
    async def api_push(self, request):
        body = await request.json()
        return {"result": await self.push(body.get("repo"))}

    @web_route("POST", "/api/pull")
    async def api_pull(self, request):
        body = await request.json()
        return {"result": await self.pull(body.get("repo"))}

    @web_route("POST", "/api/save")
    async def api_save(self, request):
        body = await request.json()
        message = body.get("message", "")
        if not message:
            return {"error": "message is required"}
        return {"result": await self.save(message, body.get("repo"))}

    @web_route("GET", "/api/branches")
    async def api_branches(self, request):
        return await self.branches_at(self._repo_path(request), all=True)

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        path = self._repo_path(request)
        (log_out, _, _), (status_out, err, _), (branch_out, _, _) = await asyncio.gather(
            self._git_at(path, "log", "--oneline", "-100"),
            self._git_at(path, "status", "--short"),
            self._git_at(path, "rev-parse", "--abbrev-ref", "HEAD"),
        )
        commits = len([l for l in log_out.strip().split("\n") if l.strip()])
        text = (status_out or err).strip()
        changed = len([l for l in text.split("\n") if l.strip()]) if text else 0
        return {
            "branch": branch_out.strip(),
            "recent_commits": commits,
            "uncommitted_changes": changed,
        }

    @web_route("GET", "/api/summary")
    async def api_summary(self, request):
        """AI summary of recent commits."""
        path = self._repo_path(request)
        count = int(request.query_params.get("count", "20"))
        out, _, _ = await self._git_at(path, "log", "--oneline", f"-{count}")
        if not out.strip():
            return {"summary": "No commits found."}
        prompt = (
            "Summarize these recent git commits in 2-3 sentences. "
            "Highlight the main themes of work.\n\n" + out
        )
        result = await self.think(prompt, domain="text")
        return {"summary": result, "commit_count": count}

    @web_route("GET", "/api/log-detail")
    async def api_log_detail(self, request):
        path = self._repo_path(request)
        count = int(request.query_params.get("count", "20"))
        out, _, _ = await self._git_at(path, "log", f"-{count}", "--pretty=format:%H|%h|%an|%ar|%s")
        commits = []
        for line in out.strip().split("\n"):
            if "|" not in line:
                continue
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0], "short": parts[1],
                    "author": parts[2], "ago": parts[3], "message": parts[4],
                })
        return commits
