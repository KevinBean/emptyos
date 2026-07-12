"""`eos bus ...` — Agent Context Bus subcommand.

Thin Typer wrapper over :mod:`emptyos.sdk.agent_bus`. Kept kernel-free: the
bus is pure filesystem work, so this stays in the fast CLI startup path
(no daemon boot, no VaultIndex).
"""

from __future__ import annotations

import sys

import typer

from emptyos.sdk.agent_bus import (
    RippleError,
    check_dry_run,
    run_import,
    run_status,
    run_transpile,
)

bus_app = typer.Typer(
    name="bus",
    help="Agent Context Bus — sync CLAUDE.md / AGENTS.md / .claude/* across workspaces.",
    no_args_is_help=True,
)


@bus_app.command("status")
def status_cmd(
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root path"),
) -> None:
    """Show last-import / last-ripple / counts for the workspace's bus."""
    run_status(workspace)


@bus_app.command("import")
def import_cmd(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """Initialize or refresh .agent-bus/ from CLAUDE.md + .claude/rules + .claude/skills."""
    try:
        run_import(workspace)
    except RippleError as e:
        typer.secho(f"[ERROR] {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=e.code)


@bus_app.command("ripple")
def ripple_cmd(
    workspace: str = typer.Option(".", "--workspace", "-w"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report changes without writing"),
    force: bool = typer.Option(False, "--force", help="Overwrite native edits"),
) -> None:
    """Transpile the canonical store back into the workspace's native files."""
    try:
        if dry_run:
            changes = check_dry_run(workspace)
            raise typer.Exit(code=1 if changes else 0)
        run_transpile(workspace, force=force)
    except RippleError as e:
        typer.secho(f"[ERROR] {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=e.code)


@bus_app.command("transpile")
def transpile_cmd(
    workspace: str = typer.Option(".", "--workspace", "-w"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Alias for `ripple` without the dry-run option (explicit one-way sync)."""
    try:
        run_transpile(workspace, force=force)
    except RippleError as e:
        typer.secho(f"[ERROR] {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=e.code)
