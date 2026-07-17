"""EmptyOS Desktop (macOS) launcher — boot the real daemon, open a native window.

This is NOT a WriteDesk-style app slice. It wraps the **whole EmptyOS daemon**
(all apps, plugins, vault, conversation-mode growth) in a native macOS window.
The daemon is the real codebase — discovered from the repo at `cwd` — so a
`git pull` or an edit under `apps/` is picked up on next launch with no rebuild.

Architecture
------------
    EmptyOS.app  (bundle built by build_app.py)
      └─ Contents/MacOS/EmptyOS   shell stub: sets EOS_REPO + venv python,
                                  execs THIS launcher
            │
            ├─ boots `python -m emptyos start` as a subprocess
            │     cwd = repo root            → apps/ plugins/ engines/ discovery
            │     EOS_CONFIG = app-support   → config + data + vault live OUTSIDE
            │                                  the repo (survives git, read-only ok)
            │
            ├─ waits for /api/health on 127.0.0.1:<port>
            └─ opens a native WKWebView window (pywebview) at the local URL

Lifetime is the **window**: the daemon runs in its own process group and is torn
down when the window closes. Unlike WriteDesk we own the subprocess handle
directly, so no heartbeat-ping hack.

Runs *from the repo*, not from a frozen tier bundle — that is the one thing that
separates it from `products/desktop-windows/`. Everything they genuinely share
(per-user paths, first-run config, port selection, process teardown) now lives in
`products/_shared/launcher_core.py`, which was extracted when this became its
second caller.

Per-user state:
    ~/Library/Application Support/EmptyOS/emptyos.toml   (created on first run)
    ~/Library/Application Support/EmptyOS/data/          (kernel telemetry)
    ~/Documents/EmptyOSVault/                            (default vault)

Flags
-----
  --no-window     boot the daemon only, no window (smoke test / debugging)
  --vault PATH    first-run only: use PATH as the vault instead of the default
  --port N        override the port (else config's; first run picks a free one)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared.launcher_core import (  # noqa: E402
    DEFAULT_PORT,
    RESTART_EXIT_CODE,
    default_vault_dir,
    ensure_config,
    free_port,
    read_port,
    stop_process,
    user_data_dir,
)
from _shared.product_config import load_product, product_toml_path  # noqa: E402

PRODUCT = load_product(product_toml_path(__file__))


def repo_root() -> Path:
    """EmptyOS repo root. The bundle stub bakes EOS_REPO; in dev we infer it
    from this file's location (products/desktop-macos/launcher.py)."""
    env = os.environ.get("EOS_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    return user_data_dir(PRODUCT.appdata_name) / "emptyos.toml"


def start_daemon(repo: Path, env: dict) -> subprocess.Popen:
    """Spawn `python -m emptyos start` in its own process group (so teardown
    reaches the whole tree — sandbox-pool / dogfood sidecar children included)."""
    return subprocess.Popen(
        [sys.executable, "-m", "emptyos", "start"],
        cwd=str(repo),
        env=env,
        start_new_session=True,
    )


def _arg(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main() -> int:
    from emptyos.sdk.daemon_launcher import wait_health

    repo = repo_root()
    if not (repo / "emptyos" / "__main__.py").exists():
        print(f"EmptyOS repo not found at {repo} (set EOS_REPO).", file=sys.stderr)
        return 1

    cfg = config_path()
    vault = Path(_arg("--vault") or default_vault_dir(PRODUCT.appdata_name)).expanduser()
    forced = _arg("--port")
    if forced:
        port = int(forced)              # explicit override always wins
    elif cfg.exists():
        port = read_port(cfg)           # respect the user's pinned port
    else:
        port = free_port(DEFAULT_PORT)  # first run: 9000 may already be busy
    ensure_config(cfg, vault=vault, port=port, display_name=PRODUCT.display_name)
    url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["EOS_CONFIG"] = str(cfg)
    env["EOS_PRODUCT"] = PRODUCT.id
    env["PYTHONIOENCODING"] = "utf-8"
    # Make the repo importable even if the venv didn't `pip install -e .`.
    env["PYTHONPATH"] = os.pathsep.join([str(repo), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    while True:
        proc = start_daemon(repo, env)
        if not wait_health(url, proc, timeout=60.0):
            log = user_data_dir(PRODUCT.appdata_name) / "data" / "daemon.err.log"
            hint = f" See {log}." if log.exists() else ""
            print(f"daemon failed to start at {url} (port {port} may be in use).{hint}",
                  file=sys.stderr)
            stop_process(proc)
            return 1
        print(f"{PRODUCT.display_name} running at {url}")

        code = _hold_window(proc, url)
        # A daemon that exits 42 is asking to be respawned (settings changed the
        # vault, an update landed). Any other exit is the end of the run.
        if code != RESTART_EXIT_CODE:
            return code


def _hold_window(proc: subprocess.Popen, url: str) -> int:
    """Show the window and block until it closes (or the daemon dies).
    Returns the daemon's exit code."""
    headless = "--no-window" in sys.argv
    if not headless:
        try:
            import webview  # pywebview → native WKWebView on macOS
        except ImportError:
            print("pywebview not installed; opening default browser instead.",
                  file=sys.stderr)
            import webbrowser

            webbrowser.open(url)
            headless = True

    if headless:
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        stop_process(proc)
        return proc.returncode or 0

    webview.create_window(PRODUCT.display_name, url,
                          width=PRODUCT.window_width, height=PRODUCT.window_height,
                          min_size=(900, 600))
    try:
        webview.start()  # blocks until every window closes
    finally:
        code = proc.poll()
        stop_process(proc)
    # The daemon exiting on its own (e.g. a restart request) also closes the
    # window, so its code — not the window — decides what happens next.
    return code if code is not None else 0


if __name__ == "__main__":
    sys.exit(main())
