"""eos init — interactive setup for new EmptyOS installation."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

console = Console()


def _register_config_pointer(config_path: Path) -> None:
    """Write ~/.config/emptyos/config-path.txt so `eos` finds the config from any directory."""
    pointer_dir = Path.home() / ".config" / "emptyos"
    try:
        pointer_dir.mkdir(parents=True, exist_ok=True)
        pointer = pointer_dir / "config-path.txt"
        pointer.write_text(str(config_path.resolve()), encoding="utf-8")
        console.print(
            f"  [dim]Registered config at {pointer} — `eos` now works from any directory.[/dim]"
        )
    except Exception as e:
        console.print(f"  [yellow]Could not register global config pointer: {e}[/yellow]")
        console.print(
            f"  [dim]Set EOS_CONFIG={config_path.resolve()} to use `eos` outside this directory.[/dim]"
        )


def _check_eos_on_path() -> None:
    """Warn if `eos` isn't on PATH and show how to add it."""
    import platform
    import shutil
    import sys

    if shutil.which("eos") or shutil.which("eos.exe"):
        return

    # Locate the pip-installed scripts directory that contains eos/eos.exe.
    candidates: list[Path] = []
    exe = "eos.exe" if platform.system() == "Windows" else "eos"
    try:
        import sysconfig

        for scheme in (f"{sysconfig.get_default_scheme()}_user", sysconfig.get_default_scheme()):
            try:
                p = sysconfig.get_path("scripts", scheme)
                if p:
                    candidates.append(Path(p))
            except Exception:
                pass
    except Exception:
        pass
    candidates.append(
        Path(sys.executable).parent / ("Scripts" if platform.system() == "Windows" else "bin")
    )
    if platform.system() == "Windows":
        candidates.append(
            Path.home()
            / "AppData"
            / "Roaming"
            / "Python"
            / f"Python{sys.version_info.major}{sys.version_info.minor}"
            / "Scripts"
        )

    scripts_dir = next((c for c in candidates if (c / exe).exists()), None)

    console.print()
    console.print("[yellow]Warning:[/yellow] `eos` is not on PATH.")
    if scripts_dir and platform.system() == "Windows":
        console.print(f"  eos.exe is at: [cyan]{scripts_dir}[/cyan]")
        console.print("  Add permanently (PowerShell, then open a new window):")
        console.print(
            f"    [cyan][Environment]::SetEnvironmentVariable("
            f'"Path", $env:Path + ";{scripts_dir}", "User")[/cyan]'
        )
    elif scripts_dir:
        console.print(f"  eos is at: [cyan]{scripts_dir}[/cyan]")
        console.print(
            f'  Add to PATH in your shell rc: [cyan]export PATH="{scripts_dir}:$PATH"[/cyan]'
        )
    else:
        console.print("  Could not locate the scripts directory. Re-open your shell and try again.")
    console.print("  Alternative: run [bold]python -m emptyos[/bold] — works without PATH changes.")


