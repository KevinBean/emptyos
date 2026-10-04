"""`eos store ...` — Store app CLI.

Thin Typer wrapper over the Store app's /store/api/install endpoints.
The daemon must be running since the install logic handles dependency
resolution and state updates.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import typer

# emptyos.cli.main is imported inside functions to avoid circular import.

store_app = typer.Typer(
    name="store",
    help="Manage apps, plugins, and skills.",
    no_args_is_help=True,
)


def _post_install(kind: str, item_id: str, install_deps: bool, action: str) -> None:
    from emptyos.cli.main import console, _daemon_url, _daemon_cli_headers, _find_config

    daemon = _daemon_url()
    if not daemon:
        console.print("[red]The daemon must be running to manage store items.[/red]")
        console.print("[dim]Start it with `eos start` or run this command with `--sandbox`.[/dim]")
        raise typer.Exit(1)

    cfg_path = _find_config()
    payload = json.dumps({"install_deps": install_deps}).encode()
    # The API endpoint is mounted on the store app's route: /store/api/...
    url = f"{daemon}/store/api/{action}/{kind}/{item_id}"

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers=_daemon_cli_headers(cfg_path),
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())

        if result.get("ok"):
            console.print(f"[green]{result.get('message', 'Success')}[/green]")
            deps = result.get("deps_installed")
            if deps:
                console.print(f"[dim]Also installed dependencies: {', '.join(deps)}[/dim]")
        else:
            if result.get("needs_confirm"):
                console.print(f"[yellow]{result.get('message')}[/yellow]")
            else:
                console.print(f"[red]Error:[/red] {result.get('error', 'Unknown error')}")
                raise typer.Exit(1)

    except urllib.error.HTTPError as e:
        console.print(f"[red]Daemon request failed (HTTP {e.code}).[/red]")
        try:
            body = e.read().decode()
            if body:
                console.print(f"[dim]{body}[/dim]")
        except Exception:
            pass
        raise typer.Exit(1)
    except (urllib.error.URLError, OSError) as e:
        console.print(f"[red]Failed to connect to the daemon: {e}[/red]")
        raise typer.Exit(1)


@store_app.command("install")
def install_cmd(
    item_id: str = typer.Argument(..., help="ID of the app/plugin/skill to install"),
    kind: str = typer.Option("apps", "--kind", help="Kind of item (apps/plugins/skills)"),
    install_deps: bool = typer.Option(
        False,
        "--install-deps",
        "-y",
        help="Automatically install missing dependencies"
    ),
):
    """Install an app, plugin, or skill."""
    _post_install(kind, item_id, install_deps, "install")


@store_app.command("uninstall")
def uninstall_cmd(
    item_id: str = typer.Argument(..., help="ID of the app/plugin/skill to uninstall"),
    kind: str = typer.Option("apps", "--kind", help="Kind of item (apps/plugins/skills)"),
):
    """Uninstall an app, plugin, or skill."""
    _post_install(kind, item_id, False, "uninstall")
