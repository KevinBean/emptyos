"""Desktop-product launcher core — boot the daemon, own its lifetime, show a window.

This is what a product's ``.exe`` actually runs. It replaces the two divergent
launchers it was extracted from (``scripts/plekto_launcher.py``, which ran the
kernel *in-process* in the foreground and so had no shutdown or restart story,
and ``products/desktop-macos/launcher.py``, whose subprocess-ownership model is
the one kept).

Architecture
------------
The launcher and the daemon are **two processes of the same executable**:

    EmptyOS.exe                     ← supervisor (this module, no kernel import)
      ├─ EmptyOS.exe --daemon       ← the real EmptyOS daemon (kernel + web)
      ├─ chromeless browser window  ← detached; points at 127.0.0.1:<port>
      └─ tray icon (bg thread)      ← Open / Restart / Quit

Owning the daemon as a subprocess (rather than running it in the foreground)
buys three things a product needs: a killable handle, an exit code, and the
ability to respawn. **Exit code 42 means "restart me"** — the first-run wizard
(vault relocation) and the updater ("restart to apply") both use it, and the
supervisor loop simply respawns.

Shutdown is graceful-first. The daemon child watches its stdin; the supervisor
closes it, the child sees EOF and raises ``KeyboardInterrupt`` in its own main
thread — exactly what Ctrl-C does — so ``cli.main.start``'s ``finally:
kernel.stop()`` runs and SQLite closes cleanly. Only if that hasn't landed
within ``STOP_GRACE_S`` do we kill the tree. This detour exists because Windows
has no usable graceful-terminate for a console-less child: ``SIGTERM`` is
``TerminateProcess`` (no cleanup) and ``CTRL_C_EVENT`` is ignored by processes
created with ``CREATE_NEW_PROCESS_GROUP``. Force-killing a live daemon is what
``.claude/rules/daemon-handling.md`` warns leaves dangling WAL handles.

Per-user state lives outside the app directory (so an update can replace the
app wholesale without touching it):

    %APPDATA%\\<AppName>\\emptyos.toml     machine config (created on first run)
    %APPDATA%\\<AppName>\\data\\           kernel telemetry (syslog, billing…)
    %APPDATA%\\<AppName>\\launcher.log     supervisor log
    %APPDATA%\\<AppName>\\daemon.log       daemon stdout/stderr
    ~/Documents/<AppName>Vault/            default vault (changeable in Settings)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

from .product_config import ProductConfig

# A --noconsole exe has sys.stdout/stderr == None; anything probing .isatty()
# (uvicorn, rich) crashes. Give them real sinks before any of it loads.
if sys.stdout is None:  # pragma: no cover - only true in a windowed build
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:  # pragma: no cover
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

#: A daemon that exits with this code is asking to be respawned.
RESTART_EXIT_CODE = 42

#: A daemon that exits with this code is asking to be restarted **into a newly
#: installed version**. The supervisor cannot do that itself — it *is* the old
#: version, and re-spawning itself would just start the old one again — so it
#: hands off to the stub, which always picks the newest complete version.
UPDATE_EXIT_CODE = 43

#: How long after boot to look for an update. Late enough that it never competes
#: with startup for bandwidth or attention.
UPDATE_CHECK_DELAY_S = 30.0

#: How long a graceful stop may take before we kill the process tree.
STOP_GRACE_S = 20.0

#: How long the daemon has to answer /api/health before we call the boot failed.
#: Generous because a frozen first boot is nothing like a dev one: 65 apps are
#: imported from source inside the bundle, and if the install dir is read-only
#: (Program Files) none of that is cached to __pycache__, so every boot pays it.
#: At 90s this was cutting the daemon off mid-app-load and reporting a failure
#: that hadn't happened.
BOOT_TIMEOUT_S = 240.0

DEFAULT_PORT = 9000


# ── locations ────────────────────────────────────────────────────────────────
def app_root() -> Path:
    """Directory holding ``apps/``, ``plugins/``, ``engines/`` and the ``emptyos``
    package — i.e. what the daemon must treat as its working directory.

    Frozen: PyInstaller's data tree (``_MEIPASS``; the ``_internal`` dir of a
    one-dir build). From source: the repo root.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def install_root() -> Path:
    """Directory the product was installed into.

    Phase 3 lays out ``<install_root>/app-<version>/`` beside a stable stub, so
    the install root is the *parent* of the app directory when that layout is in
    play; otherwise it is the exe's own directory. The updater is handed this
    path via ``EOS_INSTALL_ROOT``.
    """
    if not getattr(sys, "frozen", False):
        return app_root()
    exe_dir = Path(sys.executable).resolve().parent
    if exe_dir.name.startswith("app-"):
        return exe_dir.parent
    return exe_dir


