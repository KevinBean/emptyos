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

Lifetime is the **window**: the daemon runs in its own process group and is
torn down (SIGTERM → SIGKILL the whole group) when the window closes. Unlike
WriteDesk we own the subprocess handle directly, so no heartbeat-ping hack.

Per-user state lives in:
    ~/Library/Application Support/EmptyOS/
        emptyos.toml      (machine config — created on first run)
        data/             (kernel telemetry: syslog, billing, sessions)
    ~/EmptyOS/Vault/      (default markdown vault — change in Settings)

Flags
-----
  --no-window     boot the daemon only, no window (smoke test / debugging)
  --vault PATH    first-run only: use PATH as the vault instead of the default
  --port N        override the port (else config's; first run picks a free one)
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

APP_NAME = "EmptyOS"
DEFAULT_PORT = 9000

# A .app launched without a console has sys.stdout/stderr == None. Anything that
# probes .isatty() (uvicorn, rich) would crash. Give them real sinks early.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


# ── locations ──────────────────────────────────────────────────────────────
def repo_root() -> Path:
    """EmptyOS repo root. The bundle stub bakes EOS_REPO; in dev we infer it
    from this file's location (products/desktop-macos/launcher.py)."""
    env = os.environ.get("EOS_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def app_support() -> Path:
    d = Path.home() / "Library" / "Application Support" / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_support() / "emptyos.toml"


def default_vault() -> Path:
    return Path.home() / APP_NAME / "Vault"


# ── first-run config ─────────────────────────────────────────────────────────
_CONFIG_TEMPLATE = """\
# EmptyOS Desktop (macOS) — machine config. Auto-created on first run.
# Edit freely; this file is per-user and never committed.

[os]
name = "EmptyOS"
data_dir = "{data_dir}"
log_level = "INFO"

[notes]
# Your markdown vault. Change this path to point at an existing vault.
path = "{vault}"
watch = true

[network]
# local = bind 127.0.0.1, no auth. The desktop app is single-user on one machine.
mode = "local"
host = "127.0.0.1"
port = {port}

[capabilities.think]
# Free/local first; "human" is always the final fallback. Add an OpenAI-compatible
# provider below (uncomment) to automate more.
providers = ["ollama", "human"]
timeout = 30

[capabilities.think.ollama]
host = "http://localhost:11434"
model = "llama3.1"

# [capabilities.think.openai]
# host = "https://api.openai.com"
# model = "gpt-5-mini"
# api_key_env = "OPENAI_API_KEY"

[plugins]
path = "./plugins"

[apps]
path = "./apps"
"""


def ensure_config(vault: Path, port: int) -> None:
    """Write emptyos.toml on first run. Never overwrites an existing config."""
    cfg = config_path()
    if cfg.exists():
        return
    vault.mkdir(parents=True, exist_ok=True)
    data_dir = app_support() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        _CONFIG_TEMPLATE.format(
            data_dir=str(data_dir), vault=str(vault), port=port
        ),
        encoding="utf-8",
    )
    print(f"created {cfg}")


def read_port(default: int = DEFAULT_PORT) -> int:
    cfg = config_path()
    if cfg.exists():
        try:
            data = tomllib.loads(cfg.read_text(encoding="utf-8"))
            return int(data.get("network", {}).get("port") or default)
        except Exception as e:
            print(f"config unreadable, using default port: {e}", file=sys.stderr)
    return default


def free_port(start: int = DEFAULT_PORT, count: int = 50) -> int:
    """First bindable 127.0.0.1 port at/after `start`. Lets the desktop app boot
    even when the preferred port is busy (a second instance, or a dev daemon
    already on 9000). Falls back to `start` if the whole range is taken."""
    import socket
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


# ── daemon lifecycle ─────────────────────────────────────────────────────────
def start_daemon(repo: Path, env: dict) -> subprocess.Popen:
    """Spawn `python -m emptyos start` in its own process group (so we can tear
    down the whole tree — sandbox-pool / dogfood sidecar children included)."""
    return subprocess.Popen(
        [sys.executable, "-m", "emptyos", "start"],
        cwd=str(repo),
        env=env,
        start_new_session=True,  # new process group → killpg on shutdown
    )


def wait_health(url: str, proc: subprocess.Popen, timeout: float = 60.0) -> bool:
    """Poll /api/health until the daemon answers. Abort early if it dies."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False  # daemon exited during boot (bad config / port busy)
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def stop_daemon(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


# ── main ─────────────────────────────────────────────────────────────────────
def _arg(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main() -> int:
    repo = repo_root()
    if not (repo / "emptyos" / "__main__.py").exists():
        print(f"EmptyOS repo not found at {repo} (set EOS_REPO).", file=sys.stderr)
        return 1

    vault = Path(_arg("--vault") or default_vault()).expanduser()
    forced = _arg("--port")
    if forced:
        port = int(forced)               # explicit override always wins
    elif config_path().exists():
        port = read_port()               # respect the user's pinned port
    else:
        port = free_port(DEFAULT_PORT)   # first run: pick a free port (9000 may be busy)
    ensure_config(vault, port)           # writes the chosen port on first run only
    url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["EOS_CONFIG"] = str(config_path())
    env["PYTHONIOENCODING"] = "utf-8"
    # Make the repo importable even if the venv didn't `pip install -e .`.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    proc = start_daemon(repo, env)
    if not wait_health(url, proc):
        log = app_support() / "data" / "daemon.err.log"
        hint = f" See {log}." if log.exists() else ""
        print(f"daemon failed to start at {url} (port {port} may be in use).{hint}",
              file=sys.stderr)
        stop_daemon(proc)
        return 1
    print(f"{APP_NAME} running at {url}")

    if "--no-window" in sys.argv:
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        stop_daemon(proc)
        return 0

    try:
        import webview  # pywebview → native WKWebView on macOS
    except ImportError:
        print("pywebview not installed; opening default browser instead.",
              file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        stop_daemon(proc)
        return 0

    webview.create_window(APP_NAME, url, width=1280, height=860,
                          min_size=(900, 600))
    try:
        webview.start()  # blocks until every window closes
    finally:
        stop_daemon(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
