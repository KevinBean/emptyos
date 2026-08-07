"""Agent runner contract for worktree-backed agent harnesses.

The first implementation wraps the existing Claude Code CLI path. The contract
exists so app-builder / fix-agent / dogfood-agent can depend on a runner shape
instead of depending directly on one CLI's subprocess API.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class AgentRunSpec:
    """One agent subprocess/task request.

    ``None`` on an optional field means "use the runner's default".
    ``mode`` and ``metadata`` are advisory caller context (run kind, run ids
    for audit) — a runner may ignore them; they never change run semantics.
    """

    prompt: str
    cwd: str | Path
    system_prompt: str | None = None
    mode: str = "generic"
    allowed_tools: str | None = None
    timeout_s: float | None = None
    idle_timeout_s: float | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    on_stdout_line: Callable[[bytes], None] | None = None
    on_stderr_line: Callable[[bytes], None] | None = None
    on_tick: Callable[[], Awaitable[None]] | None = None
    tick_interval_s: float | None = None
    provider_id: str | None = None
    model: str | None = None
    effort: str | None = None
    max_iters: int | None = None
    temperature: float | None = None
    extra_args: list[str] | None = None
    # Detached-supervised lifecycle (ClaudeCliRunner.spawn_detached/await_handle/
    # reattach_handle) — the child outlives the daemon; dogfood-agent persists the
    # handle so a restart mid-run reattaches instead of orphaning the run.
    env: dict[str, str] | None = None
    supervision_key: str | None = None
    early_exit_grace_s: float = 30.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRunResult:
    """Normalized result returned by an AgentRunner."""

    runner_id: str
    returncode: int | None = None
    timeout: bool = False
    idle_timeout: bool = False
    duration_s: float | None = None
    error: str | None = None
    binary: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.raw)
        out.update(
            {
                "runner_id": self.runner_id,
                "returncode": self.returncode,
                "timeout": self.timeout,
                "idle_timeout": self.idle_timeout,
                "duration_s": self.duration_s,
                "error": self.error,
                "binary": self.binary,
            }
        )
        return out


class AgentRunner(Protocol):
    """A pluggable agent execution backend."""

    runner_id: str

    def available(self) -> bool:
        ...

    def unavailable_reason(self) -> str:
        ...

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        ...


class ClaudeCliRunner:
    """AgentRunner adapter over agent-runtime.claude_cli_run()."""

    runner_id = "claude-cli"

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def available(self) -> bool:
        try:
            return bool(self.runtime.resolve_claude_binary())
        except Exception:
            return False

    def unavailable_reason(self) -> str:
        return "claude CLI not on PATH"

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        kwargs: dict[str, Any] = {
            "prompt": spec.prompt,
            "system_prompt": spec.system_prompt,
            "cwd": spec.cwd,
            "stdout_path": spec.stdout_path,
            "stderr_path": spec.stderr_path,
            "timeout_s": spec.timeout_s,
            "model": spec.model,
            "effort": spec.effort,
            "extra_args": spec.extra_args,
        }
        if spec.allowed_tools is not None:
            kwargs["allowed_tools"] = spec.allowed_tools
        # Liveness/streaming knobs pass through only when set, so the
        # backend's own defaults (e.g. tick_interval_s) stay authoritative.
        if spec.idle_timeout_s is not None:
            kwargs["idle_timeout_s"] = spec.idle_timeout_s
        if spec.on_stdout_line is not None:
            kwargs["on_stdout_line"] = spec.on_stdout_line
        if spec.on_stderr_line is not None:
            kwargs["on_stderr_line"] = spec.on_stderr_line
        if spec.on_tick is not None:
            kwargs["on_tick"] = spec.on_tick
        if spec.tick_interval_s is not None:
            kwargs["tick_interval_s"] = spec.tick_interval_s
        raw = await self.runtime.claude_cli_run(**kwargs)
        return AgentRunResult(
            runner_id=self.runner_id,
            returncode=raw.get("returncode"),
            timeout=bool(raw.get("timeout", False)),
            idle_timeout=bool(raw.get("idle_timeout", False)),
            duration_s=raw.get("duration_s"),
            error=raw.get("error"),
            binary=raw.get("binary"),
            raw=dict(raw),
        )

    # ── Detached-supervised lifecycle ───────────────────────────────────
    # The piped run() above is spawn+await in one call. A detached run is a
    # split lifecycle: spawn now (persist the handle), await later (survives a
    # daemon restart via reattach). These three own the claude-cli sentinels +
    # arg plumbing so a harness app never reaches into runtime.CLAUDE_* — and
    # they return the runtime's RAW result dict unchanged, so a caller's
    # finalizer stays byte-identical to the pre-abstraction path.

    def spawn_detached(self, spec: AgentRunSpec) -> dict:
        """Detached spawn → ``{pid, started, create_time, binary}`` or ``{error}``.
        Persist the handle, then drive ``await_handle`` (or ``reattach_handle``
        after a restart)."""
        kwargs: dict[str, Any] = {
            "prompt": spec.prompt,
            "system_prompt": spec.system_prompt,
            "cwd": spec.cwd,
            "stdout_path": spec.stdout_path,
            "stderr_path": spec.stderr_path,
            "model": spec.model,
            "effort": spec.effort,
            "extra_args": spec.extra_args,
            "env": spec.env,
            "supervision_key": spec.supervision_key,
            "metadata": spec.metadata or {},
        }
        if spec.allowed_tools is not None:
            kwargs["allowed_tools"] = spec.allowed_tools
        return self.runtime.claude_cli_spawn_detached(**kwargs)

    async def await_handle(self, handle: dict, spec: AgentRunSpec) -> dict:
        """Await a detached handle to completion — raw runtime result dict."""
        return await self.runtime.await_subprocess(
            handle["pid"],
            stdout_path=spec.stdout_path,
            started=handle["started"],
            create_time=handle.get("create_time"),
            timeout_s=spec.timeout_s,
            idle_timeout_s=spec.idle_timeout_s,
            progress_predicate=self.runtime.CLAUDE_PROGRESS_PREDICATE,
            early_exit_on_line=self.runtime.CLAUDE_RESULT_SENTINEL,
            early_exit_grace_s=spec.early_exit_grace_s,
            on_tick=spec.on_tick,
            tick_interval_s=spec.tick_interval_s or 30.0,
            poll_s=1.0,
        )

    async def reattach_handle(self, key: str, spec: AgentRunSpec) -> dict:
        """Reattach to a supervised child after a daemon restart — raw result
        dict (with ``reattached: True`` + ``metadata``). Raises ``KeyError``
        when the supervision record is gone."""
        return await self.runtime.reattach(
            key,
            timeout_s=spec.timeout_s,
            idle_timeout_s=spec.idle_timeout_s,
            progress_predicate=self.runtime.CLAUDE_PROGRESS_PREDICATE,
            early_exit_on_line=self.runtime.CLAUDE_RESULT_SENTINEL,
            early_exit_grace_s=spec.early_exit_grace_s,
            on_tick=spec.on_tick,
            tick_interval_s=spec.tick_interval_s or 30.0,
            poll_s=1.0,
        )

    def clear_supervision(self, key: str) -> bool:
        """Drop a run's supervision record once it's finalized in this lifetime."""
        return self.runtime.clear_supervision(key)


# Appended to every EosAgentRunner system prompt. A claude-cli run learns its
# root from the real process cwd; the in-process loop has no cwd, so it must
# be stated — without this, weaker models fall back to training priors for the
# filesystem layout (the 2026-06-12 bench saw gpt-4o-mini invent /mnt/d/...
# paths and try to build the app there).
WORKSPACE_GROUNDING = (
    "\n\n[workspace] Your working root is: {workspace}\n"
    "All file tools resolve relative paths against this root. Always use "
    "relative paths (e.g. 'apps/foo/app.py'). Never use Unix-style "
    "absolute paths like /mnt/... or /tmp/... — they are invalid here."
)


class _RunnerEventRecorder:
    """Event bridge for non-UI runner invocations."""

    def __init__(self, app_ref: Any, stdout_path: Path | None, runner_id: str, mode: str):
        self.app_ref = app_ref
        self.stdout_path = Path(stdout_path) if stdout_path is not None else None
        self.runner_id = runner_id
        self.mode = mode

    async def emit(self, etype: str, data: dict, source: str = "agent-runner") -> None:
        payload = {"type": etype, "runner_id": self.runner_id, "mode": self.mode, **(data or {})}
        if self.stdout_path is not None:
            try:
                self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
                with self.stdout_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except Exception:
                pass
        try:
            events = getattr(getattr(self.app_ref, "kernel", None), "events", None)
            if events is not None:
                await events.emit(etype, data, source=source)
        except Exception:
            pass


class EosAgentRunner:
    """AgentRunner backed by EmptyOS's first-party run_turn() harness."""

    runner_id = "eos-agent"

    def __init__(self, app_ref: Any, provider_id: str | None = None):
        self.app_ref = app_ref
        self.provider_id = provider_id

    def _provider_candidates(self) -> list[Any]:
        try:
            think = self.app_ref.kernel.capability("think")
        except Exception:
            return []
        candidates: list[Any] = list(getattr(think, "providers", []) or [])
        for chain in getattr(think, "_domains", {}).values():
            candidates.extend(chain)
        for chain in getattr(think, "_buckets", {}).values():
            candidates.extend(chain)
        return candidates

    def _resolve_provider(self, provider_id: str | None = None):
        from emptyos.capabilities.providers._tool_capable import ToolCapableProvider

        requested = (provider_id or self.provider_id or "").strip()
        candidates = self._provider_candidates()
        if requested:
            for provider in candidates:
                if getattr(provider, "name", "") == requested and isinstance(provider, ToolCapableProvider):
                    return provider
            return None
        for provider in candidates:
            if isinstance(provider, ToolCapableProvider):
                return provider
        return None

    def available(self) -> bool:
        return self._resolve_provider() is not None

    def unavailable_reason(self) -> str:
        if self.provider_id:
            return f"tool-capable provider {self.provider_id!r} not available"
        return "no tool-capable think provider available for eos-agent"

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        from emptyos.sdk.agent_loop import (
            DEFAULT_MAX_ITERS,
            DEFAULT_TEMPERATURE,
            AgentSession,
            run_turn,
        )
        from emptyos.sdk.agent_tools import build_registry
        from emptyos.sdk.subagent_context import WorktreeApp

        # Workspace re-rooting: the EOS tool registry resolves relative paths
        # against app.repo_root (agent_tools/base.py::resolve_path), NOT
        # spec.cwd. A foreign cwd (e.g. an app-builder worktree) is honored by
        # handing the tool loop a WorktreeApp facade whose repo_root IS the
        # workspace — the same single seam SubAgentTool's isolate="worktree"
        # rides. The shared app instance is never mutated, so concurrent tool
        # loops are unaffected. Absolute paths in tool calls can still escape
        # it — the same exposure a claude-cli run has — and the worktree diff
        # + merge gates own that risk, not the runner.
        workspace = Path(spec.cwd).resolve()
        if not workspace.is_dir():
            return AgentRunResult(
                runner_id=self.runner_id,
                returncode=-1,
                error=f"cwd does not exist or is not a directory: {str(spec.cwd)!r}",
            )
        try:
            repo_root = Path(self.app_ref.repo_root).resolve()
        except Exception:
            repo_root = None
        tool_app = self.app_ref if workspace == repo_root else WorktreeApp(self.app_ref, workspace)

        provider = self._resolve_provider(spec.provider_id)
        if provider is None:
            return AgentRunResult(
                runner_id=self.runner_id,
                returncode=-1,
                error=self.unavailable_reason(),
            )

        enabled_tools = None
        if spec.allowed_tools:
            enabled_tools = [t.strip() for t in spec.allowed_tools.split(",") if t.strip()]
        tools = build_registry(enabled=enabled_tools)
        session_id = str(spec.metadata.get("run_id") or f"runner-{int(time.time() * 1000)}")
        session = AgentSession(id=session_id, messages=[], provider_kind=provider.kind)
        events = _RunnerEventRecorder(self.app_ref, spec.stdout_path, self.runner_id, spec.mode)
        tool_consent = None
        try:
            tool_consent = self.app_ref.service("tool_consent")
        except Exception:
            pass

        started = time.monotonic()
        max_iters = spec.max_iters or DEFAULT_MAX_ITERS
        temperature = spec.temperature if spec.temperature is not None else DEFAULT_TEMPERATURE
        grounding = WORKSPACE_GROUNDING.format(workspace=workspace)
        coro = run_turn(
            session=session,
            user_text=spec.prompt,
            provider=provider,
            tools=tools,
            tool_consent=tool_consent,
            events=events,
            app_ref=tool_app,
            system=(spec.system_prompt or "") + grounding,
            max_iters=max_iters,
            temperature=temperature,
        )
        try:
            turn = await asyncio.wait_for(coro, timeout=spec.timeout_s) if spec.timeout_s else await coro
        except TimeoutError:
            return AgentRunResult(
                runner_id=self.runner_id,
                returncode=-1,
                timeout=True,
                duration_s=round(time.monotonic() - started, 2),
                error="timeout",
                raw={
                    "provider": getattr(provider, "name", ""),
                    "session_id": session_id,
                    "workspace": str(workspace),
                },
            )

        return AgentRunResult(
            runner_id=self.runner_id,
            returncode=0,
            timeout=False,
            duration_s=round(time.monotonic() - started, 2),
            raw={
                "provider": getattr(provider, "name", ""),
                "provider_kind": getattr(provider, "kind", ""),
                "session_id": session_id,
                "workspace": str(workspace),
                "stop_reason": turn.stop_reason,
                "usage": turn.usage,
            },
        )


