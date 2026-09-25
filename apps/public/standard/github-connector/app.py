"""GitHub Connector — fetch GitHub issues/PRs, with one-click import into
EmptyOS tasks.

Declares [provides.project-tools] so it auto-appears in development
project Tools tabs via manifest-driven discovery.
"""

from __future__ import annotations

from emptyos.sdk import BaseApp, cli_command, web_route

try:
    import aiohttp
except ImportError:
    aiohttp = None


class GitHubConnectorApp(BaseApp):
    def _token(self) -> str:
        return self.setting_or_config("github.token", "")

    def _default_repo(self) -> str:
        return self.setting_or_config("github.default_repo", "")

    def _headers(self) -> dict:
        token = self._token()
        h = {"Accept": "application/vnd.github+json", "User-Agent": "EmptyOS"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    async def _gh_get(self, path: str) -> dict | list:
        """GET from GitHub API."""
        if aiohttp is None:
            return {"error": "aiohttp not installed"}
        url = f"https://api.github.com{path}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        """Check GitHub connection status."""
        token = self._token()
        repo = self._default_repo()
        if not token:
            return {"connected": False, "reason": "No GitHub token configured"}
        try:
            user = await self._gh_get("/user")
            return {
                "connected": True,
                "user": user.get("login", ""),
                "default_repo": repo,
            }
        except Exception as e:
            return {"connected": False, "reason": str(e)}

    async def sync_issues(self, repo: str = "") -> dict:
        """Fetch open issues from a GitHub repo. Pure read — no state mutation
        on GitHub or in EmptyOS — so it's safe as a ``[[provides.verbs]]``
        ``stable`` action node (an automation may auto-run it unattended)."""
        repo = repo or self._default_repo()
        if not repo:
            return {"error": "repo required (owner/repo format)"}

        try:
            issues = await self._gh_get(f"/repos/{repo}/issues?state=open&per_page=50")
            result = []
            for issue in issues:
                if issue.get("pull_request"):
                    continue  # Skip PRs in issues list
                result.append(
                    {
                        "number": issue["number"],
                        "title": issue["title"],
                        "state": issue["state"],
                        "labels": [l["name"] for l in issue.get("labels", [])],
                        "assignee": (issue.get("assignee") or {}).get("login", ""),
                        "created": issue["created_at"][:10],
                        "url": issue["html_url"],
                    }
                )
            await self.emit("github:synced", {"repo": repo, "count": len(result)})
            return {"issues": result, "repo": repo}
        except Exception as e:
            return {"error": str(e)}

    @web_route("POST", "/api/sync-issues")
    async def api_sync_issues(self, request):
        """Fetch open issues from a GitHub repo."""
        data = await request.json()
        return await self.sync_issues(data.get("repo", ""))

    async def pr_status(self, repo: str = "") -> dict:
        """Fetch open PRs from a GitHub repo. Pure read — same eligibility
        rationale as ``sync_issues`` above."""
        repo = repo or self._default_repo()
        if not repo:
            return {"error": "repo required (owner/repo format)"}

        try:
            prs = await self._gh_get(f"/repos/{repo}/pulls?state=open&per_page=30")
            result = []
            for pr in prs:
                result.append(
                    {
                        "number": pr["number"],
                        "title": pr["title"],
                        "state": pr["state"],
                        "author": pr["user"]["login"],
                        "branch": pr["head"]["ref"],
                        "created": pr["created_at"][:10],
                        "url": pr["html_url"],
                        "draft": pr.get("draft", False),
                    }
                )
            await self.emit("github:pr_status", {"repo": repo, "count": len(result)})
            return {"prs": result, "repo": repo}
        except Exception as e:
            return {"error": str(e)}

    @web_route("POST", "/api/pr-status")
    async def api_pr_status(self, request):
        """Fetch open PRs from a GitHub repo."""
        data = await request.json()
        return await self.pr_status(data.get("repo", ""))

    async def import_issue_as_task(self, repo: str, number: int, project: str = "") -> dict:
        """Create an EmptyOS project task from one GitHub issue.

        The one write-back path that makes the manifest's "sync into EmptyOS
        projects" claim real — everything else in this app is a pure read.
        Idempotent enough for manual use (no dedup check); re-importing the
        same issue just adds a second task line, same as any other capture.
        """
        repo = repo or self._default_repo()
        if not repo:
            return {"error": "repo required (owner/repo format)"}
        project = project or "inbox"
        try:
            issue = await self._gh_get(f"/repos/{repo}/issues/{number}")
        except Exception as e:
            return {"error": str(e)}
        if issue.get("pull_request"):
            return {"error": f"#{number} is a pull request, not an issue"}
        text = f"[GH #{issue['number']}] {issue['title']} — {issue['html_url']}"
        result = await self.call_app(
            "projects", "add_task_to_project", project_id=project, text=text,
        )
        if result.get("error"):
            return result
        await self.emit("github:issue_imported", {"repo": repo, "number": number, "project": project})
        return {"ok": True, "project": project, "task": text}

    @web_route("POST", "/api/import-issue")
    async def api_import_issue(self, request):
        """Create an EmptyOS project task from one GitHub issue."""
        data = await request.json()
        number = data.get("number")
        if not isinstance(number, int):
            return {"error": "number (int) required"}
        return await self.import_issue_as_task(
            data.get("repo", ""), number, data.get("project", ""),
        )

    @cli_command("github", help="GitHub connector")
    async def cmd_github(self, action: str = "status", repo: str = ""):
        if action == "status":
            token = self._token()
            if not token:
                print("  No GitHub token configured. Set via Settings > github.token")
                return
            try:
                user = await self._gh_get("/user")
                print(f"  Connected as: {user.get('login')}")
                print(f"  Default repo: {self._default_repo() or '(not set)'}")
            except Exception as e:
                print(f"  Connection failed: {e}")
        elif action == "issues":
            repo = repo or self._default_repo()
            if not repo:
                print("  Specify repo: eos github issues --repo owner/repo")
                return
            issues = await self._gh_get(f"/repos/{repo}/issues?state=open&per_page=10")
            for i in issues:
                if not i.get("pull_request"):
                    print(f"  #{i['number']} {i['title']}")
