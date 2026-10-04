"""EmptyOS CLI — the `eos` command."""

from __future__ import annotations

import asyncio
import contextlib
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

# `eos bus` — Agent Context Bus. Kept kernel-free so the subcommand starts
# fast (no daemon boot, no VaultIndex) — appropriate for filesystem-only work.
from emptyos.cli.commands.bus import bus_app  # noqa: E402

app.add_typer(bus_app, name="bus")

# `eos autopilot` — autopilot grants + HMAC-chained audit chain. Kernel-free:
# pure filesystem reads of `data/autopilot/`, so fast and offline.
from emptyos.cli.commands.autopilot import autopilot_app  # noqa: E402

app.add_typer(autopilot_app, name="autopilot")

# `eos context` — Context Packing. Kernel-free: pure filesystem + in-memory
# reducers over `data/context/`, so fast and offline.
from emptyos.cli.commands.context import context_app  # noqa: E402

app.add_typer(context_app, name="context")

# `eos verb` — inspect + validate the unified verb registry. Kernel-lite:
# `list`/`describe` discover manifests (no full boot); `validate` prefers the
# running daemon's authoritative drift sweep. First command built to the
# agent-facing output convention in `.claude/rules/agent-cli.md`.
from emptyos.cli.commands.verb import verb_app  # noqa: E402

app.add_typer(verb_app, name="verb")

# `eos store` — manage apps/plugins/skills by proxying to the daemon's API.
from emptyos.cli.commands.store import store_app  # noqa: E402

app.add_typer(store_app, name="store")

# `eos loops` — inspect the feedback-loop registry (kernel-free, pure data).
# The single surface that NAMES every EmptyOS feedback loop as one concept.
from emptyos.cli.commands.loops import loops_app  # noqa: E402

app.add_typer(loops_app, name="loops")

# `eos send` — the ONLY general entry point to the `send` capability, and a CLI
# rather than an HTTP route on purpose: outbound to a third party is permanently
# human-gated (CLAUDE.md north star), and a typed command carries that intent in
# the act. An endpoint would be POST-able by any page, agent or [DO:] token.
from emptyos.cli.commands.send import send_cmd  # noqa: E402

app.command(name="send")(send_cmd)

from emptyos.cli._common import (
    bearer_headers,
    require_config,
)
from emptyos.cli._common import (
    daemon_url as _common_daemon_url,
)
from emptyos.cli.chat import chat_command
from emptyos.cli.commands.boot import boot_command
from emptyos.cli.commands.init import init_command

app.command("init")(init_command)
app.command("boot")(boot_command)
# `eos rooms` is the CLI client for apps/rooms (chat-shape work: multi-
# participant, [DO:]-token review gate, personas). `eos chat` is reserved
# for apps/agent's autonomous tool-use REPL (registered via that app's
# @cli_command("chat") in _wire_app_cli_commands below).
app.command(
    "rooms",
    help="Multi-participant chat REPL — review-gated [DO:] actions, personas. "
    "Use --code for the review-gated coding preset; autonomous loop → eos chat.",
)(chat_command)


def _find_config() -> str:
    """Backward-compatible delegate to the shared config resolver."""
    return require_config()


def _daemon_url() -> str | None:
    """Backward-compatible delegate to the shared daemon probe."""
    return _common_daemon_url()


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
    required_services = list((manifest.requires or {}).get("services", []) or [])
    daemon_required = bool(required_services)

    def make_handler(
        app_id: str,
        c_name: str,
        cfg_path: str,
        interactive: bool,
        needs_daemon: bool,
        services: list[str],
    ):
        def handler(
            args: list[str] = typer.Argument(None, help="Command arguments"),
        ):
            active_cfg = _find_config()
            if not interactive:
                # Try daemon first — single kernel, shared state
                daemon = _daemon_url()
                if daemon:
                    _run_via_daemon(
                        daemon,
                        app_id,
                        c_name,
                        args,
                        active_cfg,
                        daemon_required=needs_daemon,
                        required_services=services,
                    )
                    return
                if needs_daemon:
                    console.print(
                        f"[red]Cannot run '{c_name}' locally.[/red] "
                        f"It requires {_format_required_services(services)}."
                    )
                    console.print(
                        "[dim]Start the EmptyOS daemon from your own terminal, then retry.[/dim]"
                    )
                    return

            # Interactive, or no daemon — run in this process so stdin/stdout are live
            _run_locally(app_id, c_name, active_cfg, args)

        return handler

    handler = make_handler(
        manifest.id, cmd_name, config_path, is_interactive, daemon_required, required_services
    )
    help_value = cli_section.get("help", manifest.description)
    help_text = (
        help_value.get(cmd_name, manifest.description)
        if isinstance(help_value, dict)
        else help_value
    )
    app.command(cmd_name, help=help_text)(handler)


