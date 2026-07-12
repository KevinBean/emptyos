"""WorktreeApp — a thin BaseApp facade that redirects file tools into a git worktree.

The SubAgent tool's ``isolate="worktree"`` mode runs a nested agent turn whose
file-mutating tools (Edit, Write, Bash) land in an isolated git worktree instead
of the live tree, so a background/parallel subagent can't clobber the working
copy. The whole redirection rides one seam: every file tool resolves relative
paths through ``resolve_path(app, ...)`` → ``repo_root(app)`` → ``app.repo_root``
(`emptyos/sdk/agent_tools/base.py`). Override just that property and all three
tools follow — no per-tool plumbing.

Everything else (provider resolution, ``service``, ``runs``, ``emit``, kernel,
capabilities) delegates to the wrapped app unchanged. This is a facade, not a
subclass: it owns nothing except the ``repo_root`` override.

Second consumer: ``EosAgentRunner`` (``emptyos/sdk/agent_runner.py``) wraps its
``app_ref`` in this facade to honor a foreign ``AgentRunSpec.cwd`` (an
app-builder worktree) — same seam, run-scoped instead of subagent-scoped.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emptyos.sdk import BaseApp


class WorktreeApp:
    """Delegate-everything wrapper that reports a worktree as the repo root."""

    def __init__(self, app: BaseApp, worktree_root: Path):
        # Bypass __setattr__/__getattr__ recursion by writing to __dict__.
        object.__setattr__(self, "_wrapped", app)
        object.__setattr__(self, "_worktree_root", Path(worktree_root))

    @property
    def repo_root(self) -> Path:
        """The seam — file tools resolve relative paths against this."""
        return self._worktree_root

    def __getattr__(self, name: str):
        # Only reached for attributes not found on the instance (repo_root is a
        # real property, so it never lands here). Forward everything else.
        return getattr(self._wrapped, name)

    def __setattr__(self, name: str, value):
        # Mutations flow to the wrapped app so state the sub-turn writes
        # (caches, counters) lands on the real app, not this ephemeral facade.
        setattr(self._wrapped, name, value)
