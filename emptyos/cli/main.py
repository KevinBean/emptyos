"""EmptyOS CLI — the `eos` command."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

# Force UTF-8 stdio on Windows so Rich output (middle dots, box drawing, emojis)
# renders correctly instead of showing replacement characters (�) under cp1252.
# Must run before any Rich Console is instantiated.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

import typer
from rich.console import Console
from rich.table import Table

from emptyos.kernel.app_loader import AppState

app = typer.Typer(
    name="eos",
    help="EmptyOS — A personal AI-powered operating system.",
    no_args_is_help=False,
)
console = Console()

app_cmd = typer.Typer(help="Manage apps")
service_cmd = typer.Typer(help="Manage services")
event_cmd = typer.Typer(help="Event bus operations")
config_cmd = typer.Typer(help="Configuration")
group_cmd = typer.Typer(help="Export groups — multi-app bundles")

app.add_typer(app_cmd, name="app")
app.add_typer(service_cmd, name="service")
app.add_typer(event_cmd, name="event")
app.add_typer(config_cmd, name="config")
app.add_typer(group_cmd, name="export-group")

from emptyos.cli.commands.boot import boot_command
from emptyos.cli.commands.init import init_command

app.command("init")(init_command)
app.command("boot")(boot_command)


def _find_config() -> str:
    """Find emptyos.toml by searching: EOS_CONFIG env, cwd, ~/.config/emptyos/, pointer file."""
    candidates = [
        os.environ.get("EOS_CONFIG", ""),
        "emptyos.toml",
        str(Path.home() / ".config" / "emptyos" / "emptyos.toml"),
    ]
    # Pointer written by `eos init` — absolute path to the real config.
    # Lets `eos` work from any directory after a one-time init.
    pointer = Path.home() / ".config" / "emptyos" / "config-path.txt"
    if pointer.exists():
        try:
            p = pointer.read_text(encoding="utf-8").strip()
            if p:
                candidates.append(p)
        except Exception:
            pass
    for c in candidates:
        if c and Path(c).exists():
            return c
    console.print("[red]No emptyos.toml found.[/red]")
    console.print("  Set EOS_CONFIG=/path/to/emptyos.toml or place it in the current directory.")
    console.print("  Run [bold]eos init[/bold] to create one interactively.")
    raise typer.Exit(1)


def _daemon_url() -> str | None:
    """Check if the EmptyOS daemon is running. Returns base URL or None."""
    try:
        config_path = _find_config()
    except (typer.Exit, SystemExit):
        return None
    from emptyos.kernel.config import Config

    config = Config(config_path)
    # Always probe via loopback; config.host may be 0.0.0.0 (bind-all) which is not a valid client address.
    client_host = "127.0.0.1" if config.host in ("0.0.0.0", "::") else config.host
    url = f"http://{client_host}:{config.port}"
    try:
        import urllib.request

        resp = urllib.request.urlopen(f"{url}/api/health", timeout=1)
        return url if resp.status == 200 else None
    except Exception:
        return None


def _get_kernel():
    from emptyos.kernel import Kernel

    k = Kernel(_find_config())
    k.apps.discover()
    return k


def _wire_app_cli_commands():
    """Discover apps and register their @cli_command methods as eos subcommands."""
    try:
        config_path = _find_config()
    except (typer.Exit, SystemExit):
        return  # No config — skip app CLI wiring

    from emptyos.kernel import Kernel

    kernel = Kernel(config_path)
    kernel.apps.discover()

    for cmd_name, manifest in kernel.apps.get_cli_commands().items():
        # Skip if a built-in command already uses this name
        existing = {c.name for c in app.registered_commands}
        existing.update(g.name for g in app.registered_groups if g.name)
        if cmd_name in existing:
            continue

        _register_app_command(cmd_name, manifest, config_path)


def _register_app_command(cmd_name: str, manifest, config_path: str):
    """Create a Typer command that talks to daemon (if running) or loads app locally."""
    cli_section = manifest.provides.get("cli", {})
    # Interactive commands (REPLs) must run in the client process — the daemon's
    # /api/cli endpoint buffers stdout and doesn't connect stdin, so proxying a
    # REPL through it produces mangled/empty output and orphaned sessions.
    is_interactive = cmd_name in (cli_section.get("interactive", []) or [])

    def make_handler(app_id: str, c_name: str, cfg_path: str, interactive: bool):
        def handler(
            args: list[str] = typer.Argument(None, help="Command arguments"),
        ):
            if not interactive:
                # Try daemon first — single kernel, shared state
                daemon = _daemon_url()
                if daemon:
                    _run_via_daemon(daemon, app_id, c_name, args)
                    return

            # Interactive, or no daemon — run in this process so stdin/stdout are live
            _run_locally(app_id, c_name, cfg_path, args)

        return handler

    handler = make_handler(manifest.id, cmd_name, config_path, is_interactive)
    help_text = cli_section.get("help", manifest.description)
    app.command(cmd_name, help=help_text)(handler)


def _run_via_daemon(daemon_url: str, app_id: str, cmd_name: str, args: list[str] | None):
    """Execute an app command via the running daemon's API."""
    import urllib.error
    import urllib.request

    # Map CLI args to a generic command endpoint
    payload = json.dumps(
        {
            "app": app_id,
            "command": cmd_name,
            "args": list(args) if args else [],
        }
    ).encode()

    try:
        req = urllib.request.Request(
            f"{daemon_url}/api/cli",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode())
        if result.get("output"):
            print(result["output"])
        elif result.get("error"):
            console.print(f"[red]{result['error']}[/red]")
    except urllib.error.URLError:
        # Daemon went away — fall back to local
        console.print("[dim]Daemon unreachable, running locally...[/dim]")
        _run_locally(app_id, cmd_name, _find_config(), args)