def resolve_agent_runner(
    runtime: Any,
    runner_id: str | None = None,
    *,
    app_ref: Any | None = None,
    provider_id: str | None = None,
) -> AgentRunner:
    """Resolve a runner by id.

    Unsupported ids fail explicitly so config mistakes do not silently route to
    the default. ``eos-agent:<provider>`` is a shorthand for pinning the
    tool-capable think provider used by EosAgentRunner.
    """

    rid = (runner_id or "claude-cli").strip() or "claude-cli"
    inline_provider = None
    if ":" in rid:
        rid, inline_provider = rid.split(":", 1)
        rid = rid.strip()
        inline_provider = inline_provider.strip() or None
    if rid == "claude-cli":
        if inline_provider:
            raise ValueError(
                f"runner 'claude-cli' does not take a ':<provider>' suffix: {runner_id!r}"
            )
        return ClaudeCliRunner(runtime)
    if rid == "eos-agent":
        if app_ref is None:
            raise ValueError("eos-agent runner requires app_ref")
        return EosAgentRunner(app_ref, provider_id=provider_id or inline_provider)
    raise ValueError(f"unsupported agent runner: {rid!r}")


def _configured_runner_id(app: Any, override: str | None = None) -> str:
    if override:
        return override.strip() or "claude-cli"
    try:
        configured = app.app_config("runner", "claude-cli")
    except Exception:
        configured = "claude-cli"
    return (str(configured or "claude-cli").strip() or "claude-cli")


