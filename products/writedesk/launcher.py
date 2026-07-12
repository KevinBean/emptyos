"""WriteDesk launcher — boots the mini-server and opens an app window.

PyInstaller entry point. Reads config.toml from the exe's directory, starts
uvicorn on a free localhost port, then opens a chromeless Edge app window
(dedicated profile so mic permission persists and the process handle is ours
to wait on). Closing the window shuts the server down.

Flags:
  --no-window   run the server only (for testing / debugging)
  --port N      pin the port (default: pick a free one)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

# PyInstaller --noconsole: sys.stdout/stderr are None, and uvicorn's log
# formatter calls .isatty() on them at Config() time. Give it real streams
# BEFORE importing uvicorn.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import uvicorn

from server import create_app  # frozen alongside this module by PyInstaller


def app_dir() -> Path:
    """Directory the exe (or this script, in dev) lives in — config + caches."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def assets_dir() -> Path:
    """Bundled assets — _MEIPASS when frozen, ./assets in dev."""
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return base / "assets"


def load_config() -> dict:
    p = app_dir() / "config.toml"
    if p.exists():
        try:
            return tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"config.toml unreadable: {e}", file=sys.stderr)
    return {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def find_browser() -> list[str] | None:
    """Edge first (every Win10/11 has it), Chrome as fallback."""
    import os
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return [str(c)]
    return None


def main():
    cfg = load_config()
    port = 0
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    port = port or int(cfg.get("port") or 0) or free_port()
    url = f"http://127.0.0.1:{port}"

    log_file = app_dir() / "writedesk.log"
    app = create_app(cfg, assets_dir(), app_dir())

    # log_config=None skips uvicorn's dictConfig (its colourized formatter
    # probes isatty and is pointless in a windowed exe anyway).
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    if not wait_health(url) or not t.is_alive():
        # t dead = our uvicorn failed (e.g. port already bound by another
        # process) even if something else answered the health probe.
        log_file.write_text("server failed to start (port busy?)", encoding="utf-8")
        sys.exit(1)
    try:
        log_file.write_text(f"started ok at {url}\n", encoding="utf-8")
    except Exception:
        pass
    print(f"WriteDesk running at {url}")

    if "--no-window" in sys.argv:
        try:
            while t.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    browser = find_browser()
    if browser:
        profile = app_dir() / "browser-profile"
        profile.mkdir(exist_ok=True)  # never let Edge fail/fall back on mkdir
        subprocess.Popen(
            browser + [f"--app={url}/", f"--user-data-dir={profile}",
                       "--no-first-run", "--no-default-browser-check"],
        )
    else:
        import webbrowser
        webbrowser.open(url)

    # Lifetime is decided by the window's heartbeat, NOT the spawned process:
    # Edge re-execs itself (and may hand off to an existing instance), so the
    # process we spawned often exits while the app window lives on. Every
    # served page pings /api/ping every 3s; we stay up while pings arrive and
    # shut down after they stop. The 120s floor covers slow first launches
    # (profile creation, antivirus scan) before the first page load.
    start = time.time()
    try:
        while True:
            time.sleep(2)
            idle = time.time() - app.state.last_seen
            if idle > 15 and time.time() - start > 120:
                break
    except KeyboardInterrupt:
        pass

    server.should_exit = True
    t.join(timeout=10)


if __name__ == "__main__":
    main()