def _format_required_services(services: list[str] | None) -> str:
    """Human-facing label for manifest service dependencies."""
    if not services:
        return "daemon service(s)"
    labels = [f"service:{s}" for s in services]
    if len(labels) == 1:
        return labels[0]
    return "services: " + ", ".join(labels)


def _daemon_cli_headers(cfg_path: str) -> dict[str, str]:
    """Backward-compatible delegate for `/api/cli` request headers."""
    return bearer_headers(cfg_path, json_content=True)


def _run_via_daemon(
    daemon_url: str,
    app_id: str,
    cmd_name: str,
    args: list[str] | None,
    cfg_path: str,
    *,
    daemon_required: bool = False,
    required_services: list[str] | None = None,
):
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
            headers=_daemon_cli_headers(cfg_path),
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode())
        if result.get("output"):
            print(result["output"])
        if result.get("error"):
            console.print(f"[red]{result['error']}[/red]")
        # A daemon-side command failure (ok:false, or a bare error with no
        # output) must surface as a non-zero exit — mirror the local path
        # (_run_locally) so an agent/script branching on $? sees the failure
        # instead of a false success (.claude/rules/agent-cli.md).
        if result.get("ok") is False or (
            result.get("error") and not result.get("output")
        ):
            raise typer.Exit(1)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            console.print(
                f"[red]Daemon rejected '{cmd_name}' (HTTP {e.code}).[/red] "
                "Check that `network.auth_token` in emptyos.toml matches the running daemon."
            )
            raise typer.Exit(1) from e
        console.print(f"[red]Daemon CLI request failed (HTTP {e.code}).[/red]")
        if body:
            console.print(f"[dim]{body}[/dim]")
        raise typer.Exit(1) from e
    except (urllib.error.URLError, OSError):
        if daemon_required:
            console.print(
                f"[red]Cannot run '{cmd_name}' locally.[/red] "
                f"The daemon is unreachable and the command requires "
                f"{_format_required_services(required_services)}."
            )
            console.print(
                "[dim]Start the EmptyOS daemon from your own terminal, then retry.[/dim]"
            )
            return
        console.print("[dim]Daemon unreachable, running locally...[/dim]")
        _run_locally(app_id, cmd_name, cfg_path, args)


