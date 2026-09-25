"""EmptyOS desktop daily driver — what you double-click to open EmptyOS.

Ensures the :9000 daemon is reachable, then opens a full-size chromeless
``--app=`` window at the dashboard. That window is detached, so this launcher
spawns it and exits immediately (the daemon is a separate, already-running
process). The ``--webview`` shell below instead *is* the window, and this
process lives as long as it does.

SECURITY: the auth_token is deliberately NOT placed in the window URL — that
would leak the long-lived credential into the browser process argv and the
profile's history. An isolated profile persists the session cookie after a
one-time login, so you log in once: ``data/launcher-profile`` for the browser
window (shared with the command palette), ``data/desktop-shell/webview`` for the
native one. The native shell now does the zero-login properly: it trades the
token it already holds for a 60-second single-use code
(``/api/auth/shell-exchange``, bearer-only) and opens the window on the redeem
URL, so the webview arrives signed in and the token still never appears in a
URL. Dark behind ``[network] feature.shell-exchange.enabled``; without it the
one-time login above is what happens.

Usage:
    pythonw scripts/eos_desktop.py          # open the window (normal use)
    python  scripts/eos_desktop.py --dry-run  # print resolved browser + args, spawn nothing
    python  scripts/eos_desktop.py --start    # if the daemon is down, run restart.bat first
    pythonw scripts/eos_desktop.py --webview  # native window (pywebview → WebView2) instead

Two window shells. The default is still a Chromium ``--app`` window. ``--webview``
(or ``[desktop] shell = "webview"`` in emptyos.toml; ``--browser`` overrides it)
opens the native shell in ``products/_shared/shell.py``: one instance per port,
a login that persists, an offline splash with a Start button, and a tray icon:
closing the window hides it there, and the tray's Quit ends the app (without a
tray — pystray missing — closing quits). It runs from its own venv —
``%LOCALAPPDATA%/eos/envs/desktop-3.13`` (or ``$EOS_DESKTOP_PYTHON``) — and this
script re-execs itself there, under ``pythonw.exe``, when ``webview`` isn't
importable here. One-time setup (Git Bash; in PowerShell use ``$env:LOCALAPPDATA``):

    uv venv --python 3.13 "$LOCALAPPDATA/eos/envs/desktop-3.13"
    uv pip install --python "$LOCALAPPDATA/eos/envs/desktop-3.13/Scripts/python.exe" pywebview==6.2.1 pystray Pillow

The version is pinned because the shell's bridge guard patches pywebview
internals; it refuses to open on an untested release (``shell.TESTED_PYWEBVIEW``).

The splash's Start button runs ``restart.bat``, which kills every ``python.exe``;
it is only offered when this process is not one (``pythonw.exe`` is spared).

Links on EmptyOS pages that leave the web (``mailto:``, a notes app's "open
external") are handed to the OS only for schemes in ``[desktop]
external_schemes`` (default ``["mailto"]``); others are refused with a notice.

**Quick entry** is off until asked for::

    [desktop]
    quick_entry.enabled = true       # off by default
    quick_shortcut = "ctrl+space"    # optional; this is the default

It preloads a second frameless, always-on-top window on ``/portal/?quick=1`` and
shows it on that global hotkey — preloaded rather than respawned, which is the
whole difference from the ``command-launcher`` plugin it replaces (that plugin
stands down while this shell holds the shortcut). Esc hides it, Ctrl+Enter moves
the conversation to the main window. Two limits are the OS's, not ours: a
combination another program already holds cannot be registered (the shell says
so and carries on without it), and a non-elevated process receives no hotkey
while an elevated window has focus.

Reads ``emptyos.toml`` (repo root, or ``$EOS_CONFIG``) for the [network] port
and host, and the ``[desktop]`` table (``shell``, ``external_schemes``,
``quick_entry.enabled``, ``quick_shortcut``).
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
# Make ``emptyos.sdk`` importable when run as a loose script from anywhere, and
# ``_shared`` (the desktop-shell modules under products/).
for _p in (REPO_ROOT, REPO_ROOT / "products"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

#: Set on the re-exec'd child, so a desktop venv that is missing ``webview`` too
#: reports the problem instead of re-exec'ing itself forever.
REEXEC_ENV = "EOS_DESKTOP_REEXEC"

#: Quick entry's shortcut when ``[desktop] quick_entry.enabled`` is on and no
#: ``quick_shortcut`` is given. The same combination ``plugins/global-hotkey``
#: defaults to, on purpose: quick entry *replaces* that launcher rather than
#: adding a second shortcut, and turning it on should move the key the user
#: already presses, not teach them another one.
DEFAULT_QUICK_SHORTCUT = "ctrl+space"


def config_path() -> Path:
    """The emptyos.toml the daemon is running with."""
    env = os.environ.get("EOS_CONFIG")
    if env:
        return Path(env)
    return REPO_ROOT / "emptyos.toml"


def load_config_dict(path: Path | None = None) -> dict:
    """The parsed toml, or ``{}`` when it is missing or unparseable."""
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return {}


def desktop_config_from_dict(data: dict) -> dict:
    """Pure: ``{'shell', 'quick_shortcut'}`` from ``[desktop]``.

    ``browser`` (the Chromium ``--app`` window) stays the default until the
    native shell has earned it; an unknown value falls back rather than failing
    to open any window at all.

    ``quick_shortcut`` is ``""`` unless ``[desktop] quick_entry.enabled`` is set —
    dark by default like every new feature, and "off" is expressed as an empty
    shortcut so a single value answers both "is it on" and "which keys".
    """
    desk = data.get("desktop", {}) if isinstance(data, dict) else {}
    if not isinstance(desk, dict):
        desk = {}
    shell = desk.get("shell", "browser")
    return {"shell": shell if shell in ("browser", "webview") else "browser",
            "quick_shortcut": _quick_shortcut_from(desk)}


def _quick_shortcut_from(desk: dict) -> str:
    """The configured shortcut, or ``""`` when quick entry is off.

    Reads ``quick_entry.enabled`` the way TOML actually delivers it. A dotted
    key is a **nested table** — ``quick_entry.enabled = true`` parses to
    ``{"quick_entry": {"enabled": True}}``, never to a flat key with a dot in
    its name — so a `desk.get("quick_entry.enabled")` reads `None` from the one
    spelling the documentation gives, and the feature can never be turned on.
    The kernel's own `config.get` walks the nesting for exactly this reason
    (`emptyos/kernel/config.py`); the shell cannot import it, so it walks here.

    The flat spelling is still accepted, because a hand-built dict (a test, a
    caller assembling config in code) may legitimately use it — but the nested
    form is what a real ``emptyos.toml`` produces and is checked first.
    """
    nested = desk.get("quick_entry")
    enabled = nested.get("enabled") if isinstance(nested, dict) else desk.get("quick_entry.enabled")
    if enabled is not True:   # truthy is not True: "yes"/1 stay off
        return ""
    raw = desk.get("quick_shortcut", DEFAULT_QUICK_SHORTCUT)
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_QUICK_SHORTCUT


def choose_shell(*, webview_flag: bool, browser_flag: bool, configured: str) -> str:
    """Pure: the command line beats the config; ``--browser`` beats ``--webview``
    so the escape hatch always works."""
    if browser_flag:
        return "browser"
    if webview_flag:
        return "webview"
    return configured


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


def _webview_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("webview") is not None


def _report(message: str) -> None:
    """Print, and also show a message box when there is no console to print to —
    a double-clicked launch runs under pythonw, where ``sys.stdout`` is None."""
    print(f"[eos-desktop] {message}")
    if sys.stdout is None and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "EmptyOS", 0x30)
        except Exception:
            pass


def run_webview(args: argparse.Namespace, argv: list[str], port: int,
                bind_host: str | None = None, external_schemes: object = None,
                auth_token: str = "", quick_shortcut: str = "") -> int:
    """The native shell. Stays clear of ``emptyos.sdk`` — the desktop venv this
    ends up running in does not carry the daemon's dependencies."""
    from _shared import shell_core

    # The re-exec marker is for this process only. Left in the environment,
    # everything this shell spawns (restart.bat, and through it the daemon and
    # its children) would inherit it and refuse a legitimate re-exec later.
    reexeced = bool(os.environ.pop(REEXEC_ENV, None))

    if args.path is not None and shell_core.safe_local_path(args.path) is None:
        # Refuse rather than silently open the default page: a path that fails
        # the same-origin gate is a typo (or a shell rewriting it — Git Bash
        # turns "/journal/" into a Windows path) and should say so.
        _report(f"--path must be a same-origin path like /journal/, got {args.path!r}")
        return 2
    forward_path = args.path or ("/code/" if args.code else None)

    if not _webview_importable():
        if reexeced:
            _report("the desktop interpreter has no pywebview — reinstall it:\n"
                    "  uv pip install --python <desktop venv>/Scripts/python.exe pywebview")
            return 1
        py = shell_core.resolve_desktop_python()
        if args.dry_run:
            print(f"[eos-desktop] shell:   webview (re-exec via {py or 'NOT FOUND'})")
            return 0 if py else 1
        if py is None:
            _report(f"no desktop interpreter at {shell_core.default_desktop_python()} "
                    "(set EOS_DESKTOP_PYTHON), or use --browser. "
                    "Setup is in scripts/eos_desktop.py's docstring.")
            return 1
        import subprocess
        env = dict(os.environ, **{REEXEC_ENV: "1"})
        subprocess.Popen([str(py), str(Path(__file__).resolve()), *argv],
                         cwd=str(REPO_ROOT), env=env)
        return 0

    offer_start = shell_core.can_offer_start(sys.executable)
    if args.dry_run:
        up = shell_core.is_up(shell_core.probe_health(port))
        print(f"[eos-desktop] shell:   webview (in-process, {sys.executable})")
        print(f"[eos-desktop] daemon:  {'UP' if up else 'DOWN'} at {shell_core.base_url(port)}")
        print(f"[eos-desktop] opens:   {shell_core.start_url(port, forward_path or shell_core.DEFAULT_START_PATH)}")
        print(f"[eos-desktop] start:   {'offered' if offer_start else 'hidden (running as python.exe)'}")
        print(f"[eos-desktop] quick:   {quick_shortcut or 'off ([desktop] quick_entry.enabled)'}")
        return 0

    from _shared.shell import run_shell
    return run_shell(
        port=port, repo_root=REPO_ROOT,
        # The machine token, for the login exchange only — it is traded for a
        # one-shot code and never reaches the window's URL (shell_core.first_url).
        auth_token=auth_token,
        start_path=forward_path or shell_core.DEFAULT_START_PATH,
        forward_path=forward_path, width=args.width, height=args.height,
        start_daemon=run_restart_bat if offer_start else None,
        start_on_launch=args.start, bind_host=bind_host,
        external_schemes=shell_core.clean_external_schemes(external_schemes),
        quick_shortcut=quick_shortcut,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Open the EmptyOS desktop window.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the resolved browser + window args; spawn nothing.")
    ap.add_argument("--start", action="store_true",
                    help="If the daemon is down, run restart.bat (UAC) and wait.")
    ap.add_argument("--code", action="store_true",
                    help="Open the /code IDE window (file tree + diff + terminal + chat) "
                         "instead of the dashboard.")
    ap.add_argument("--webview", action="store_true",
                    help="Open the native window shell (pywebview → WebView2).")
    ap.add_argument("--browser", action="store_true",
                    help="Force the Chromium --app window, overriding [desktop] shell.")
    ap.add_argument("--path", default=None,
                    help="Webview shell: page to open, e.g. /journal/ (same-origin only).")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(raw_argv)

    data = load_config_dict()
    dcfg = desktop_config_from_dict(data)
    shell = choose_shell(webview_flag=args.webview, browser_flag=args.browser,
                         configured=dcfg["shell"])
    if shell == "webview":
        net = data.get("network", {}) if isinstance(data, dict) else {}
        desk = data.get("desktop", {}) if isinstance(data, dict) else {}
        return run_webview(
            args, raw_argv, network_config_from_dict(data)["port"],
            bind_host=net.get("host") if isinstance(net, dict) else None,
            external_schemes=desk.get("external_schemes") if isinstance(desk, dict) else None,
            # Only the webview shell gets it: the browser path opens a Chromium
            # process whose argv is visible to every other process on the box.
            auth_token=str(net.get("auth_token") or "") if isinstance(net, dict) else "",
            quick_shortcut=dcfg["quick_shortcut"],
        )

    from emptyos.sdk.browser_window import (
        build_app_window_args,
        find_chromium,
        open_app_window,
    )
    from emptyos.sdk.daemon_launcher import wait_health

    cfg = network_config_from_dict(data)
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
