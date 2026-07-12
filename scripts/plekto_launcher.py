"""Plekto desktop launcher — what users actually double-click.

Wraps `emptyos start` so the .app / .exe acts like a native application:

1. On first run, lay down a default emptyos.toml in a per-user data dir
   if one doesn't exist (Plekto isn't useful without a vault path).
2. Start the EmptyOS kernel + web server in the foreground.
3. Open the default browser to the dashboard.
4. On Ctrl-C / dock-quit, shut down cleanly.

PyInstaller treats this as the entrypoint (see scripts/plekto.spec).
"""
from __future__ import annotations

import os
import platform
import socket
import sys
import threading
import webbrowser
from pathlib import Path


# When frozen by PyInstaller, sys.frozen is True and resources live under
# sys._MEIPASS. When running from source for development, fall back to the
# repo root.
def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _user_data_dir(app_name: str = "Plekto") -> Path:
    """Per-user app data directory. Mirrors platformdirs convention but
    avoids the runtime dep — we know the three OSes we ship for."""
    home = Path.home()
    sys_name = platform.system()
    if sys_name == "Darwin":
        return home / "Library" / "Application Support" / app_name
    elif sys_name == "Windows":
        return Path(os.environ.get("APPDATA", home)) / app_name
    else:  # Linux + everything else
        return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / app_name


def _default_vault_dir(app_name: str = "Plekto") -> Path:
    """Where the default vault lives on first run. Documents folder is
    discoverable for users who later want to back it up."""
    home = Path.home()
    sys_name = platform.system()
    if sys_name == "Darwin":
        return home / "Documents" / f"{app_name}Vault"
    elif sys_name == "Windows":
        return home / "Documents" / f"{app_name}Vault"
    else:
        return home / "Documents" / f"{app_name}Vault"


def _free_port(start: int = 9000, count: int = 20) -> int:
    """Find the first free localhost port from start..start+count.

    Scans *upward from :9000* on purpose — a product wants the standard EmptyOS
    port when it's free. This is the opposite of
    ``check_snapshot_boot.pick_free_port``, which binds :0 for an *ephemeral*
    port precisely to stay off :9000-:9009 (the user's daemon / dogfood sidecar
    / sandbox-pool). Don't "unify" the two: the strategies are contradictory by
    design, so a shared helper would be wrong for one caller.
    """
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start  # fall back to the default; will fail loudly if truly blocked


def _startup_url(port: int) -> str:
    """First user-facing surface for the Plekto desktop app."""
    return f"http://127.0.0.1:{port}/code/"


def first_run_setup(data_dir: Path, vault_dir: Path, port: int) -> Path:
    """Lay down a minimal emptyos.toml + create the vault dir if missing.
    Returns the absolute path to the config file used."""
    data_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)

    config_path = data_dir / "emptyos.toml"
    if config_path.exists():
        return config_path

    # Sensible defaults for a single-user local install. Network mode 'local'
    # = no auth required, only loopback. User can flip to 'private' in
    # /settings when they want Tailscale access etc.
    config_path.write_text(
        f"""# Plekto config — generated on first run.
# Edit via the /settings page, or hand-edit here.

[notes]
path = {vault_dir.as_posix()!r}

[network]
mode = "local"
host = "127.0.0.1"
port = {port}

[capabilities.think]
providers = ["human"]   # add "claude-cli" or "ollama" here once configured

[capabilities.draw]
providers = ["human"]
""",
        encoding="utf-8",
    )
    return config_path


def main() -> int:
    app_name = "Plekto"
    data_dir = _user_data_dir(app_name)
    vault_dir = _default_vault_dir(app_name)
    port = _free_port()

    config_path = first_run_setup(data_dir, vault_dir, port)
    print(f"[plekto] config: {config_path}")
    print(f"[plekto] vault:  {vault_dir}")
    print(f"[plekto] port:   {port}")

    # Tell the kernel where to load config from.
    os.environ["EOS_CONFIG"] = str(config_path)
    os.environ["EOS_DAEMON"] = "1"

    # Imports deferred until config env is set so the kernel boots cleanly.
    # (The kernel reads EOS_CONFIG during its boot sequence.)
    bundle = _bundle_root()
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))

    # Open the app window shortly after the server is reachable. Prefer a
    # chromeless --app window (shared with the from-source daily driver via
    # emptyos.sdk.browser_window); fall back to a browser tab if no Chromium
    # browser is present.
    def _open_browser_when_ready():
        from emptyos.sdk.daemon_launcher import wait_health
        if wait_health(f"http://127.0.0.1:{port}", timeout=20.0):
            url = _startup_url(port)
            try:
                from emptyos.sdk.browser_window import open_app_window
                open_app_window(url, width=1440, height=900,
                                profile_dir=data_dir / "window-profile")
            except Exception:
                webbrowser.open(url)
        else:
            print(f"[plekto] server didn't come up in 20s — open http://127.0.0.1:{port}/ manually")

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    # Use the same start path as `eos start` — keeps a single source of truth.
    from emptyos.cli.main import start as _eos_start
    try:
        _eos_start(no_web=False)
    except KeyboardInterrupt:
        print("\n[plekto] shutting down")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
