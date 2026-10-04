"""Tool-execution isolation policy — gate side-effecting tools on the
*unsupervised* agent-loop path.

EmptyOS's existing agent gates are about *permission / approval* — the
per-tool consent prompt, the autopilot eligibility floor, the room review
gate. None of them are *execution isolation*: when the agent loop reaches
``await tool.run(app, **input)`` (``agent_loop.py``), the tool runs raw in the
daemon process with full privileges. That is fine while a human is reviewing
each action (interactive ``eos chat``, the rooms ``[DO:]`` gate), but it is the
wrong default for an *unsupervised* loop (autopilot auto-apply, a headless
staff/fix-drain agent) running side-effecting tools (``Bash``/``Write``/``Edit``).

This module is the missing seam, distilled from AgentScope 2.0's "hardened
per-tool-call sandbox" idea but built over EmptyOS primitives and the pin:

  * ``decide()`` — a **pure** policy returning ``"inline" | "isolate" | "refuse"``.
    Default-safe: readonly tools and supervised sessions always run inline;
    only an *unsupervised, side-effecting* tool is isolated, and a tool we
    can't yet isolate is refused (fail-safe, not fail-open).
  * ``run_bash_jailed()`` — the v1 executor: runs ``Bash`` with its cwd forced
    into a per-session **jailed workspace directory** under ``data/`` and a
    path-jail that refuses absolute / ``..``-escaping paths.

Why a jailed directory and not the ``sandbox-pool`` plugin? sandbox-pool
isolates whole *daemons* (~150 MB each, leased/restarted) — leasing one to run
a single shell command is the wrong primitive: heavyweight and semantically
mismatched. Per-call Bash isolation only needs a scoped *directory*. The
daemon-level pool stays the documented escalation path for Phase 2 if a
consumer ever needs network/process isolation, not just filesystem scoping.

Honesty about strength: v1 is **process-level filesystem scoping**, not a
kernel sandbox. The jailed command runs as the same OS user; the path-jail
blocks the obvious escapes (absolute paths outside the jail, ``..`` traversal)
but is heuristic. It is a real, bounded improvement over "raw in the daemon",
not a security boundary against a determined adversary. Stronger isolation is
the Phase 2 sandbox-pool/container path.

Everything here is dark by default: ``decide(flag_on=False, ...)`` returns
``"inline"`` unconditionally, so with the feature flag off the agent loop is
byte-for-byte unchanged. See ``.claude/rules/autopilot-grants.md`` (the sibling
permission axis) and the approved plan.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from emptyos.sdk.agent_tools.base import ToolResult

if TYPE_CHECKING:
    from emptyos.sdk import BaseApp
    from emptyos.sdk.agent_tools.base import Tool

Decision = Literal["inline", "isolate", "refuse"]

# Feature flag key, read via ``BaseApp.app_config`` → ``[apps.agent]`` section
# (matches the podcast ``feature.pipeline.enabled`` precedent). Default off.
FLAG_KEY = "feature.tool_isolation.enabled"

# Side-effecting tools we have a v1 isolation executor for. Everything else
# that is side-effecting is *refused* on the unsupervised path until an
# executor exists (fail-safe). Bash is the highest-risk and the only one whose
# effect is cleanly scoped by a cwd jail.
ISOLATABLE_TOOLS: frozenset[str] = frozenset({"Bash"})


def decide(
    *,
    tool_name: str,
    is_readonly: bool,
    supervised: bool,
    flag_on: bool,
    isolatable: frozenset[str] = ISOLATABLE_TOOLS,
) -> Decision:
    """Pure isolation decision. No I/O, no objects — trivially unit-testable.

    Truth table (first match wins):

      flag off                         -> inline   (dark default; zero change)
      readonly tool                    -> inline   (nothing to isolate)
      supervised session               -> inline   (the human is the boundary)
      unsupervised + side-effecting:
        tool has an executor           -> isolate
        tool has no executor           -> refuse   (fail-safe)

    The caller computes ``is_readonly`` via ``tool.is_readonly(input)`` (which
    is input-dependent for tools like ``Fetch``) and ``flag_on`` from config.
    """
    if not flag_on:
        return "inline"
    if is_readonly:
        return "inline"
    if supervised:
        return "inline"
    return "isolate" if tool_name in isolatable else "refuse"


def jailed_workspace(app: BaseApp | None, session_id: str) -> Path:
    """Per-session jail directory under the daemon's data dir.

    ``data/tool-isolation/<session_id>/``. Created lazily by the executor.
    Defends against a missing/stub ``app`` (unit tests pass a bare object) by
    falling back to ``./data``.
    """
    data_dir: Path | None = None
    try:
        dd = getattr(app, "data_dir", None)
        if dd:
            data_dir = Path(dd)
    except Exception:
        data_dir = None
    if data_dir is None:
        data_dir = Path("data")
    safe = "".join(c for c in (session_id or "default") if c.isalnum() or c in "-_") or "default"
    return data_dir / "tool-isolation" / safe


def _is_rooted_any_os(token: str) -> bool:
    """True if the token is an absolute/rooted path in *either* OS's grammar.

    ``Path.is_absolute()`` is host-specific — on Windows ``/etc/passwd`` is NOT
    absolute (no drive), and on POSIX ``C:\\x`` is NOT absolute. The jail must
    treat both forms as rooted so a POSIX-absolute escape can't sneak past on a
    Windows host (and vice-versa). Rooted = leading ``/`` or ``\\``, or a
    ``X:`` drive prefix.
    """
    norm = token.replace("\\", "/")
    if norm.startswith("/"):
        return True
    return len(token) >= 2 and token[1] == ":" and token[0].isalpha()


def command_within_jail(command: str, *, jail_dir: Path) -> tuple[bool, str]:
    """Best-effort path-jail for a Bash command string.

    Returns ``(ok, reason)``. ``ok=False`` means the command references a path
    that escapes ``jail_dir`` and must be refused. Heuristic by design (see the
    module docstring) — it parses argv with ``shlex`` and rejects any token that:

      * contains a ``..`` path segment (traversal), or
      * is rooted (``/etc``, ``C:\\x``) and not provably inside ``jail_dir``.

    A token rooted only in the *foreign* OS's grammar (e.g. ``/etc`` on Windows)
    can't be inside a host jail and is rejected outright. A command that fails
    to tokenise (unbalanced quotes, shell soup) is refused — on the unsupervised
    path "couldn't prove it's safe" means don't run it.
    """
    if not command.strip():
        return False, "empty command"
    try:
        argv = shlex.split(command, posix=sys.platform != "win32")
    except ValueError:
        return False, "command could not be parsed for the path-jail"

    jail_resolved = jail_dir.resolve()
    for tok in argv:
        if not tok or tok.startswith("-"):
            continue
        if ".." in tok.replace("\\", "/").split("/"):
            return False, f"path traversal ('..') is not allowed in an isolated command: {tok!r}"
        if not _is_rooted_any_os(tok):
            continue  # relative path — resolves inside the jail cwd, fine
        p = Path(tok)
        if p.is_absolute():
            # Rooted in the host OS — allowed iff genuinely under the jail.
            try:
                p.resolve().relative_to(jail_resolved)
            except (ValueError, OSError):
                return False, (
                    f"absolute path {tok!r} is outside the isolation jail "
                    f"({jail_resolved}); isolated commands may only touch the jail"
                )
        else:
            # Rooted only in the other OS's grammar (e.g. /etc on Windows) —
            # can't be inside a host jail; treat as an escape.
            return False, (
                f"rooted path {tok!r} is outside the isolation jail; "
                "isolated commands may only touch the jail"
            )
    return True, ""


async def run_bash_jailed(
    tool: Tool,
    app: BaseApp | None,
    tool_input: dict,
    *,
    jail_dir: Path,
) -> ToolResult:
    """Execute the ``Bash`` tool with its cwd forced into ``jail_dir``.

    Path-jails the command first (refusing absolute/``..`` escapes), creates the
    jail dir, then delegates to the real ``BashTool.run`` with ``cwd`` overridden
    to the jail so relative-path effects land in the throwaway tree. The caller
    must only route ``Bash`` here (``decide()`` guarantees this).
    """
    command = (tool_input or {}).get("command", "") or ""
    ok, reason = command_within_jail(command, jail_dir=jail_dir)
    if not ok:
        return ToolResult(
            ok=False,
            content=(
                f"error: [isolation] refused — {reason}. This command ran on the "
                "unsupervised agent path, where side-effecting commands are jailed "
                "to a per-session workspace. Use relative paths inside the jail, or "
                "run this in a supervised session."
            ),
            display={"name": "Bash", "isolated": True, "refused": True},
        )
    try:
        jail_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return ToolResult(
            ok=False,
            content=f"error: [isolation] could not create jail workspace: {e}",
            display={"name": "Bash", "isolated": True, "refused": True},
        )
    forced = dict(tool_input or {})
    forced["cwd"] = str(jail_dir)
    result = await tool.run(app, **forced)
    # Tag the display so the UI / audit can see this ran isolated.
    disp = dict(result.display or {})
    disp["isolated"] = True
    disp["jail"] = str(jail_dir)
    return ToolResult(ok=result.ok, content=result.content, display=disp)