def _configured_provider_id(app: Any, override: str | None = None) -> str | None:
    if override:
        return override.strip() or None
    try:
        configured = app.app_config("runner_provider", None)
    except Exception:
        configured = None
    return str(configured).strip() if configured else None


def effective_runner_config(
    app: Any,
    runner_id: str | None = None,
    provider_id: str | None = None,
) -> tuple[str, str | None]:
    """The ``(runner_id, provider_id)`` a run will use — overrides else app config.

    Call at queue time and persist both on the run record (``runner_config`` /
    ``runner_provider``), then pass them back into ``resolve_app_runner`` at
    execution time. Freezing the resolved pair at queue time means a config
    edit between queue and run can't shift a queued run — bench results stay
    attributable to the runner that was actually requested.
    """
    return _configured_runner_id(app, runner_id), _configured_provider_id(app, provider_id)


def resolve_app_runner(
    self: Any,
    runner_id: str | None = None,
    provider_id: str | None = None,
) -> tuple[AgentRunner | None, str | None]:
    """Resolve an app's configured agent runner, or ``(None, <non-empty reason>)``.

    ``runner_id``/``provider_id`` are per-run overrides (A/B benching one run
    against a different runner without flipping the config default); ``None``
    falls back to the app's ``[apps.<id>] runner`` / ``runner_provider`` config
    keys. First arg is the app, so harness apps re-bind this directly as a
    method per the multi-module convention (app-builder + fix-agent are the
    two consumers that earned the extraction).
    """
    rid = _configured_runner_id(self, runner_id)
    pid = _configured_provider_id(self, provider_id)
    runtime = self.service("agent-runtime")
    # agent-runtime is only the claude-cli backend's dependency; the eos-agent
    # runner must stay usable on a machine without that plugin (the whole point
    # of the runner abstraction).
    if runtime is None and rid.split(":", 1)[0].strip() == "claude-cli":
        return None, "agent-runtime plugin not loaded"
    try:
        runner = resolve_agent_runner(runtime, rid, app_ref=self, provider_id=pid)
    except ValueError as exc:
        return None, str(exc)
    if not runner.available():
        return None, runner.unavailable_reason() or f"runner '{runner.runner_id}' unavailable"
    return runner, None
