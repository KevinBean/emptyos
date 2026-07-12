"""EmptyOS desktop daily driver — what you double-click to open EmptyOS.

Ensures the :9000 daemon is reachable, then opens a full-size chromeless
``--app=`` window at the dashboard. The window is detached, so this launcher
spawns it and exits immediately (the daemon is a separate, already-running
process).

SECURITY: the auth_token is deliberately NOT placed in the window URL — that
would leak the long-lived credential into the browser process argv and the
profile's history. The isolated profile (data/launcher-profile, shared with
the command palette) persists the session cookie after a one-time login, so
you log in once. To restore true zero-login without the leak, mint a
short-TTL single-use exchange token from the daemon (future).

Usage:
    pythonw scripts/eos_desktop.py          # open the window (normal use)
    python  scripts/eos_desktop.py --dry-run  # print resolved browser + args, spawn nothing
    python  scripts/eos_desktop.py --start    # if the daemon is down, run restart.bat first

Reads ``emptyos.toml`` (repo root, or ``$EOS_CONFIG``) for the [network] port.
Connects to 127.0.0.1 even when the daemon binds 0.0.0.0
(per .claude/rules/environment.md — local Python must use 127.0.0.1).
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Make ``emptyos.sdk`` importable when run as a loose script from anywhere.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def config_path() -> Path:
    """The emptyos.toml the daemon is running with."""
    env = os.environ.get("EOS_CONFIG")
    if env:
        return Path(env)
    return REPO_ROOT / "emptyos.toml"


def load_network_config(path: Path | None = None) -> dict:
    """Return {'port': int} from a toml file's [network] table.

    Takes a path, returns a dict; default applied for a fresh/local config
    (port 9000). Tolerates a missing file (returns defaults).
    """
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        data = {}
    return network_config_from_dict(data)


def network_config_from_dict(data: dict) -> dict:
    """Pure: extract {'port'} from a parsed toml dict. Unit-testable."""
    net = data.get("network", {}) if isinstance(data, dict) else {}
    port = net.get("port", 9000)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 9000
    return {"port": port}


def build_window_url(port: int, path: str = "/") -> str:
    """Pure: the dashboard URL for the window.

    No auth token is placed in the URL — that would leak the long-lived
    auth_token into the browser's argv + history. Auth is handled by a
    one-time login persisted in the isolated profile's cookie (see the
    SECURITY note in the module docstring).
    """
    return f"http://127.0.0.1:{int(port)}{path}"


def run_restart_bat() -> bool:
    """Launch restart.bat (it self-elevates). Returns True if it was spawned."""
    import subprocess
    bat = REPO_ROOT / "restart.bat"
    if not bat.exists():
        return False
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(REPO_ROOT), creationflags=flags)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Open the EmptyOS desktop window.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the resolved browser + window args; spawn nothing.")
    ap.add_argument("--start", action="store_true",
                    help="If the daemon is down, run restart.bat (UAC) and wait.")
    ap.add_argument("--code", action="store_true",
                    help="Open the /code IDE window (file tree + diff + terminal + chat) "
                         "instead of the dashboard.")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args(argv)

    from emptyos.sdk.browser_window import (
        build_app_window_args,
        find_chromium,
        open_app_window,
    )
    from emptyos.sdk.daemon_launcher import wait_health

    cfg = load_network_config()
    base = f"http://127.0.0.1:{cfg['port']}"
    url = build_window_url(cfg["port"], path="/code/" if args.code else "/")
    profile_dir = REPO_ROOT / "data" / "launcher-profile"
    browser = find_chromium()

    if args.dry_run:
        up = wait_health(base, timeout=1.5)
        print(f"[eos-desktop] config:  {config_path()}")
        print(f"[eos-desktop] daemon:  {'UP' if up else 'DOWN'} at {base}")
        print(f"[eos-desktop] browser: {browser[0] if browser else 'NONE FOUND'}")
        if browser:
            window_args = build_app_window_args(
                browser[0], url, width=args.width, height=args.height,
                profile_dir=profile_dir,
            )
            print("[eos-desktop] args:   " + " ".join(window_args))
        return 0 if browser else 1

    if browser is None:
        print("[eos-desktop] No Chromium-family browser found (Chrome / Edge / Brave).")
        return 1

    if not wait_health(base, timeout=2.0):
        if args.start:
            print("[eos-desktop] daemon down — running restart.bat (may prompt for UAC)…")
            if not run_restart_bat():
                print("[eos-desktop] restart.bat not found; start the daemon manually.")
                return 1
            if not wait_health(base, timeout=60.0):
                print("[eos-desktop] daemon did not come up within 60s.")
                return 1
        else:
            print(f"[eos-desktop] daemon not running at {base} — start it from your "
                  f"terminal (restart.bat), or re-run with --start.")
            return 1

    open_app_window(url, browser=browser, width=args.width, height=args.height,
                    profile_dir=profile_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