def user_data_dir(appdata_name: str) -> Path:
    """Per-user state directory. Mirrors the platformdirs convention without
    taking the runtime dependency — we know the three OSes we ship for."""
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA") or home) / appdata_name
    if system == "Darwin":
        return home / "Library" / "Application Support" / appdata_name
    return Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share")) / appdata_name


def default_vault_dir(appdata_name: str) -> Path:
    """First-run vault location. Documents is discoverable — users find it when
    they want to back it up or point a sync client at it."""
    return Path.home() / "Documents" / f"{appdata_name}Vault"


def free_port(start: int = DEFAULT_PORT, count: int = 50) -> int:
    """First bindable loopback port at or after ``start``.

    Scans *upward from 9000* on purpose: a product wants the standard EmptyOS
    port when it is free, and must still boot when it is not (a dev daemon, or a
    second copy of the product). This is the opposite of
    ``check_snapshot_boot.pick_free_port``, which binds :0 for an *ephemeral*
    port precisely to stay off 9000-9009. Don't unify them — the strategies are
    contradictory by design.
    """
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start  # fall back; the daemon will fail loudly if it is truly blocked


# ── first-run config ─────────────────────────────────────────────────────────
CONFIG_TEMPLATE = """\
# {display_name} — machine config, created on first run.
# Per-user and never committed. Edit here, or use the in-app Settings page.

[os]
name = "{display_name}"
data_dir = "{data_dir}"
log_level = "INFO"

[notes]
# Your markdown vault. Point this at an existing vault to adopt it.
path = "{vault}"
watch = true

[network]
# local = bind loopback only, no auth. A desktop app is single-user on one machine.
mode = "local"
host = "127.0.0.1"
port = {port}

[capabilities.think]
# Local-first; "human" is always the final fallback, so the app is useful with
# no model configured at all. Add a cloud key in Settings to automate more.
providers = {think_providers}
timeout = 30

{ollama_config}

[capabilities.draw]
providers = ["human"]

# Plugins that are wrong for a packaged product. Both are hard-off here; the
# store cannot re-enable them (a toml `enabled = false` wins).
[plugins.system-tray]
# The launcher owns the tray, so it survives daemon restarts. Leaving the
# daemon-side plugin on gives the user two icons and starts a Windows message
# loop inside the daemon during boot.
enabled = false

[plugins.dogfood-demo]
# Dev-only: it spawns a *second* EmptyOS daemon as a test sidecar. In a frozen
# product sys.executable is the product exe, so it would boot another copy of
# the product on the user's machine.
enabled = false

[plugins.global-hotkey]
# Off in the general desktop product unless its product declaration explicitly
# bundles the keyboard hook and enables this owner-scoped service.
enabled = {global_hotkey_enabled}
"""


def ensure_config(config_path: Path, *, vault: Path, port: int, display_name: str,
                  think_providers: tuple[str, ...] = ("ollama", "human"),
                  enable_plugins: tuple[str, ...] = ()) -> bool:
    """Create the config + vault + data dirs if absent. Never overwrites.

    Returns True when this call *created* the config — i.e. this is a first run,
    and the caller should send the user to the welcome wizard.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    (config_path.parent / "data").mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        return False
    vault.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        CONFIG_TEMPLATE.format(
            display_name=display_name,
            data_dir=(config_path.parent / "data").as_posix(),
            vault=vault.as_posix(),
            port=port,
            think_providers=json.dumps(list(think_providers)),
            ollama_config=(
                '[capabilities.think.ollama]\n'
                'host = "http://localhost:11434"\n'
                'model = "llama3.1"'
                if "ollama" in set(think_providers) else ""
            ),
            global_hotkey_enabled=(
                "true" if "global-hotkey" in set(enable_plugins) else "false"
            ),
        ),
        encoding="utf-8",
    )
    return True


def read_port(config_path: Path, default: int = DEFAULT_PORT) -> int:
    """The port the user's config pins. Falls back to ``default`` if unreadable —
    a corrupt config shouldn't stop the app from booting somewhere."""
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return int(data.get("network", {}).get("port") or default)
    except Exception:
        return default


