"""`eos verb ...` — inspect + validate the unified verb registry.

The verb registry (`emptyos/sdk/verb_registry.py`) is the single source of truth for
every invokable app verb (voice / assistant / mcp / agent surfaces + the autopilot
eligibility floor). It shipped without a CLI; this is that CLI.

First command built to the agent-facing output convention in
`.claude/rules/agent-cli.md` — every command accepts ``--json`` and emits the
``{ok, code, message, data}`` envelope with exit-code-as-signal.

Kernel-lite: ``list`` / ``describe`` build a registry from discovered manifests
(``Kernel(...).apps.get_verbs()`` — discover only, no full boot). ``validate``
prefers the running daemon's authoritative drift sweep (``/agent/api/verbs/sweep``,
which can force-load apps to check method existence) and falls back to a local
manifest-static check when the daemon is down.
"""

from __future__ import annotations

import contextlib
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from emptyos.cli._common import bearer_headers
from emptyos.cli._common import emit as _emit

verb_app = typer.Typer(
    name="verb",
    help="Inspect + validate the unified verb registry (eos verb list/describe/validate).",
    no_args_is_help=True,
)

console = Console()


def _load_registry():
    """Build a VerbRegistry from discovered manifests (kernel-lite — discover only).

    Discovery emits syslog warnings to stdout; redirect to stderr so ``--json``
    output stays a single clean object (per .claude/rules/agent-cli.md).
    """
    from emptyos.cli.main import _find_config
    from emptyos.kernel import Kernel

    with contextlib.redirect_stdout(sys.stderr):
        kernel = Kernel(_find_config())
        kernel.apps.discover()
        return kernel.apps.get_verbs()


@verb_app.command("list")
def list_cmd(
    app: str = typer.Argument(None, help="Filter to one app id"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """List every declared verb (optionally filtered to one app)."""
    registry = _load_registry()
    rows = registry.menu()
    if app:
        rows = [r for r in rows if r["app"] == app]

    def human():
        if not rows:
            console.print("[dim]No verbs declared.[/dim]")
            return
        t = Table(title="Verb registry")
        for col in ("verb", "method", "eligibility", "surfaces"):
            t.add_column(col)
        for r in rows:
            t.add_row(r["verb"], r["method"], r["eligibility"], ",".join(r["surfaces"]))
        console.print(t)

    _emit(True, "ok", f"{len(rows)} verb(s)", {"verbs": rows}, as_json=as_json, human=human)


@verb_app.command("describe")
def describe_cmd(
    verb: str = typer.Argument(..., help="Verb to describe, e.g. task.add"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """Show the full registry entry for one verb."""
    registry = _load_registry()
    entry = registry.get(verb)
    if entry is None:
        _emit(False, "not_found", f"verb {verb!r} not declared", None, as_json=as_json)
        return  # unreachable (typer.Exit), kept for clarity

    data = {
        "verb": entry.verb,
        "app": entry.app_id,
        "method": entry.method,
        "summary": entry.summary,
        "args": entry.args,
        "eligibility": entry.eligibility,
        "surfaces": list(entry.surfaces),
        "voice": entry.voice,
        "assistant": entry.assistant,
    }

    def human():
        console.print(f"[bold]{entry.verb}[/bold]  ({entry.app_id})")
        console.print(f"  method:      {entry.method}")
        if entry.summary:
            console.print(f"  summary:     {entry.summary}")
        console.print(f"  args:        {entry.args or '—'}")
        console.print(f"  eligibility: {entry.eligibility}")
        console.print(f"  surfaces:    {', '.join(entry.surfaces) or '—'}")
        if entry.voice:
            console.print(f"  voice:       {entry.voice}")
        if entry.assistant:
            console.print(f"  assistant:   {entry.assistant}")

    _emit(True, "ok", entry.verb, data, as_json=as_json, human=human)


def _daemon_sweep() -> dict | None:
    """Hit the running daemon's authoritative drift sweep, or None if unreachable."""
    from emptyos.cli.main import _daemon_url

    base = _daemon_url()
    if not base:
        return None
    import urllib.request

    headers = bearer_headers()
    try:
        req = urllib.request.Request(f"{base}/agent/api/verbs/sweep?load=1", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception:
        return None


@verb_app.command("validate")
def validate_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable envelope"),
) -> None:
    """Drift check: every declared verb's method must exist on its app.

    Prefers the running daemon's sweep (force-loads apps → checks method existence).
    Falls back to a local manifest-static check (parse rejects only) when the daemon
    is down — method-existence drift is only catchable with the daemon up.
    """
    sweep = _daemon_sweep()
    if sweep is not None:
        drift = sweep.get("drift") or []
        ok = not drift
        msg = "no drift" if ok else f"{len(drift)} verb(s) drifted"
        code = "ok" if ok else "drift"

        def human():
            if ok:
                console.print(f"[green]✓ {sweep.get('count', 0)} verbs, no drift[/green]")
            else:
                console.print(f"[red]✗ {len(drift)} drifted:[/red]")
                for d in drift:
                    console.print(f"  {d['verb']} → missing {d.get('missing')}")
            if sweep.get("unloaded"):
                console.print(f"[dim]unloaded: {', '.join(sweep['unloaded'])}[/dim]")

        _emit(ok, code, msg, {"source": "daemon", **sweep}, as_json=as_json, human=human)
        return

    # Local fallback — manifest-static parse rejects only.
    from emptyos.cli.main import _find_config
    from emptyos.kernel import Kernel
    from emptyos.sdk.verb_registry import parse_verb_entry

    with contextlib.redirect_stdout(sys.stderr):
        kernel = Kernel(_find_config())
        kernel.apps.discover()
    rejects: list[dict] = []
    for app_id, manifest in kernel.apps.manifests.items():
        raw = manifest.provides.get("verbs")
        if not raw:
            continue
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        allowed = {app_id, *(manifest.aliases or [])}
        for rv in raw:
            _, err = parse_verb_entry(rv if isinstance(rv, dict) else {}, app_id, allowed_app_halves=allowed)
            if err:
                rejects.append({"app": app_id, "error": err})

    ok = not rejects
    msg = (
        "no parse rejects (daemon down — method-existence NOT checked)"
        if ok
        else f"{len(rejects)} manifest reject(s)"
    )

    def human():
        if ok:
            console.print("[yellow]Daemon down — ran manifest-static check only.[/yellow]")
            console.print("[green]✓ no parse rejects[/green] (start the daemon for full drift check)")
        else:
            console.print(f"[red]✗ {len(rejects)} manifest reject(s):[/red]")
            for r in rejects:
                console.print(f"  {r['app']}: {r['error']}")

    _emit(ok, "ok" if ok else "invalid_args", msg, {"source": "local", "rejects": rejects},
          as_json=as_json, human=human)