def _run_locally(app_id: str, cmd_name: str, cfg_path: str, args: list[str] | None):
    """Execute an app command by creating a local kernel."""
    from emptyos.kernel import Kernel

    async def _run():
        k = Kernel(cfg_path)
        k.apps.discover()
        instance = await k.apps.load(app_id)

        method = None
        for meta, m in instance.get_cli_methods():
            if meta["name"] == cmd_name:
                method = m
                break
        if not method:
            console.print(f"[red]Command '{cmd_name}' not found in app '{app_id}'[/red]")
            raise typer.Exit(1)

        sig = inspect.signature(method)
        params = [p for p in sig.parameters.values() if p.name != "self"]

        kwargs = {}
        arg_list = list(args) if args else []
        for i, param in enumerate(params):
            if i < len(arg_list):
                val = arg_list[i]
                ann = param.annotation
                if ann == int or ann is int:
                    val = int(val)
                elif ann == bool or ann is bool:
                    val = val.lower() in ("true", "1", "yes")
                kwargs[param.name] = val
            elif param.default is not inspect.Parameter.empty:
                kwargs[param.name] = param.default

        result = method(**kwargs)
        if inspect.isawaitable(result):
            await result

    asyncio.run(_run())


# Wire app CLI commands at import time (runs once when `eos` is invoked)
_wire_app_cli_commands()


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context):
    """Show status overview when called without subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    kernel = _get_kernel()

    console.print()
    console.print("[bold]EmptyOS[/bold]", style="cyan")
    console.print(f"  Name: {kernel.config.get('os.name', 'EmptyOS')}")
    console.print(f"  Config: {kernel.config.path}")
    notes = kernel.config.notes_path
    console.print(f"  Notes: {notes or '[dim]not configured[/dim]'}")
    console.print(f"  Web: http://{kernel.config.host}:{kernel.config.port}")
    console.print()

    # Capabilities
    caps = kernel.capabilities.list()
    if caps:
        console.print("[bold]Capabilities[/bold]")
        for name, cap in caps.items():
            providers = [p.name for p in cap.providers]
            console.print(f"  {name:<12} providers: {', '.join(providers)}")
        console.print()

    # Plugins
    kernel.plugins.discover()
    if kernel.plugins.manifests:
        console.print(f"[bold]Plugins[/bold] ({len(kernel.plugins.manifests)} discovered)")
        for m in kernel.plugins.manifests.values():
            services = ", ".join(m.provides.get("services", []))
            console.print(f"  {m.id:<20} {m.name:<30} -> {services}")
        console.print()

    # Apps
    manifests = kernel.apps.manifests
    if manifests:
        console.print(f"[bold]Apps[/bold] ({len(manifests)} discovered)")
        for m in manifests.values():
            state = kernel.apps.states.get(m.id, AppState.DISCOVERED)
            console.print(f"  {m.id:<20} {m.name:<30} [{state.value}]")
    else:
        console.print("[dim]No apps discovered.[/dim]")
    console.print()


@app.command()
def start(
    no_web: bool = typer.Option(False, "--no-web", help="Start without web dashboard"),
):
    """Start EmptyOS kernel, services, and web dashboard."""
    os.environ["EOS_DAEMON"] = "1"  # Signal non-interactive mode to human providers
    kernel = _get_kernel()

    async def _start():
        await kernel.start()

        # Load every enabled app (per store state) so web routes are available.
        # Uninstalled and disabled apps stay discovered (kernel.apps.manifests)
        # but unloaded — the store's catalog endpoint lists them, but no
        # routes mount until they're enabled + the daemon is restarted.
        enabled = kernel.apps.enabled_manifests()
        for app_id, manifest in enabled.items():
            if app_id not in kernel.apps.instances:
                try:
                    await kernel.apps.load(app_id)
                except Exception as e:
                    console.print(f"[yellow]Warning: failed to load '{app_id}': {e}[/yellow]")

        total_discovered = len(kernel.apps.manifests)
        loaded = len(kernel.apps.instances)
        if loaded < total_discovered:
            console.print(
                f"[green]Kernel started. {loaded}/{total_discovered} apps loaded "
                f"({total_discovered - loaded} not enabled — see /store).[/green]"
            )
        else:
            console.print(f"[green]Kernel started. {loaded} apps loaded.[/green]")

        if not no_web:
            import uvicorn

            from emptyos.web.server import create_server

            # --- Deployment mode safety check ---
            _mode = kernel.config.network_mode
            _host = kernel.config.host
            _token = kernel.config.auth_token
            _password = kernel.config.login_password
            if kernel.config.auth_required and not (_token or _password):
                console.print(
                    f"[bold red]Refusing to start.[/bold red] "
                    f"network.mode = '{_mode}' requires network.auth_token "
                    f"or network.password to be set."
                )
                console.print(
                    "  Set a long random token in emptyos.toml, e.g. "
                    "[cyan]auth_token = \"$(python -c 'import secrets;print(secrets.token_urlsafe(32))')\"[/cyan]"
                )
                console.print(
                    "  Or set a human-typeable password (browser login):\n"
                    "  [cyan]password = \"choose-something-strong\"[/cyan]"
                )
                return
            if kernel.config.is_remote_bind and _mode == "private" and not (_token or _password):
                # Reachable only when user explicitly set
                # `network.auth_required = false` — they've opted out of the
                # default-on auth gate that landed 2026-04-27.
                console.print(
                    f"[bold yellow]Warning:[/bold yellow] binding {_host} in "
                    f"'private' mode with auth disabled. Anyone on the same "
                    f"network can reach EmptyOS without a token. Your only "
                    f"gate is the network layer (Tailscale/VPN/firewall)."
                )
            if kernel.config.demo_enabled:
                console.print(
                    "[cyan]Demo mode enabled.[/cyan] GPU capabilities disabled; "
                    "BYOK (bring your own key) available in settings."
                )

            server = create_server(kernel)
            console.print(
                f"[green]Web dashboard at http://{_host}:{kernel.config.port}[/green] "
                f"[dim](mode: {_mode}{'  auth: on' if _token else ''})[/dim]"
            )
            # Suppress benign Windows WS disconnect noise: when a browser
            # tab dies abruptly, websockets-legacy logs a full traceback
            # ("data transfer failed" + WinError 121) before raising the
            # WebSocketDisconnect we already handle. Filter those out.
            import logging as _logging

            class _WSDisconnectFilter(_logging.Filter):
                def filter(self, record):
                    msg = record.getMessage()
                    if "data transfer failed" in msg:
                        return False
                    exc = getattr(record, "exc_info", None)
                    if exc and exc[1] is not None:
                        s = str(exc[1])
                        if "WinError 121" in s or "WinError 10054" in s:
                            return False
                    return True

            for _ln in ("websockets.protocol", "websockets.legacy.protocol", "websockets.server"):
                _logging.getLogger(_ln).addFilter(_WSDisconnectFilter())

            config = uvicorn.Config(
                server,
                host=_host,
                port=kernel.config.port,
                log_level="warning",
                ws_ping_interval=25,
                ws_ping_timeout=25,
            )
            srv = uvicorn.Server(config)
            try:
                await srv.serve()
            except KeyboardInterrupt:
                pass
            finally:
                await kernel.stop()
                console.print("[yellow]EmptyOS stopped.[/yellow]")

    asyncio.run(_start())


@app.command()
def health():
    """Check system health — capabilities, connectors, apps."""
    kernel = _get_kernel()

    async def _health():
        await kernel.start()
        health_svc = kernel.services.get_optional("health")
        if not health_svc:
            console.print("[red]Health plugin not loaded[/red]")
            raise typer.Exit(1)
        status = await health_svc.check()
        await kernel.stop()
        return status

    status = asyncio.run(_health())

    console.print()
    console.print("[bold]EmptyOS Health[/bold]", style="cyan")
    console.print(f"  Uptime: {status['uptime_seconds']}s")
    console.print()

    # Vault
    v = status["vault"]
    v_status = (
        "[green]OK[/green]" if v.get("status") == "ok" else f"[red]{v.get('status', '?')}[/red]"
    )
    v_info = f" ({v.get('files', '?')} files)" if v.get("files") else ""
    console.print(f"  Vault  {v_status}{v_info}")

    # Capabilities
    console.print()
    console.print("  [bold]Capabilities[/bold]")
    for name, cap in status["capabilities"].items():
        cap_status = "[green]OK[/green]" if cap["status"] == "ok" else "[yellow]degraded[/yellow]"
        active = [p["name"] for p in cap["providers"] if p["available"]]
        console.print(f"    {name:<12} {cap_status}  ({', '.join(active) if active else 'none'})")

    # Connectors
    if status["connectors"]:
        console.print()
        console.print("  [bold]Connectors[/bold]")
        for name, info in status["connectors"].items():
            c_status = (
                "[green]OK[/green]" if info["status"] == "ok" else f"[red]{info['status']}[/red]"
            )
            console.print(f"    {name:<16} {c_status}")

    # Apps
    console.print()
    loaded = sum(1 for a in status["apps"].values() if a["status"] in ("loaded", "started"))
    console.print(f"  [bold]Apps[/bold] ({loaded}/{len(status['apps'])} loaded)")

    # Integrity audit (filesystem-only, no app loading needed)
    try:
        from apps.integrity.app import IntegrityApp

        # Create a lightweight instance just for the audit
        ia = IntegrityApp.__new__(IntegrityApp)
        ia.kernel = kernel
        audit = ia._run_audit()
        console.print()
        console.print(
            f"  [bold]Integrity[/bold]  {audit['total_score']}/{audit['max_score']} ({audit['pct']}%)"
        )
        for name, dim in audit["dimensions"].items():
            if dim["score"] >= 8:
                icon, style = "+", "green"
            elif dim["score"] >= 5:
                icon, style = "~", "yellow"
            else:
                icon, style = "!", "red"
            console.print(f"    [{style}]{icon}[/{style}] {name}: {dim['score']}/10")
        if audit.get("growth_signals"):
            console.print()
            console.print("  [bold]Growth Signals[/bold]")
            for gs in audit["growth_signals"][:3]:
                console.print(f"    - {gs['signal']}")
    except Exception:
        pass

    console.print()


@app.command("check-release")
def check_release():
    """Scan committed code for personal data leaks."""
    import subprocess

    script = Path(__file__).parent.parent.parent / "scripts" / "check-personal.py"
    if not script.exists():
        console.print(f"[red]Scanner not found: {script}[/red]")
        raise typer.Exit(1)
    result = subprocess.run([sys.executable, str(script)], cwd=str(script.parent.parent))
    raise typer.Exit(result.returncode)


@app.command()
def release(
    action: str = typer.Argument("check", help="check | core | standard"),
):
    """Package EmptyOS for release.

    Actions:
        check    — run safety checks only (personal data + branding)
        core     — package minimum OS tier
        standard — package full community tier
    """
    import subprocess

    root = Path(__file__).parent.parent.parent
    script = root / "scripts" / "package-release.py"
    if not script.exists():
        console.print(f"[red]Packaging script not found: {script}[/red]")
        raise typer.Exit(1)

    if action == "check":
        args = [sys.executable, str(script), "--check"]
    elif action in ("core", "standard"):
        args = [sys.executable, str(script), action]
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("[dim]Usage: eos release {{check|core|standard}}[/dim]")
        raise typer.Exit(1)

    result = subprocess.run(args, cwd=str(root))
    raise typer.Exit(result.returncode)


@app.command()
def status():
    """Show services, apps, and recent events."""
    kernel = _get_kernel()

    table = Table(title="Services")
    table.add_column("Name")
    table.add_column("Status")
    for entry in kernel.services.list():
        table.add_row(entry.name, entry.status.value)
    console.print(table)

    table = Table(title="Apps")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("State")
    table.add_column("CLI")
    table.add_column("Web")
    for m in kernel.apps.manifests.values():
        state = kernel.apps.states.get(m.id, AppState.DISCOVERED)
        cli_cmds = ", ".join(m.provides.get("cli", {}).get("commands", []))
        web_prefix = m.provides.get("web", {}).get("prefix", "")
        table.add_row(m.id, m.name, state.value, cli_cmds, web_prefix)
    console.print(table)


@app_cmd.command("list")
def app_list():
    """List all discovered apps."""
    kernel = _get_kernel()
    if not kernel.apps.manifests:
        console.print("[dim]No apps found.[/dim]")
        return
    table = Table()
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Description")
    for m in kernel.apps.manifests.values():
        table.add_row(m.id, m.name, m.version, m.description)
    console.print(table)


@app_cmd.command("info")
def app_info(app_id: str):
    """Show full details for an app — auto-generated from manifest + code."""
    kernel = _get_kernel()
    m = kernel.apps.manifests.get(app_id)
    if not m:
        console.print(f"[red]App not found: {app_id}[/red]")
        raise typer.Exit(1)

    console.print()
    console.print(f"[bold]{m.name}[/bold] v{m.version}")
    console.print(f"  {m.description}")
    console.print()

    # Capabilities
    caps = m.requires.get("capabilities", [])
    if caps:
        console.print(f"  Capabilities: {', '.join(caps)}")

    # App dependencies
    dep_apps = m.requires.get("apps", [])
    if dep_apps:
        console.print(f"  Depends on:   {', '.join(dep_apps)}")

    # Connectors
    conns = m.requires.get("connectors", [])
    if conns:
        console.print(f"  Connectors:   {', '.join(conns)}")

    # Services
    svcs = m.requires.get("services", [])
    if svcs:
        console.print(f"  Services:     {', '.join(svcs)}")

    # CLI
    cli_cmds = m.provides.get("cli", {}).get("commands", [])
    if cli_cmds:
        console.print(f"  CLI:          eos {cli_cmds[0]}")

    # Web
    prefix = m.provides.get("web", {}).get("prefix", "")
    if prefix:
        pages_dir = m.path / "pages"
        ui_type = "custom UI" if pages_dir.exists() else "auto-generated"
        console.print(f"  Web:          {prefix}/ ({ui_type})")

    # API routes (from loaded instance or manifest)
    async def _show_routes():
        instance = await kernel.apps.load(app_id)
        routes = instance.get_web_methods()
        if routes:
            console.print("  API:")
            for meta, _ in routes:
                console.print(f"                {meta['method'].upper()} {prefix}{meta['path']}")

        # CLI methods with params
        cli_methods = instance.get_cli_methods()
        if cli_methods:
            for meta, method in cli_methods:
                sig = inspect.signature(method)
                params = [p for p in sig.parameters.values() if p.name != "self"]
                param_str = " ".join(
                    f"[{p.name}]" if p.default is not inspect.Parameter.empty else f"<{p.name}>"
                    for p in params
                )
                if param_str:
                    console.print(f"  Usage:        eos {meta['name']} {param_str}")

    asyncio.run(_show_routes())

    # Events
    emits = m.provides.get("events", {}).get("emits", [])
    if emits:
        console.print(f"  Emits:        {', '.join(emits)}")

    listens = m.requires.get("events", [])
    if listens:
        console.print(f"  Listens:      {', '.join(listens)}")

    # Files
    console.print(f"  Path:         {m.path}")
    pages_dir = m.path / "pages"
    if pages_dir.exists():
        pages = list(pages_dir.glob("*.html"))
        console.print(f"  Pages:        {len(pages)} file(s)")

    # Export support (surfaces [provides.export])
    export_cfg = m.provides.get("export", {})
    if export_cfg.get("enabled"):
        fb = ", ".join(export_cfg.get("fallbacks", [])) or "(defaults)"
        console.print(f"  Export:       yes ({export_cfg.get('mode', 'standalone')})")
        console.print(f"  Fallbacks:    {fb}")
    elif export_cfg:
        console.print("  Export:       declared but disabled")
    else:
        console.print("  Export:       no")

    console.print()


@app_cmd.command("export")
def app_export(
    app_id: str = typer.Argument(..., help="App id to export"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output path (dir, zip, or .html)"),
    fmt: str = typer.Option("dir", "--format", "-f", help="Bundle format: dir | zip | single-html"),
    verify: bool = typer.Option(
        False, "--verify", help="After build, open bundle headless and assert no console errors"
    ),
):
    """Export an app to a standalone HTML+JS bundle.

    The app must declare ``[provides.export].enabled = true`` in its manifest.
    """
    if fmt not in ("dir", "zip", "single-html"):
        console.print(f"[red]Unknown format: {fmt}[/red] — use dir | zip | single-html")
        raise typer.Exit(2)

    kernel = _get_kernel()
    manifest = kernel.apps.manifests.get(app_id)
    if not manifest:
        console.print(f"[red]App not found: {app_id}[/red]")
        raise typer.Exit(1)

    export_cfg = manifest.provides.get("export", {}) or {}
    if not export_cfg.get("enabled"):
        console.print(
            f"[red]App '{app_id}' has not declared [provides.export].enabled = true[/red]"
        )
        raise typer.Exit(1)

    async def _run():
        from emptyos.sdk.exporter import AppExporter

        instance = await kernel.apps.load(app_id)
        out_path = Path(out) if out else Path.cwd() / f"{app_id}-export"
        exporter = AppExporter(instance, out_dir=out_path, fmt=fmt)  # type: ignore[arg-type]
        result = await exporter.build()
        console.print(f"[green]✓[/green] Exported '{app_id}' → {result}")
        if verify:
            errors = await _verify_bundle(result, fmt)
            if errors:
                console.print("[red]Verification found issues:[/red]")
                for e in errors:
                    console.print(f"  • {e}")
                raise typer.Exit(3)
            console.print("[green]✓[/green] Verification clean")
        return result

    asyncio.run(_run())


async def _verify_bundle(path: Path, fmt: str) -> list[str]:
    """Open the exported bundle headless and collect console errors.

    Uses Playwright if available; falls back to a simple static-file sanity
    check when Playwright is not installed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        # Lightweight fallback: just check required files exist.
        if fmt == "zip":
            return [] if path.exists() and path.stat().st_size > 0 else ["empty zip"]
        if fmt == "single-html":
            return [] if path.exists() and path.stat().st_size > 0 else ["empty html"]
        errs = []
        for rel in ["index.html", "_assets/eos-export-shim.js", "_meta/export.json"]:
            if not (path / rel).exists():
                errs.append(f"missing {rel}")
        return errs

    # Only dir format is playwright-verifiable (needs file:// URL to an HTML file).
    if fmt != "dir":
        return []
    index = path / "index.html"
    if not index.exists():
        return ["no index.html"]

    errors: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on(
                "console",
                lambda msg: (
                    errors.append(f"console {msg.type}: {msg.text}")
                    if msg.type in ("error",)
                    else None
                ),
            )
            await page.goto("file:///" + str(index.resolve()).replace("\\", "/"))
            await page.wait_for_load_state("networkidle", timeout=5000)
        finally:
            await browser.close()
    return errors