def _run_locally(app_id: str, cmd_name: str, cfg_path: str, args: list[str] | None):
    """Execute an app command by creating a local kernel."""
    from emptyos.kernel import Kernel

    async def _run():
        k = Kernel(cfg_path)
        k.apps.discover()
        instance = await k.apps.load(app_id)

        from emptyos.sdk.cli_args import (
            bind_cli_kwargs,
            cli_command_label,
            cli_usage,
            missing_required_args,
            render_cli_return,
            resolve_cli_method,
        )

        # Subcommand-aware resolution, matching the daemon /api/cli path.
        method, cmd_args = resolve_cli_method(
            instance.get_cli_methods(), cmd_name, args
        )
        if not method:
            console.print(f"[red]Command '{cmd_name}' not found in app '{app_id}'[/red]")
            raise typer.Exit(1)

        kwargs = bind_cli_kwargs(method, cmd_args)

        # A short/bare invocation missing a required arg gets a usage hint
        # instead of a raw `TypeError: ... missing N required positional
        # argument` reaching the terminal (the calculator-CLI leak).
        missing = missing_required_args(method, kwargs)
        if missing:
            label = cli_command_label(instance.get_cli_methods(), method, cmd_name)
            console.print(f"[red]Missing required argument(s): {', '.join(missing)}[/red]")
            console.print(f"[dim]{cli_usage(method, label)}[/dim]")
            raise typer.Exit(1)

        # Wrap the call so a raising command prints a clean error + exits
        # non-zero, mirroring the daemon /api/cli path (which returns
        # {ok:false, error}). Without this the local-fallback path dumps a
        # raw traceback. typer.Exit (e.g. command-not-found above) passes through.
        try:
            result = method(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except typer.Exit:
            raise
        except Exception as e:  # noqa: BLE001 — surface any command failure cleanly
            console.print(f"[red]{cmd_name} failed:[/red] {e}")
            raise typer.Exit(1) from e

        # A command that returns a value instead of printing still shows it.
        # (The method's own prints already streamed to the terminal live.)
        rendered = render_cli_return(result)
        if rendered is not None:
            print(rendered)

    asyncio.run(_run())


# Wire app CLI commands at import time (runs once when `eos` is invoked).
# Discovery emits syslog warnings (id collisions, etc.) to stdout; route them to
# stderr so machine-readable commands (`--json`, per .claude/rules/agent-cli.md)
# keep stdout pure. Human output printed later by each command is unaffected.
with contextlib.redirect_stdout(sys.stderr):
    _wire_app_cli_commands()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Run this eos command against the first ready sandbox-pool member.",
    ),
    sandbox_port: int | None = typer.Option(
        None,
        "--sandbox-port",
        help="Run this eos command against a specific sandbox-pool port.",
    ),
):
    """Show status overview when called without subcommand."""
    if sandbox or sandbox_port is not None or os.environ.get("EOS_SANDBOX_DEFAULT"):
        from emptyos.cli.sandbox_target import activate_sandbox_config

        main_config = _find_config()
        try:
            os.environ["EOS_CONFIG"] = activate_sandbox_config(main_config, sandbox_port)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e

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
    # Before the kernel exists: a daemon started with no console at all (the
    # watchdog's DETACHED respawn, any GUI parent) must never let a child open
    # one — that is the console storm behind three hard freezes and a fourth
    # storm caught live (2026-08-01, 08-15, 09-06). Kernel boot itself spawns
    # (sandbox-pool, dogfood-demo, comfyui), so this has to be the first thing
    # `start` does. No-op with a console, hidden or not. See emptyos/headless.py.
    from emptyos.headless import install_headless_subprocess_guard

    install_headless_subprocess_guard()
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
                    # Persist (syslog also prints, so the live-boot signal stays).
                    # A skipped app would otherwise vanish with the console line.
                    kernel.apps.log_load_failure(
                        app_id, e, source="app_loader", phase="boot load"
                    )

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
            if _mode == "public" and not kernel.config.trust_web:
                console.print(
                    f"[bold red]Refusing to start.[/bold red] "
                    f"network.mode = 'public' but [trust] web is unset, so the "
                    f"daemon cannot tell whether the browser user is the operator."
                )
                console.print(
                    "  Set it in emptyos.toml:\n"
                    "  [cyan]web = \"operator\"[/cyan]  — you run this machine and "
                    "use it yourself (self-host);\n"
                    "  [cyan]web = \"user\"[/cyan]      — visitors use it but don't "
                    "operate the host (demo / hosted)."
                )
                console.print("  Section: [cyan][trust][/cyan]. See docs/AUTH.md § Operator vs user.")
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
            # Windows peer-reset teardown guard. A dying client makes the
            # Proactor transport's shutdown() raise mid-`finally`, which both
            # prints an unactionable traceback AND skips sock.close() +
            # server._detach(). See emptyos/proactor_guard.py.
            from emptyos.proactor_guard import install_proactor_reset_guard

            install_proactor_reset_guard()

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
        from eos_apps.integrity.app import IntegrityApp

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
    out: str | None = typer.Option(None, "--out", "-o", help="Output path (dir, zip, .html, or extension dir)"),
    fmt: str = typer.Option(
        "dir", "--format", "-f", help="Bundle format: dir | zip | single-html | extension"
    ),
    verify: bool = typer.Option(
        False, "--verify", help="After build, open bundle headless and assert no console errors"
    ),
    csp_safe: bool = typer.Option(
        False,
        "--csp-safe",
        help="Target CSP-locked hosts (MV3 extensions): external bootstrap + inline-handler bridge",
    ),
):
    """Export an app to a standalone HTML+JS bundle.

    The app must declare ``[provides.export].enabled = true`` in its manifest.
    """
    if fmt not in ("dir", "zip", "single-html", "extension"):
        console.print(f"[red]Unknown format: {fmt}[/red] — use dir | zip | single-html | extension")
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
        from emptyos.sdk.exporter import AppExporter, build_extension_shell

        instance = await kernel.apps.load(app_id)
        if fmt == "extension":
            # MV3 extension = csp-safe dir export wrapped in the generated
            # shell (manifest.json + background.js + icons). Load-unpacked-able
            # as-is; zip it for distribution (scripts/build_standalone_release.py
            # does that plus the release README).
            shell = Path(out) if out else Path.cwd() / f"{app_id}-extension"
            exporter = AppExporter(instance, out_dir=shell / "app", fmt="dir", csp_safe=True)
            await exporter.build()
            build_extension_shell(
                shell,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
            )
            result = shell
        else:
            out_path = Path(out) if out else Path.cwd() / f"{app_id}-export"
            exporter = AppExporter(instance, out_dir=out_path, fmt=fmt, csp_safe=csp_safe)  # type: ignore[arg-type]
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
    # Extension: static checks only — a real MV3 verification needs
    # --load-extension in a persistent context (the release script's
    # verification checklist covers that; see standalone-distribution.md).
    if fmt == "extension":
        errs = []
        for rel in ["manifest.json", "background.js", "app/index.html",
                    "app/_assets/eos-csp-bridge.js", "app/_data/bootstrap.js"]:
            if not (path / rel).exists():
                errs.append(f"missing {rel}")
        return errs

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

    # dir and single-html are playwright-verifiable via file:// URLs.
    if fmt == "zip":
        return []
    index = path if fmt == "single-html" else path / "index.html"
    if not index.exists():
        return ["no index.html"] if fmt == "dir" else [f"missing {path.name}"]

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
    csp_safe: bool = typer.Option(
        False,
        "--csp-safe",
        help="Target CSP-locked hosts (MV3 extensions): external bootstrap/auto-rpc + inline-handler bridge",
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
        exporter = GroupExporter(kernel, match, out_dir=out_path, fmt=fmt, csp_safe=csp_safe)
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
    packed: bool = typer.Option(
        False,
        "--packed",
        help="Compress the output via Context Packing — a structural summary "
        "(grouped by event type, anomalies kept) plus a ctx_ ref for full "
        "recovery. Use when piping a long log to a coding agent.",
    ),
):
    """Show recent events. With --packed, emit a compressed summary + a ctx_ ref."""
    kernel = _get_kernel()

    async def _show():
        events = await kernel.events.history(event_type=event_type, limit=limit)
        if not events:
            console.print("[dim]No events found.[/dim]")
            return

        if packed:
            # Serialize to JSONL (the shape an external agent wants anyway), then
            # compress via the Context Packing domain. Fail-open: on any error the
            # packer returns the original text, so the agent still gets the data.
            from emptyos.context import pack_text

            jsonl = "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in events)
            store_root = kernel.config.data_dir / "context"
            result = pack_text(jsonl, "jsonl", source="eos event log", store_root=store_root)
            s = result.stats
            console.print(
                f"[dim]{s.get('original_est_tokens', 0)} -> "
                f"{s.get('packed_est_tokens', 0)} est tok "
                f"({s.get('reduction_pct', 0)}% reduction)[/dim]"
            )
            for ref_id in result.refs:
                console.print(f"[dim]full log: eos context show {ref_id}[/dim]")
            # markup=False/highlight=False: the packed summary uses literal
            # "[ctx:...]" / "[jsonl summary: ...]" header lines that Rich would
            # otherwise eat as style markup.
            console.print(result.text, markup=False, highlight=False)
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
