"""`eos loops ...` — inspect EmptyOS's feedback-loop registry.

The loop registry (`emptyos/sdk/loops.py`) is the single place that NAMES every
feedback loop in EmptyOS — the surface that closes the discoverability gap
``docs/ENGINEERING-WORK-LOOP.md`` admits ("these surfaces existed but weren't
named as a single loop"). This is that CLI.

Kernel-free: the registry is pure data, so every subcommand reads it directly —
no daemon, no discovery, no boot. Built to the agent-facing output convention in
`.claude/rules/agent-cli.md` — every command accepts ``--json`` and emits the
``{ok, code, message, data}`` envelope with exit-code-as-signal.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from emptyos.cli._common import emit as _emit
from emptyos.sdk import loops as _loops

loops_app = typer.Typer(
    name="loops",
    help="Inspect the feedback-loop registry (eos loops list/show/stages).",
    no_args_is_help=True,
)

console = Console()

_STATUS_COLOR = {"live": "green", "dark": "yellow", "doc": "dim"}


def _loop_row(lp: _loops.Loop) -> dict:
    return {
        "id": lp.id,
        "name": lp.name,
        "family": lp.family,
        "status": lp.status,
        "flag": lp.flag,
        "stages": list(lp.stages),
        "summary": lp.summary,
        "components": list(lp.components),
    }


@loops_app.command("list")
def list_cmd(
    family: str = typer.Option(None, "--family", help="Filter: self-improve|primitive|self-audit|engineering|agent"),
    status: str = typer.Option(None, "--status", help="Filter: live|dark|doc"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """List every feedback loop (optionally filtered by family or status)."""
    rows = _loops.all_loops()
    if family:
        rows = [lp for lp in rows if lp.family == family]
    if status:
        rows = [lp for lp in rows if lp.status == status]

    data = {"loops": [_loop_row(lp) for lp in rows], "summary": _loops.summary()}

    def human():
        if not rows:
            console.print("[dim]No loops match.[/dim]")
            return
        t = Table(title="EmptyOS feedback loops")
        for col in ("id", "name", "family", "status", "stages"):
            t.add_column(col)
        for lp in rows:
            color = _STATUS_COLOR.get(lp.status, "white")
            status_cell = f"[{color}]{lp.status}[/{color}]"
            t.add_row(lp.id, lp.name, lp.family, status_cell, " ".join(lp.stages))
        console.print(t)
        s = _loops.summary()
        console.print(
            f"[dim]{s['total']} loops · {s['live']} live · {s['dark']} dark · "
            f"stage coverage {s['stage_coverage']}[/dim]"
        )

    _emit(True, "ok", f"{len(rows)} loop(s)", data, as_json=as_json, human=human)


@loops_app.command("show")
def show_cmd(
    loop_id: str = typer.Argument(..., help="Loop id, e.g. test-fix-verify"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """Show the full registry entry for one loop."""
    lp = _loops.by_id(loop_id)
    if lp is None:
        _emit(False, "not_found", f"loop {loop_id!r} not in registry", None, as_json=as_json)
        return  # unreachable (typer.Exit)

    def human():
        color = _STATUS_COLOR.get(lp.status, "white")
        console.print(f"[bold]{lp.id}[/bold]  ({lp.family})")
        console.print(f"  name:    {lp.name}")
        console.print(f"  status:  [{color}]{lp.status}[/{color}]" + (f"  flag=[yellow]{lp.flag}[/yellow]" if lp.flag else ""))
        console.print(f"  stages:  {', '.join(_loops.STAGE_LABEL[s] for s in lp.stages)}")
        console.print(f"  summary: {lp.summary}")
        if lp.components:
            console.print("  components:")
            for c in lp.components:
                console.print(f"    - {c}")

    _emit(True, "ok", lp.id, _loop_row(lp), as_json=as_json, human=human)


@loops_app.command("stages")
def stages_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """Stage-coverage view — how many loops implement each of the 5 stages."""
    coverage = _loops.stage_coverage()
    data = {
        "stages": [
            {
                "stage": s,
                "label": _loops.STAGE_LABEL[s],
                "count": coverage[s],
                "loops": [lp.id for lp in _loops.loops_covering(s)],
            }
            for s in _loops.STAGES
        ],
        "summary": _loops.summary(),
    }

    def human():
        t = Table(title="Loop stage coverage")
        for col in ("stage", "count", "loops"):
            t.add_column(col)
        for s in _loops.STAGES:
            ids = ", ".join(lp.id for lp in _loops.loops_covering(s))
            t.add_row(_loops.STAGE_LABEL[s], str(coverage[s]), ids)
        console.print(t)

    _emit(True, "ok", "5 stages", data, as_json=as_json, human=human)