@group_cmd.command("list")
def group_list():
    """Show every declared export group + member status."""
    from emptyos.sdk.exporter import load_groups

    kernel = _get_kernel()
    groups = load_groups(Path(kernel.config.path).parent / "export-groups.toml")
    if not groups:
        console.print("[dim]No groups declared (create export-groups.toml at repo root).[/dim]")
        return
    for g in groups:
        console.print()
        console.print(f"[bold]{g.get('name', g.get('id'))}[/bold]  — {g.get('description', '')}")
        console.print(f"  id: {g.get('id')}")
        for app_id in g.get("apps", []):
            manifest = kernel.apps.manifests.get(app_id)
            if not manifest:
                console.print(f"  [red]✗[/red] {app_id}  (not found)")
                continue
            exp = manifest.provides.get("export", {}) or {}
            ok = exp.get("enabled")
            mark = "[green]✓[/green]" if ok else "[yellow]⚠[/yellow]"
            suffix = "" if ok else "  [yellow](export disabled)[/yellow]"
            console.print(f"  {mark} {app_id}{suffix}")
    console.print()


@group_cmd.command("build")
def group_build(
    group_id: str = typer.Argument(..., help="Group id from export-groups.toml"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output path"),
    fmt: str = typer.Option("dir", "--format", "-f", help="Bundle format: dir | zip"),
    verify: bool = typer.Option(
        False, "--verify", help="After build, open chooser headless and assert no console errors"
    ),
):
    """Build an export group into a multi-app bundle."""
    if fmt not in ("dir", "zip"):
        console.print(f"[red]Unsupported format for groups: {fmt}[/red]")
        raise typer.Exit(2)

    kernel = _get_kernel()

    async def _run():
        from emptyos.sdk.exporter import GroupExporter, load_groups

        groups = load_groups(Path(kernel.config.path).parent / "export-groups.toml")
        match = next((g for g in groups if g.get("id") == group_id), None)
        if not match:
            console.print(
                f"[red]Group '{group_id}' not found. Available: {[g.get('id') for g in groups]}[/red]"
            )
            raise typer.Exit(1)

        out_path = Path(out) if out else Path.cwd() / f"{group_id}-export"
        exporter = GroupExporter(kernel, match, out_dir=out_path, fmt=fmt)
        result, warnings = await exporter.build()
        console.print(f"[green]✓[/green] Built group '{group_id}' → {result}")
        for w in warnings:
            console.print(f"  [yellow]⚠[/yellow] {w}")
        if verify and fmt == "dir":
            errs = await _verify_bundle(result, fmt)
            if errs:
                console.print("[red]Verification found issues:[/red]")
                for e in errs:
                    console.print(f"  • {e}")
                raise typer.Exit(3)
            console.print("[green]✓[/green] Verification clean")
        return result

    asyncio.run(_run())


@service_cmd.command("list")
def service_list():
    """List all registered services."""
    kernel = _get_kernel()
    entries = kernel.services.list()
    if not entries:
        console.print("[dim]No services registered (start kernel first).[/dim]")
        return
    table = Table()
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Status")
    for e in entries:
        table.add_row(e.name, type(e.instance).__name__, e.status.value)
    console.print(table)


@config_cmd.command("show")
def config_show():
    """Show current configuration."""
    kernel = _get_kernel()
    console.print_json(json.dumps(kernel.config._data, indent=2, default=str))


@event_cmd.command("log")
def event_log(
    event_type: str | None = typer.Argument(None, help="Filter by event type"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """Show recent events."""
    kernel = _get_kernel()

    async def _show():
        events = await kernel.events.history(event_type=event_type, limit=limit)
        if not events:
            console.print("[dim]No events found.[/dim]")
            return
        table = Table()
        table.add_column("Time")
        table.add_column("Type")
        table.add_column("Source")
        table.add_column("Data")
        for e in events:
            ts = e["timestamp"][:19].replace("T", " ")
            data_str = str(e["data"])[:60]
            table.add_row(ts, e["type"], e["source"], data_str)
        console.print(table)

    asyncio.run(_show())


# ── Skills Management ────────────────────────────────────


@app.command()
def skills(
    action: str = typer.Argument("list", help="list | install | sync | check"),
    category: str = typer.Argument("", help="Filter: vault, creative, life, tool, dev, or 'all'"),
):
    """Manage Claude Code skills — install, sync, check."""
    import shutil

    eos_dir = Path(__file__).parent.parent.parent
    bundled_dir = eos_dir / "skills"
    user_dir = Path.home() / ".claude" / "skills"

    if not bundled_dir.exists():
        console.print("[red]No bundled skills found at emptyos/skills/[/red]")
        raise typer.Exit(1)

    bundled = sorted(
        [d.name for d in bundled_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    )
    installed = (
        set(d.name for d in user_dir.iterdir() if d.is_dir()) if user_dir.exists() else set()
    )

    if category and category != "all":
        bundled = [b for b in bundled if b.startswith(category + "-")]

    if action == "list":
        table = Table(title="Skills")
        table.add_column("Skill")
        table.add_column("Status")
        table.add_column("Category")
        for name in bundled:
            cat = name.split("-")[0] if "-" in name else "other"
            status = "[green]installed[/green]" if name in installed else "[dim]available[/dim]"
            table.add_row(name, status, cat)
        console.print(table)
        console.print(
            f"\n  {len(bundled)} bundled, {sum(1 for b in bundled if b in installed)} installed"
        )

    elif action == "install":
        user_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for name in bundled:
            dest = user_dir / name
            if dest.exists():
                continue
            shutil.copytree(str(bundled_dir / name), str(dest))
            console.print(f"  [green]+[/green] {name}")
            added += 1
        if added:
            console.print(f"\n  Installed {added} skills")
        else:
            console.print("  All skills already installed")

    elif action == "sync":
        user_dir.mkdir(parents=True, exist_ok=True)
        updated = 0
        for name in bundled:
            src = bundled_dir / name / "SKILL.md"
            dest = user_dir / name / "SKILL.md"
            if not src.exists():
                continue
            if not dest.exists() or src.read_text(encoding="utf-8") != dest.read_text(
                encoding="utf-8"
            ):
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))
                console.print(f"  [yellow]~[/yellow] {name}")
                updated += 1
        if updated:
            console.print(f"\n  Updated {updated} skills")
        else:
            console.print("  All skills up to date")

    elif action == "check":
        missing = [b for b in bundled if b not in installed]
        outdated = []
        for name in bundled:
            src = bundled_dir / name / "SKILL.md"
            dest = user_dir / name / "SKILL.md"
            if src.exists() and dest.exists():
                if src.read_text(encoding="utf-8") != dest.read_text(encoding="utf-8"):
                    outdated.append(name)
        if missing:
            console.print(f"  [yellow]Missing ({len(missing)}):[/yellow] {', '.join(missing)}")
        if outdated:
            console.print(f"  [yellow]Outdated ({len(outdated)}):[/yellow] {', '.join(outdated)}")
        if not missing and not outdated:
            console.print("  [green]All skills installed and up to date[/green]")
