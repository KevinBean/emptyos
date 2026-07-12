"""RunCenterMixin — the generic HTTP surface shared by worktree+RunRegistry dev apps.

Both app-builder and fix-agent drive claude-cli runs inside a git worktree, track
each run as a RunRegistry record, and expose an identical run-center HTTP surface:
list runs, read one run (with live tail), discard a run (+ delete its branch), and
report repo/worktree/claude-binary status. This mixin owns those four handlers so a
third such app inherits them for free instead of copying them a third time.

Host-class contract — the mixin calls these; the app must provide them:
  - ``self.repo_root``        (BaseApp property)
  - ``self.runs(kind)``       (BaseApp — RunRegistry handle)
  - ``self.service(name)``    (BaseApp)
  - ``self._lock``            (asyncio.Lock, set in ``setup``)
  - ``self._worktree_path()`` -> Path        (app-specific worktree location)
  - ``self._list_runs()``     -> list[dict]   (app-specific run listing)

Use via inheritance: ``class MyDevApp(BaseApp, RunCenterMixin): ...``. The route
handlers are discovered through the MRO walk in ``BaseApp._get_decorated`` (it
iterates ``type(self).__mro__``), so inherited ``@web_route`` methods register
under the app's own prefix with no per-app binding.

When NOT to use: an app that isn't worktree+RunRegistry shaped, or one whose
run-center semantics diverge (different discard side-effects, a richer status
payload). Override the single diverging handler in the app's own class body — an
app-local method wins over the mixin in MRO order — rather than widening the mixin.
"""

from __future__ import annotations

from datetime import datetime, timezone

from emptyos.sdk.decorators import web_route
from emptyos.sdk.worktree import git_run


class RunCenterMixin:
    """Shared run-center HTTP handlers for worktree+RunRegistry dev apps."""

    @web_route("GET", "/api/runs")
    async def api_runs(self, request):
        return {"runs": self._list_runs()}

    @web_route("GET", "/api/runs/{run_id}")
    async def api_run_get(self, request):
        rid = request.path_params["run_id"]
        res = self.runs("runs").read_with_tail(rid)
        if res is None:
            return {"error": "not found"}
        return res

    @web_route("POST", "/api/runs/{run_id}/discard")
    async def api_run_discard(self, request):
        rid = request.path_params["run_id"]
        meta = self.runs("runs").update_state(
            rid, status="discarded", discarded_at=datetime.now(timezone.utc).isoformat()
        )
        if meta is None:
            return {"error": "not found"}
        branch = meta.get("branch", "")
        # Best-effort branch delete; -D because the branch may be unmerged.
        if branch:
            git_run(["branch", "-D", branch], cwd=self.repo_root)
        return {"ok": True, "branch": branch}

    @web_route("GET", "/api/status")
    async def api_status(self, request):
        repo = self.repo_root
        wt = self._worktree_path()
        binary = ""
        runtime = self.service("agent-runtime")
        if runtime is not None:
            try:
                binary = runtime.resolve_claude_binary()
            except Exception:
                binary = ""
        rc, current_branch, _ = git_run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
        return {
            "repo_root": str(repo),
            "worktree_path": str(wt),
            "worktree_exists": wt.exists() and (wt / ".git").exists(),
            "current_branch": current_branch.strip() if rc == 0 else "",
            "claude_binary": binary,
            "claude_available": bool(binary),
            "runs": self._list_runs(),
            "busy": self._lock.locked(),
        }