# ── daemon lifecycle ─────────────────────────────────────────────────────────
def daemon_argv(entry_script: str | Path | None = None) -> list[str]:
    """Command that re-runs *this same program* in daemon mode.

    Frozen, ``sys.executable`` is the product exe, so ``[exe, "--daemon"]`` is
    the whole story. From source it is the Python interpreter, so the product's
    ``launcher.py`` has to come along.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--daemon"]
    entry = str(entry_script or sys.argv[0])
    return [sys.executable, entry, "--daemon"]


def daemon_env(product: ProductConfig, config_path: Path) -> dict[str, str]:
    """Environment for the daemon child.

    ``EOS_PRODUCT`` is the dark-flag for every product-only daemon surface (the
    welcome wizard, the update endpoint): a dev daemon on :9000 never sets it, so
    it never grows them.

    App/plugin/engine paths are passed as ``EOS_*`` overrides rather than written
    into ``emptyos.toml`` because they live *inside the versioned app directory* —
    an update moves them, and the user's config must survive that untouched.
    """
    root = app_root()
    env = dict(os.environ)
    env["EOS_CONFIG"] = str(config_path)
    env["EOS_PRODUCT"] = product.id
    env["EOS_INSTALL_ROOT"] = str(install_root())
    # Where MANIFEST.json lives, so the About panel can name its own version
    # without depending on the daemon's cwd.
    env["EOS_BUNDLE_ROOT"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    # Unbuffered, or the daemon's log is empty exactly when we need it — a wedged
    # or crashed boot flushes nothing, and the user has nothing to send us.
    env["PYTHONUNBUFFERED"] = "1"
    # Must be set before the child loads any BLAS-backed DLL. run_daemon pins
    # these too, for a child started by hand.
    for key, value in NATIVE_THREAD_ENV.items():
        env.setdefault(key, value)
    for key, sub in (("EOS_APPS_PATH", "apps"), ("EOS_PLUGINS_PATH", "plugins"),
                     ("EOS_ENGINES_PATH", "engines")):
        path = root / sub
        if path.is_dir():
            env[key] = str(path)
    if not getattr(sys, "frozen", False):
        # Running from source: make the repo importable even without `pip install -e .`
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
    return env


#: What the supervisor writes to the daemon's stdin to ask it to shut down.
STOP_MESSAGE = b"stop"


#: OpenBLAS builds a thread pool inside its DLL entry point. In a frozen bundle
#: that runs under the Windows loader lock, and it **deadlocks** — the daemon
#: hangs forever inside `import numpy` (and then `scipy.linalg._fblas`), printing
#: nothing, exiting nothing, with no error anywhere. It took a py-spy dump of the
#: wedged process to see it at all. Pinning the pool to one thread means nothing
#: is spawned at init, so the lock is never contended.
#:
#: The cost is single-threaded BLAS. For a desktop product that is the right
#: trade: engine maths here is small, and a multi-threaded BLAS on a laptop
#: mostly oversubscribes the cores the UI needs anyway.
NATIVE_THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}


#: Every native scientific module the bundled code imports, loaded up front.
#:
#: Loading a numpy/scipy extension DLL **deadlocks** inside a frozen bundle once
#: the process has other threads running — the daemon has a scheduler, a vault
#: watcher and an asyncio pool alive well before it reaches its engines, and the
#: import then blocks forever on the Windows loader lock with no error, no output
#: and no exit. Warm-importing one module only moved the hang to the next one
#: (numpy -> scipy.linalg._fblas -> scipy.sparse.linalg.arpack), which is what
#: showed the rule: it is not any single library, it is *extension loading with
#: threads alive*.
#:
#: So load them all while the daemon is still single-threaded. The list mirrors
#: what `engines/` and `apps/` actually import — keep it in step with
#: `grep -rE "^(from|import) (numpy|scipy|pandas)" engines/ apps/`.
WARM_IMPORTS = (
    "numpy",
    "scipy.linalg",
    "scipy.sparse",
    "scipy.sparse.linalg",
    "scipy.optimize",
    "scipy.special",
    "pandas",
)


def _warm_import_native() -> None:
    """Pin the native thread pools, then import every native lib on the main thread.

    Both halves matter. The env pin (see :data:`NATIVE_THREAD_ENV`) stops OpenBLAS
    spawning a thread pool inside its DLL entry point; the warm import (see
    :data:`WARM_IMPORTS`) makes every remaining extension load happen here, at the
    daemon's first statement, before any thread exists to contend with.

    ``setdefault``: an operator who deliberately set these keeps their value.
    Failures are ignored — an absent scipy is a degraded engine, not a dead daemon.
    """
    for key, value in NATIVE_THREAD_ENV.items():
        os.environ.setdefault(key, value)
    for mod in WARM_IMPORTS:
        try:
            __import__(mod)
        except Exception:
            pass


def run_daemon() -> int:
    """The ``--daemon`` child: run the real EmptyOS daemon in this process.

    Uses the same entrypoint as ``eos start`` so there is one boot path.
    A watcher thread turns the supervisor's stop message into a
    ``KeyboardInterrupt`` on the main thread, which is the graceful path
    ``cli.main.start`` already handles.
    """
    root = app_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)  # relative ./apps, ./plugins in config resolve against this
    _warm_import_native()
    threading.Thread(target=_stdin_watch, daemon=True).start()

    from emptyos.cli.main import start as eos_start

    try:
        eos_start(no_web=False)
    except KeyboardInterrupt:
        pass
    return 0


def _stdin_watch() -> None:
    """Wait for the supervisor's explicit stop message, then interrupt the main thread.

    It must be an explicit message, **not** EOF. EOF is the normal state of a
    process with no stdin — a double-clicked exe, a detached child, anything
    launched without a pipe — so treating it as "stop" fired the interrupt
    immediately, and the daemon died with a ``KeyboardInterrupt`` raised in the
    middle of ``import emptyos.cli.main``. An explicit message can only arrive
    from a supervisor that means it, and only after the daemon is up.
    """
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return  # no stdin at all (windowed build): nothing can ask us to stop
    try:
        while True:
            line = stream.readline()
            if not line:
                return  # EOF — the supervisor is gone; let it kill us if it must
            if line.strip() == STOP_MESSAGE:
                break
    except Exception:
        return

    import _thread

    _thread.interrupt_main()


def stop_process(proc: subprocess.Popen, *, grace: float = STOP_GRACE_S) -> None:
    """Graceful stop, then kill the tree.

    Two graceful signals, because the platforms don't share one:

    - **POSIX** — ``SIGTERM`` to the process group. uvicorn handles it.
    - **Windows** — write ``stop`` to the child's stdin. Its watcher thread turns
      that into a ``KeyboardInterrupt`` (see :func:`_stdin_watch`). There is no
      signal to send: ``SIGTERM`` is ``TerminateProcess`` (no cleanup) and
      ``CTRL_C_EVENT`` is ignored by a ``CREATE_NEW_PROCESS_GROUP`` child.

    Both are sent regardless of platform — a child that only understands one is
    unaffected by the other. Only when neither lands within ``grace`` do we kill,
    tree-wide, because the daemon has children of its own (sandbox pool, dogfood
    sidecar, external services) that would otherwise be orphaned.
    """
    if proc.poll() is not None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(STOP_MESSAGE + b"\n")
            proc.stdin.flush()
            proc.stdin.close()
    except Exception:
        pass
    if os.name != "nt":
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass

    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            subprocess.run(
                [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False,
            )
        else:  # pragma: no cover - taskkill ships with Windows
            proc.kill()
    else:
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


# ── supervisor ───────────────────────────────────────────────────────────────
class Supervisor:
    """Owns the daemon process for the life of the product.

    The loop is deliberately small: spawn, wait for health, wait for exit, and
    decide whether that exit means *respawn* (code 42, or a tray restart) or
    *we're done*.
    """

    def __init__(self, product: ProductConfig, *, entry_script: str | Path | None = None,
                 open_window: bool = True):
        self.product = product
        self.entry_script = entry_script
        self.open_window_enabled = open_window

        self.data_dir = user_data_dir(product.appdata_name)
        self.config_path = self.data_dir / "emptyos.toml"
        self.log_path = self.data_dir / "launcher.log"
        self.daemon_log_path = self.data_dir / "daemon.log"

        self.port = DEFAULT_PORT
        self.first_run = False
        self._proc: subprocess.Popen | None = None
        self._quit = False
        self._restart_requested = False
        self._window_opened = False
        self._update_checked = False

    # -- logging ---------------------------------------------------------------
    def log(self, message: str) -> None:
        """Always write to a file. A windowed build has nowhere else to say
        'the daemon died' — this file is the only diagnosis a user can send us."""
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        print(f"[{self.product.id}] {message}")
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # -- lifecycle -------------------------------------------------------------
    def prepare(self) -> None:
        """Pick a port and lay down first-run config."""
        if self.config_path.exists():
            self.port = read_port(self.config_path)
        else:
            self.port = free_port()
        self.first_run = ensure_config(
            self.config_path,
            vault=default_vault_dir(self.product.appdata_name),
            port=self.port,
            display_name=self.product.display_name,
            think_providers=self.product.think_providers,
            enable_plugins=self.product.enable_plugins,
        )
        self.log(f"config={self.config_path} port={self.port} first_run={self.first_run}")

    def spawn_daemon(self) -> subprocess.Popen:
        argv = daemon_argv(self.entry_script)
        env = daemon_env(self.product, self.config_path)
        self.daemon_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.daemon_log_path, "ab", buffering=0)
        kwargs: dict = {}
        if os.name == "nt":
            # Own process group so a kill reaches the daemon's own children.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(
                argv, cwd=str(app_root()), env=env,
                stdin=subprocess.PIPE,     # closing this is the graceful stop signal
                stdout=log_fh, stderr=subprocess.STDOUT,
                **kwargs,
            )
        finally:
            log_fh.close()  # Popen duplicated the handle

    def open_window(self, url: str) -> None:
        if self._window_opened or not self.open_window_enabled:
            return
        self._window_opened = True
        try:
            from emptyos.sdk.browser_window import open_app_window

            open_app_window(
                url,
                width=self.product.window_width,
                height=self.product.window_height,
                profile_dir=self.data_dir / "window-profile",
            )
        except Exception as e:
            self.log(f"no chromeless window ({e}); falling back to the default browser")
            import webbrowser

            webbrowser.open(url)

    def startup_url(self) -> str:
        """Where the window lands. A first run goes to the welcome wizard — but
        only once a product declares one, so a product without the wizard can't
        open a 404."""
        if self.first_run and self.product.welcome_url:
            return self.url + self.product.welcome_url
        return self.url + self.product.start_url

    # -- tray callbacks --------------------------------------------------------
    def request_restart(self) -> None:
        self._restart_requested = True
        if self._proc:
            stop_process(self._proc)

    def request_quit(self) -> None:
        self._quit = True
        if self._proc:
            stop_process(self._proc)

    def request_open_window(self) -> None:
        self.request_open_window_at(self.product.start_url)

    def request_open_window_at(self, path: str) -> None:
        self._window_opened = False
        self.open_window(self.url + path)

    # -- updates ---------------------------------------------------------------
    def current_version(self) -> str:
        """The running version, read from the bundle's own MANIFEST.json."""
        try:
            data = json.loads((app_root() / "MANIFEST.json").read_text(encoding="utf-8"))
            return str(data.get("version") or "")
        except Exception:
            return ""

    def check_for_update(self) -> str:
        """Fetch, verify and stage a newer version. Returns the version, or "".

        Staged, not applied: the new version sits in its own directory and only
        becomes live when the stub next picks it up. Nothing about the running
        install changes here, so a bad update cannot break a working one.
        """
        if not self.product.update_feed:
            return ""
        try:
            from . import updater

            release = updater.check(self.product.update_feed, self.current_version())
            if release is None:
                return ""
            self.log(f"update available: {release.version}")
            updater.update_to(release, install_root())
            updater.prune(install_root(), protect=self.current_version())
            self.log(f"update {release.version} staged — will apply on next start")
            return release.version
        except Exception as e:
            # Being offline, or a feed that 404s, is not an error worth surfacing.
            self.log(f"update check failed: {e}")
            return ""

    def _update_check_later(self) -> None:
        time.sleep(UPDATE_CHECK_DELAY_S)
        self.check_for_update()

    def _launch_stub_and_quit(self) -> None:
        """Hand off to the stub so the *newest* version starts, not this one."""
        from . import updater

        root = install_root()
        stub = next((p for p in root.glob("*.exe")), None) if root.is_dir() else None
        if stub is None or not getattr(sys, "frozen", False):
            self.log("no stub to hand off to — staying on the current version")
            return
        newest = updater.newest_version(root)
        self.log(f"restarting via stub into {newest[0] if newest else 'newest'}")
        kwargs: dict = {"cwd": str(root), "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([str(stub)], **kwargs)

    # -- main loop -------------------------------------------------------------
    def next_action(self, exit_code: int) -> str:
        """What a daemon exit means: ``"quit"`` | ``"restart"`` | ``"update"`` | ``"exit"``.

        Split out from :meth:`run` so the decision is testable without spawning
        anything — it is the one piece of the loop with real logic.
        """
        if self._quit:
            return "quit"
        if exit_code == UPDATE_EXIT_CODE:
            return "update"
        if self._restart_requested or exit_code == RESTART_EXIT_CODE:
            return "restart"
        return "exit"

    def _watch_stdin_for_quit(self) -> None:
        """Let a parent process ask the supervisor to quit by writing ``stop``.

        Without this the supervisor has no shutdown path except the tray, so a
        headless run (``--no-tray``, i.e. CI and the smoke test) can only be
        killed. Same explicit-message rule as the daemon's watcher — EOF is the
        normal state of a double-clicked exe and must not mean "quit".
        """
        stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            return
        try:
            while True:
                line = stream.readline()
                if not line:
                    return
                if line.strip() == STOP_MESSAGE:
                    break
        except Exception:
            return
        self.log("stop requested")
        self.request_quit()

    def run(self) -> int:
        from emptyos.sdk.daemon_launcher import wait_health

        threading.Thread(target=self._watch_stdin_for_quit, daemon=True).start()
        self.prepare()
        while True:
            self._proc = self.spawn_daemon()
            if not wait_health(self.url, self._proc, timeout=BOOT_TIMEOUT_S):
                # A quit that lands *during* boot kills the daemon, which makes
                # wait_health report a failure that never happened. Asking to
                # quit is not an error.
                if self._quit:
                    self.log("quit during boot")
                    return 0
                self.log(
                    f"daemon failed to start at {self.url} — see {self.daemon_log_path}"
                )
                stop_process(self._proc)
                return 1
            self.log(f"{self.product.display_name} running at {self.url}")
            self.open_window(self.startup_url())
            if self.product.update_feed and not self._update_checked:
                self._update_checked = True
                threading.Thread(target=self._update_check_later, daemon=True).start()

            code = self._proc.wait()
            self._proc = None

            action = self.next_action(code)
            if action == "quit":
                self.log("quit")
                return 0
            if action == "update":
                # The daemon has told us a newer version is staged. We are the old
                # version, so we cannot start it — the stub can.
                self._launch_stub_and_quit()
                return 0
            if action == "restart":
                self._restart_requested = False
                self.first_run = False  # never re-open the wizard on a respawn
                self.log("restarting daemon")
                continue
            self.log(f"daemon exited (code {code})")
            return code


# ── entrypoint ───────────────────────────────────────────────────────────────
def main(product: ProductConfig, argv: list[str] | None = None,
         *, entry_script: str | Path | None = None) -> int:
    """Product entrypoint. Products call this from their ``launcher.py``."""
    args = list(sys.argv[1:] if argv is None else argv)

    if "--daemon" in args:
        return run_daemon()

    sup = Supervisor(
        product,
        entry_script=entry_script,
        open_window="--no-window" not in args,
    )

    tray = None
    if "--no-tray" not in args:
        try:
            from .tray import start_tray

            tray = start_tray(sup)
        except Exception as e:  # tray is a convenience, never a boot blocker
            sup.log(f"tray unavailable ({e})")

    try:
        return sup.run()
    finally:
        if tray is not None:
            tray.stop()