def init_command(
    directory: str = typer.Argument(".", help="Directory to initialize EmptyOS in"),
):
    """Set up EmptyOS in a directory. Creates emptyos.toml from template."""
    target = Path(directory).resolve()
    config_path = target / "emptyos.toml"

    if config_path.exists():
        console.print(f"[yellow]emptyos.toml already exists at {config_path}[/yellow]")
        raise typer.Exit(0)

    console.print("[bold]Welcome to EmptyOS[/bold]")
    console.print()

    name = typer.prompt("Give your OS a name", default="My EmptyOS")

    notes_path = typer.prompt(
        "Path to your notes directory (markdown vault — leave empty to skip)",
        default="",
    )

    port = typer.prompt("Web dashboard port", default="9000")
    timezone = typer.prompt("Timezone", default="UTC")

    # Deployment mode
    console.print()
    console.print("[bold]Deployment Mode[/bold]")
    console.print("  [cyan]1[/cyan]  local   — only this machine (default, safest)")
    console.print(
        "  [cyan]2[/cyan]  private — access from your own devices via Tailscale / LAN / VPN"
    )
    console.print(
        "  [cyan]3[/cyan]  public  — internet-exposed (VPS / Docker) — requires auth token"
    )
    console.print()
    _mode_choice = typer.prompt("Choose mode [1/2/3]", default="1")
    _mode_map = {"1": "local", "2": "private", "3": "public"}
    network_mode = _mode_map.get(_mode_choice.strip(), "local")
    auth_token = ""
    if network_mode == "public":
        import secrets

        auth_token = secrets.token_urlsafe(32)
        console.print(f"[green]Generated auth token:[/green] {auth_token}")
        console.print("  Save this — you'll need it to access the dashboard from a browser or API.")

    # LLM provider setup
    console.print()
    console.print("[bold]LLM Providers[/bold] (configure any combination, or skip all)")
    console.print("  EmptyOS works with any OpenAI-compatible API, Ollama, Claude, etc.")
    console.print()

    llm_sections = ""
    default_provider = ""

    if typer.confirm("Configure an Ollama provider?", default=False):
        ollama_host = typer.prompt("  Ollama host", default="http://localhost:11434")
        ollama_model = typer.prompt("  Default model", default="llama3.1")
        llm_sections += f"""
[llm.providers.ollama]
host = "{ollama_host}"
model = "{ollama_model}"
"""
        if not default_provider:
            default_provider = "ollama"

    if typer.confirm("Configure an OpenAI-compatible provider?", default=False):
        openai_model = typer.prompt("  Model name", default="gpt-5-mini")
        llm_sections += f"""
[llm.providers.openai]
model = "{openai_model}"
# Set OPENAI_API_KEY env var
"""
        if not default_provider:
            default_provider = "openai"

    if typer.confirm("Configure a Claude provider?", default=False):
        claude_method = typer.prompt("  Method (cli or api)", default="cli")
        llm_sections += f"""
[llm.providers.claude]
method = "{claude_method}"
# For api method, set ANTHROPIC_API_KEY env var
"""
        if not default_provider:
            default_provider = "claude"

    # External services setup
    console.print()
    console.print("[bold]External Services[/bold] (GPU, voice, etc.)")
    console.print("  EmptyOS plugins can auto-start their services on boot.")
    console.print()

    plugin_sections = ""

    if typer.confirm("Do you have ComfyUI installed? (GPU image/video generation)", default=False):
        comfyui_launcher = typer.prompt("  Path to ComfyUI launcher (.bat)")
        comfyui_host = typer.prompt("  ComfyUI host", default="http://localhost:8188")
        comfyui_launcher_norm = comfyui_launcher.replace("\\", "/")
        plugin_sections += f"""
[plugins.comfyui]
host = "{comfyui_host}"
launcher = "{comfyui_launcher_norm}"
autostart = true
"""

    if typer.confirm("Do you have Applio installed? (AI voice conversion)", default=False):
        applio_launcher = typer.prompt("  Path to Applio launcher (.bat)")
        applio_host = typer.prompt("  Applio host", default="http://localhost:6969")
        applio_launcher_norm = applio_launcher.replace("\\", "/")
        plugin_sections += f"""
[plugins.applio]
host = "{applio_host}"
launcher = "{applio_launcher_norm}"
autostart = true
"""

    if typer.confirm("Do you have a Voice API server? (TTS + STT)", default=False):
        voice_host = typer.prompt("  Voice API host", default="http://localhost:8601")
        plugin_sections += f"""
[plugins.voice-api]
host = "{voice_host}"
"""

    startup_section = ""
    if typer.confirm(
        "Launch any other programs on boot? (e.g. note editor, browser)", default=False
    ):
        startup_entries = {}
        while True:
            prog_name = typer.prompt("  Program name (e.g. 'notes')", default="")
            if not prog_name:
                break
            prog_path = typer.prompt(f"  Path to {prog_name} executable")
            startup_entries[prog_name] = prog_path.replace("\\", "/")
            if not typer.confirm("  Add another?", default=False):
                break
        if startup_entries:
            startup_section = "\n[startup]\n"
            for k, v in startup_entries.items():
                startup_section += f'{k} = "{v}"\n'

    config = f'''# EmptyOS Configuration

[os]
name = "{name}"
data_dir = "./data"
log_level = "INFO"

[notes]
path = "{notes_path}"
watch = {str(bool(notes_path)).lower()}

[network]
mode = "{network_mode}"             # local | private | public
port = {port}
auth_token = "{auth_token}"
# host override (usually not needed — derived from mode):
#   local   → 127.0.0.1   private → 0.0.0.0   public → 0.0.0.0

[demo]
enabled = false              # set true for demo UX (banner, GPU off, BYOK)

[cloud]
consent = "ask"              # "ask" (default) | "always" | "never" — see docs/DESIGN.md

[llm]
default_provider = "{default_provider}"
{llm_sections}
[services.gpu]
comfyui = ""
tts = ""

[scheduler]
enabled = true
timezone = "{timezone}"

[plugins]
path = "./plugins"
{plugin_sections}
[apps]
path = "./apps"
autostart = []
{startup_section}'''

    config_path.write_text(config)
    console.print()
    console.print(f"[green]Created {config_path}[/green]")

    # Register config path so `eos` works from any directory.
    _register_config_pointer(config_path)

    # Warn if `eos` isn't on PATH (common on Windows with per-user pip installs).
    _check_eos_on_path()

    console.print()
    console.print("Next steps:")
    console.print("  1. Edit emptyos.toml to fine-tune settings")
    console.print("  2. Run [bold]eos[/bold] to see status")
    console.print("  3. Run [bold]eos start[/bold] to boot the system")

    # Create data dirs
    data_dir = target / "data"
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "state").mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)
    (target / "apps").mkdir(parents=True, exist_ok=True)

    # Personal defaults — seeds settings on first boot (git-ignored via data/)
    console.print()
    if typer.confirm("Set up personal defaults? (name, location, countdowns)", default=True):
        defaults = {}

        user_name = typer.prompt("  Your name", default="")
        if user_name:
            defaults["user.name"] = user_name

        lat = typer.prompt("  Latitude (for weather, 0 to skip)", default="0")
        lng = typer.prompt("  Longitude", default="0")
        tz = typer.prompt("  Timezone", default=timezone)
        if float(lat) != 0 or float(lng) != 0:
            defaults["location.latitude"] = float(lat)
            defaults["location.longitude"] = float(lng)
            defaults["location.timezone"] = tz

        console.print()
        console.print(
            "  [dim]Countdowns track important dates (visa expiry, deadlines, etc.)[/dim]"
        )
        countdowns = []
        while typer.confirm("  Add a countdown?", default=bool(not countdowns)):
            label = typer.prompt("    Label (e.g., 'Visa expires')")
            cd_date = typer.prompt("    Date (YYYY-MM-DD)")
            direction = typer.prompt(
                "    Direction (down=days left, up=days elapsed)", default="down"
            )
            countdowns.append({"label": label, "date": cd_date, "direction": direction})
        if countdowns:
            defaults["countdown.items"] = countdowns

        if defaults:
            defaults_path = data_dir / "personal-defaults.json"
            defaults_path.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
            console.print(f"  [green]Saved personal defaults to {defaults_path}[/green]")

    # Boot sequence setup
    import platform

    if platform.system() == "Windows":
        console.print()
        if typer.confirm("Install EmptyOS to start on login?", default=True):
            import tomllib

            from emptyos.cli.commands.boot import generate_boot_vbs, install_boot

            with open(config_path, "rb") as f:
                data = tomllib.load(f)
            boot_vbs = generate_boot_vbs(data, str(target))
            boot_path = target / "boot.vbs"
            boot_path.write_text(boot_vbs, encoding="utf-8")
            console.print(f"[green]Generated {boot_path}[/green]")
            install_boot(str(target))
            console.print("[green]EmptyOS will start automatically on login.[/green]")
