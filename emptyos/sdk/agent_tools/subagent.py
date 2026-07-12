"""SubAgent tool — delegate a focused subtask to a nested agent turn.

Two modes:

  • ``sync`` (default) — spawn a fresh AgentSession, run its tool loop to
    completion, and return the final assistant text plus a tool-use summary.
    The parent turn blocks until the subagent finishes. Byte-for-byte the
    original behaviour.

  • ``background`` — mint a run_id, persist it to the owning app's
    ``subagent-runs`` registry, spawn the turn detached, and return the
    run_id immediately. The parent turn keeps going; the subagent's progress
    and result land in the registry (surfaced on the Runs board via the agent
    app's ``list_all``). Use for fan-out research the parent doesn't need to
    block on.

The ``readonly`` flag restricts the subagent to a read-only tool allowlist —
the safe way to run a detached/parallel subagent without it clobbering files.
(We use an explicit allowlist rather than the per-tool ``.readonly`` flag,
which is inconsistent — ``Skill`` is flagged read-only but can execute.)

Events are NOT forwarded to the parent turn's WebSocket — the sub-turn is
silent from the UI's perspective; the registry is its record.

Design notes:
  • Uses the parent app's default provider — no provider selection at call time.
  • Uses the same tool_consent manager as the parent turn (risky tools still ask).
  • The sub-turn shares the parent app's tool hooks (audit log, task persist).
  • max_iters defaults to 10 — subagents should be focused; long loops mean the
    task should be done by the parent instead.
  • Don't nest: SubAgent is not in the sub-turn's own tool set.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from emptyos.sdk.agent_tools.base import Tool, ToolResult, feature_enabled

MAX_OUTPUT_CHARS = 12_000
_SUBAGENT_TIMEOUT_S = 180.0

# Tools a `readonly` subagent may use. Explicit allowlist — won't mutate local
# state or execute arbitrary code. Excludes Write/Edit/DeleteFunction (writes),
# Bash/Python/RestartDaemon (execution), CallApp (can reach state-changing
# verbs), Skill (can execute), and SubAgent (no nesting).
READONLY_TOOLS = ["Read", "Grep", "Glob", "Fetch", "WebSearch", "VaultQuery", "TaskList", "Screenshot"]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class SubAgentTool(Tool):
    name = "SubAgent"
    description = (
        "Delegate a focused subtask to a nested agent turn. The subagent has access "
        "to the same tools (Read, Grep, Glob, Bash, Write, Edit, Python, Fetch, etc.) "
        "and runs its own tool loop until the task is complete. "
        "mode='sync' (default) blocks and returns the final answer + tools used. "
        "mode='background' returns a run_id immediately and runs the subagent "
        "detached — its result lands in the SubAgent runs registry (visible on the "
        "Runs board); use for fan-out work you don't need to wait on. "
        "readonly=true restricts it to read-only tools — use that for safe parallel "
        "or background research. "
        "Best for isolated, well-scoped subtasks (research, single-file edits, "
        "data transforms). Don't nest subagents more than 1 level deep. Always "
        "asks permission before spawning."
    )
    permission = "ask"
    readonly = False
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the subagent — be specific and self-contained.",
            },
            "system": {
                "type": "string",
                "description": "Optional extra system-prompt context (appended after the default).",
            },
            "max_iters": {
                "type": "integer",
                "description": "Max tool-use iterations (default 10; cap 20).",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict the subagent to these tool names. Omit to give the full tool set."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["sync", "background"],
                "description": (
                    "sync (default): block and return the result. background: return a "
                    "run_id immediately; the subagent runs detached and its result lands "
                    "in the SubAgent runs registry."
                ),
            },
            "readonly": {
                "type": "boolean",
                "description": (
                    "Restrict the subagent to read-only tools (no Write/Edit/Bash/Python/"
                    "CallApp). Use for safe parallel or background research that must not "
                    "mutate state."
                ),
            },
            "isolate": {
                "type": "string",
                "enum": ["none", "worktree"],
                "description": (
                    "none (default): edits land in the live tree. worktree: run the "
                    "subagent's file edits in an isolated git worktree on its own branch "
                    "— safe for parallel writers; the branch + diff are left for review "
                    "(no auto-merge)."
                ),
            },
        },
        "required": ["task"],
    }

    def permission_summary(self, input: dict) -> str:
        task = input.get("task", "")[:80]
        mode = (input.get("mode") or "sync").lower()
        ro = " readonly" if input.get("readonly") else ""
        wt = " worktree" if (input.get("isolate") or "none").lower() == "worktree" else ""
        flags = f"{mode}{ro}{wt}"
        tag = f" [{flags}]" if (mode != "sync" or ro or wt) else ""
        return f"SubAgent{tag}: {task}"

    # ── Worktree isolation (isolate="worktree") ───────────────────────
    def _setup_worktree(self, app, run_id: str):
        """Create an isolated worktree + branch for a subagent run.

        Returns ``(wrapped_app, wt_path, branch, error)``. ``wrapped_app`` is a
        ``WorktreeApp`` whose ``repo_root`` is the worktree, so the sub-turn's
        Edit/Write/Bash redirect there automatically. Mirrors the fix-agent
        reset → clean → branch sequence (apps/extension/dev/fix-agent/runs.py).
        """
        from emptyos.sdk.subagent_context import WorktreeApp
        from emptyos.sdk.worktree import ensure_worktree, git_run

        repo = app.repo_root
        wt_path = repo / ".claude" / "worktrees" / f"subagent-{run_id}"
        branch = f"subagent/{run_id}"
        _wt, err = ensure_worktree(repo, wt_path)
        if err:
            return None, wt_path, branch, f"error: {err}"
        for cmd, label in (
            (["reset", "--hard", "HEAD"], "reset"),
            (["clean", "-fd"], "clean"),
            (["checkout", "-B", branch, "main"], "branch"),
        ):
            rc, _, e = git_run(cmd, cwd=wt_path)
            if rc != 0:
                return None, wt_path, branch, f"error: git {label} failed in worktree: {e.strip()}"
        return WorktreeApp(app, wt_path), wt_path, branch, None

    def _capture_worktree_result(self, wt_path: Path, branch: str) -> dict:
        """Commit the subagent's edits to its branch and summarise the diff.

        The nested turn leaves uncommitted edits in the worktree; we stage +
        commit them so the branch carries a reviewable changeset, run a
        py_compile gate over changed ``*.py``, and return branch/commits/diff
        metadata. Best-effort — never raises into the caller.
        """
        from emptyos.sdk.worktree import git_run, py_compile_files

        meta: dict = {"branch": branch, "worktree": str(wt_path)}
        try:
            git_run(["add", "-A"], cwd=wt_path)
            rc_changed, _, _ = git_run(["diff", "--cached", "--quiet"], cwd=wt_path)
            if rc_changed == 0:
                meta["changed"] = False
                meta["diff_stat"] = ""
                meta["commits"] = []
                meta["compile_ok"] = True
                return meta
            meta["changed"] = True
            _rc, names, _ = git_run(["diff", "--cached", "--name-only"], cwd=wt_path)
            py_files = [n for n in (names or "").splitlines() if n.strip().endswith(".py")]
            meta["compile_ok"] = True
            if py_files:
                ok, msg = py_compile_files(py_files, cwd=wt_path)
                meta["compile_ok"] = ok
                if not ok:
                    meta["compile_error"] = msg[:1000]
            git_run(["commit", "-m", f"subagent {branch}", "--no-verify"], cwd=wt_path)
            _rc2, log_out, _ = git_run(["log", "--oneline", "main..HEAD"], cwd=wt_path)
            meta["commits"] = [l for l in (log_out or "").splitlines() if l.strip()]
            _rc3, stat_out, _ = git_run(["diff", "--stat", "main...HEAD"], cwd=wt_path)
            meta["diff_stat"] = (stat_out or "").strip()
        except Exception as e:
            meta["capture_error"] = str(e)
        return meta

    @staticmethod
    def _worktree_note(meta: dict) -> str:
        """One-line human summary of an isolated run's hand-off, for the model."""
        if not meta:
            return ""
        branch = meta.get("branch", "?")
        if not meta.get("changed"):
            return f"\n\n[worktree {branch}: no file changes]"
        compile_tag = "" if meta.get("compile_ok", True) else " ⚠ py_compile FAILED"
        stat = meta.get("diff_stat", "")
        return f"\n\n[worktree branch {branch} (review + merge manually){compile_tag}]\n{stat}"

    async def run(self, app, **kwargs) -> ToolResult:
        task = (kwargs.get("task") or "").strip()
        if not task:
            return ToolResult(ok=False, content="error: task is required")

        extra_system = (kwargs.get("system") or "").strip()
        max_iters_raw = kwargs.get("max_iters", 10)
        try:
            max_iters = min(int(max_iters_raw), 20)
        except (TypeError, ValueError):
            max_iters = 10

        mode = (kwargs.get("mode") or "sync").strip().lower()
        readonly = bool(kwargs.get("readonly"))
        isolate = (kwargs.get("isolate") or "none").strip().lower()
        if isolate == "worktree" and not feature_enabled(app, "subagent-worktree"):
            return ToolResult(
                ok=False,
                content=(
                    "error: worktree isolation is disabled "
                    "([apps.agent] feature.subagent-worktree.enabled = false)"
                ),
            )
        allowed_tools = kwargs.get("tools")  # list[str] | None
        if readonly:
            # Intersect any explicit list with the read-only allowlist; an empty
            # intersection falls back to the full read-only set (never escalates).
            allowed_tools = [t for t in (allowed_tools or READONLY_TOOLS) if t in READONLY_TOOLS] or list(
                READONLY_TOOLS
            )

        # Lazy imports — agent_loop is heavy; avoid loading at tool-registry build time.
        from emptyos.sdk.agent_loop import DEFAULT_SYSTEM_PROMPT
        from emptyos.sdk.agent_tools import build_registry

        # Resolve provider — use the app's default (same as the parent turn).
        provider_name = app._default_provider_name()
        provider = app._resolve_provider(provider_name)
        if provider is None:
            return ToolResult(
                ok=False,
                content=f"error: no tool-capable provider available (tried {provider_name!r})",
            )

        tool_consent = app.service("tool_consent")
        tools = build_registry(enabled=allowed_tools)

        system = DEFAULT_SYSTEM_PROMPT
        if extra_system:
            system = system + "\n\n" + extra_system

        if mode == "background":
            runs = app.runs("subagent-runs")
            handle = runs.new()
            handle.write_state({
                "run_id": handle.run_id,
                "task": task[:500],
                "status": "running",
                "mode": "background",
                "readonly": readonly,
                "isolate": isolate,
                "tools": sorted(tools.keys()),
                "started": _utcnow(),
            })
            asyncio.create_task(
                self._run_background(
                    app, handle, task, system, provider, tools, tool_consent, max_iters,
                    isolate=isolate,
                )
            )
            iso_note = " (isolated in a worktree branch)" if isolate == "worktree" else ""
            return ToolResult(
                ok=True,
                content=(
                    f"[SubAgent dispatched in background — run_id={handle.run_id}{iso_note}. "
                    "It runs detached; its result will appear in the SubAgent runs "
                    "registry / Runs board.]"
                ),
                display={
                    "run_id": handle.run_id,
                    "mode": "background",
                    "readonly": readonly,
                    "isolate": isolate,
                    "task": task[:120],
                },
            )

        # sync mode — block and return the result.
        app_for_turn = app
        wt_path = None
        branch = ""
        if isolate == "worktree":
            run_id = f"sync-{int(time.time() * 1000)}"
            app_for_turn, wt_path, branch, err = self._setup_worktree(app, run_id)
            if err:
                return ToolResult(ok=False, content=err)

        result_text, tools_used, iters, error = await self._execute_turn(
            app_for_turn, task, system, provider, tools, tool_consent, max_iters
        )
        if error:
            return ToolResult(ok=False, content=error)

        wt_note = ""
        if isolate == "worktree" and wt_path is not None:
            wt_note = self._worktree_note(self._capture_worktree_result(wt_path, branch))

        summary = f"[SubAgent: {iters} iter(s), tools: {', '.join(tools_used) or 'none'}]\n\n"
        content = summary + result_text + wt_note
        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS] + "\n… (truncated)"

        return ToolResult(
            ok=bool(result_text),
            content=content,
            display={
                "task": task[:120],
                "iters": iters,
                "tools_used": tools_used,
                "isolate": isolate,
                "branch": branch,
                "chars": len(result_text),
            },
        )

    async def _execute_turn(
        self, app, task, system, provider, tools, tool_consent, max_iters
    ) -> tuple[str, list[str], int, str | None]:
        """Run one nested turn to completion. Returns
        ``(result_text, tools_used, iters, error)`` — ``error`` is None on
        success. Shared by sync + background modes."""
        from emptyos.capabilities.providers._tool_capable import TextBlock
        from emptyos.sdk.agent_loop import DEFAULT_TEMPERATURE, AgentSession, run_turn

        sub_sid = f"sub_{int(time.time() * 1000)}"
        sub_session = AgentSession(id=sub_sid)

        # Minimal event collector — captures tool names without touching the WS.
        events_log: list[tuple[str, dict]] = []

        class _MinimalBus:
            async def emit(self, event_type: str, data: dict = None, **_):
                events_log.append((event_type, data or {}))

        try:
            final_turn = await asyncio.wait_for(
                run_turn(
                    session=sub_session,
                    user_text=task,
                    provider=provider,
                    tools=tools,
                    tool_consent=tool_consent,
                    events=_MinimalBus(),
                    app_ref=app,
                    system=system,
                    max_iters=max_iters,
                    temperature=DEFAULT_TEMPERATURE,
                ),
                timeout=_SUBAGENT_TIMEOUT_S,
            )
        except TimeoutError:
            return "", [], 0, f"error: subagent timed out after {int(_SUBAGENT_TIMEOUT_S)}s"
        except Exception as e:
            return "", [], 0, f"error: subagent failed — {e}"

        text_parts = [
            b.text for b in (final_turn.assistant_blocks or []) if isinstance(b, TextBlock)
        ]
        result_text = "".join(text_parts).strip()

        tools_used: list[str] = []
        for event_type, data in events_log:
            if event_type == "agent:tool_call":
                name = data.get("name", "")
                if name and name not in tools_used:
                    tools_used.append(name)
        iters = len([e for e, _ in events_log if e == "agent:iter_start"])
        return result_text, tools_used, iters, None

    async def _run_background(
        self, app, handle, task, system, provider, tools, tool_consent, max_iters,
        *, isolate: str = "none",
    ) -> None:
        """Detached driver — run the turn, then persist the terminal state.

        With ``isolate="worktree"`` the sub-turn runs against a per-run worktree
        branch; the diff/commit summary is captured into the run state for review.
        """
        app_for_turn = app
        wt_path = None
        branch = ""
        if isolate == "worktree":
            app_for_turn, wt_path, branch, err = self._setup_worktree(app, handle.run_id)
            if err:
                meta = handle.read_state() or {}
                meta["status"] = "failed"
                meta["error"] = err
                meta["finished"] = _utcnow()
                handle.write_state(meta)
                return

        result_text, tools_used, iters, error = await self._execute_turn(
            app_for_turn, task, system, provider, tools, tool_consent, max_iters
        )
        meta = handle.read_state() or {}
        meta["finished"] = _utcnow()
        meta["iters"] = iters
        meta["tools_used"] = tools_used
        if isolate == "worktree" and wt_path is not None and not error:
            meta["worktree"] = self._capture_worktree_result(wt_path, branch)
        if error:
            meta["status"] = "failed"
            meta["error"] = error
        elif not result_text:
            meta["status"] = "failed"
            meta["error"] = "subagent produced no text output"
        else:
            meta["status"] = "done"
            meta["result"] = result_text[:MAX_OUTPUT_CHARS]
        handle.write_state(meta)
        try:
            await app.emit(
                "agent:subagent_finished",
                {"run_id": meta.get("run_id"), "status": meta.get("status")},
            )
        except Exception:
            pass
