"""Target Protocol — one Target implementation per native stack.

A Target answers: what's required to build it (preflight), how to scaffold a
new project, how to run it in dev, how to build a release artifact, and how
to report its current on-disk state. The forge app calls these verbs without
caring which stack is behind them.

Targets are registered in `targets/__init__.py` as a `TARGETS` dict — data-
driven dispatch, not inheritance. To add a new target (CLI, Flutter, …),
write a sibling module and add it to the registry. Mirrors the
DEFAULT_CLI_ADAPTERS shape in plugins/agent-runtime/plugin.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

LogCallback = Callable[[bytes], None]


@dataclass
class Check:
    """One preflight row — installed toolchain check."""

    name: str          # e.g. "cargo"
    ok: bool
    detail: str        # version string ("1.78.0") or install hint
    hint_url: str = ""  # optional URL to install instructions


@dataclass
class ScaffoldCtx:
    """Inputs to scaffold a new project."""

    project_id: str    # slug — used as repo dir name + vault note stem
    name: str          # human-readable title
    root: Path         # absolute parent dir (already expanduser'd)


@dataclass
class ScaffoldResult:
    repo_path: Path
    log_path: Path
    ok: bool = True
    error: str = ""


@dataclass
class BuildResult:
    success: bool
    artifacts: list[Path] = field(default_factory=list)
    log_path: Path | None = None
    duration_s: float = 0.0
    error: str = ""


@dataclass
class ReleaseResult:
    """Outcome of cutting a release. `pushed` is False when the repo has no
    remote — the tag still lands locally, just not on GitHub."""

    success: bool
    version: str = ""        # "1.2.3" (no leading v)
    tag: str = ""            # "v1.2.3"
    commit_sha: str = ""
    pushed: bool = False
    files_bumped: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class ProcessRecord:
    """Long-running dev process — owned by the forge app instance."""

    pid: int
    started_at: float
    log_path: Path
    # `proc` is asyncio.subprocess.Process at runtime; typed loose so the
    # dataclass stays import-light.
    proc: object


class Target(Protocol):
    """Verb interface every native stack implements.

    `runtime` is the agent-runtime service (`self.require("agent-runtime")`)
    — one-shot subprocess driver. `on_log` is a callback the forge app uses
    to append each stdout line to a live tail file.
    """

    id: str
    name: str
    description: str
    coming_soon: bool

    def preflight(self) -> list[Check]: ...

    async def scaffold(
        self,
        ctx: ScaffoldCtx,
        runtime,
        on_log: LogCallback,
    ) -> ScaffoldResult: ...

    async def dev(
        self,
        repo_path: Path,
        log_path: Path,
        on_log: LogCallback,
    ) -> ProcessRecord: ...

    async def build(
        self,
        repo_path: Path,
        runtime,
        on_log: LogCallback,
    ) -> BuildResult: ...

    async def release(
        self,
        repo_path: Path,
        version: str,
        runtime,
        on_log: LogCallback,
    ) -> ReleaseResult: ...

    async def status(self, repo_path: Path) -> dict: ...
