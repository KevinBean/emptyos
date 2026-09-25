"""System Tray Plugin — puts EmptyOS in the native taskbar tray.

Stands down while an EmptyOS Desktop shell's own tray icon is up for this
daemon's port (``emptyos/desktop_presence.py``): two icons — one of them
offering a Quit that ends the *daemon* — is exactly the confusion the shell's
tray exists to remove. The shell claims that only once its tray is running, so
a shell without one never hides this icon. It comes back within
``DEFER_POLL_S`` of the shell's tray going away, crash included.

While it stands down, this icon's **Capture Thought** (the only emitter of
``tray:capture_clicked``) and **Restart** are not on screen; the shell's tray
offers neither. ``[plugins.system-tray] defer_to_shell = false`` keeps this icon
visible regardless, for anyone who relies on them.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import webbrowser
from pathlib import Path

from emptyos.desktop_presence import shell_tray_present
from emptyos.sdk import BasePlugin

#: How often the icon re-checks for an attached desktop shell.
DEFER_POLL_S = 5.0

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


class SystemTrayPlugin(BasePlugin):
    name = "system-tray"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._icon = None
        self._thread = None
        self._loop = None
        self._stop = threading.Event()
        self._deferring = False

    async def connect(self):
        if not HAS_TRAY:
            print("[Tray] 'pystray' or 'Pillow' not installed. Run: pip install pystray Pillow")
            return

        self._loop = asyncio.get_running_loop()

        # Start tray in a background thread (pystray blocks)
        self._thread = threading.Thread(target=self._run_tray, daemon=True)
        self._thread.start()
        print("[Tray] System tray icon spawned")

    async def disconnect(self):
        self._stop.set()
        if self._icon:
            self._icon.stop()

    async def available(self) -> bool:
        return HAS_TRAY

    def _create_image(self):
        # Generate a simple icon for the tray
        image = Image.new("RGB", (64, 64), color="black")
        d = ImageDraw.Draw(image)
        d.ellipse([16, 16, 48, 48], fill="white")
        return image

    def _run_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open EmptyOS", self._on_open, default=True),
            pystray.MenuItem("Capture Thought", self._on_capture),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart", self._on_restart),
            pystray.MenuItem("Quit", self._on_quit),
        )
        self._icon = pystray.Icon("EmptyOS", self._create_image(), "EmptyOS", menu)
        # setup= runs on its own thread once the icon exists, and replaces
        # pystray's default (which just makes it visible).
        self._icon.run(setup=self._visibility_loop)

    def _should_show(self) -> bool:
        """Visible unless a desktop shell's tray is up for this daemon's port."""
        if not bool(self.config("defer_to_shell", True)):
            return True
        port = int(self.kernel.config.get("network.port", 9000) or 9000)
        return not shell_tray_present(port)

    def _sync_visibility(self, icon) -> None:
        show = self._should_show()
        deferring = not show
        if deferring != self._deferring:  # log the change once, not every poll
            self._deferring = deferring
            print("[Tray] desktop shell attached — hiding the daemon tray icon" if deferring
                  else "[Tray] desktop shell gone — showing the daemon tray icon")
        icon.visible = show

    def _visibility_loop(self, icon) -> None:
        while not self._stop.is_set():
            try:
                self._sync_visibility(icon)
            except Exception:
                icon.visible = True  # a broken probe must never make the tray vanish
            self._stop.wait(DEFER_POLL_S)

    def _on_open(self):
        # Open the chromeless --app window (the daily driver) rather than a
        # browser tab. Falls back to a tab if no Chromium browser is found.
        try:
            from emptyos.sdk.browser_window import open_app_window
            port = int(self.kernel.config.get("network.port", 9000) or 9000)
            # No auth_token in the URL (argv/history leak) — the shared isolated
            # profile persists the session cookie after a one-time login.
            url = f"http://127.0.0.1:{port}/"
            profile = self._project_root() / "data" / "launcher-profile"
            open_app_window(url, width=1440, height=900, profile_dir=profile)
        except Exception:
            webbrowser.open("http://localhost:9000/")

    def _on_capture(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.kernel.events.emit("tray:capture_clicked", {}), self._loop
            )

    def _project_root(self) -> Path:
        # plugins/system-tray/plugin.py -> project root
        return Path(__file__).resolve().parents[2]

    def _shutdown(self, relaunch: bool = False):
        self._stop.set()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        # Run kernel.stop() in the daemon's loop to flush state cleanly.
        if self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.kernel.stop(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
        if relaunch:
            # Spawn restart.bat in a NEW console so its `start /b ...` invocations
            # (Ollama, ComfyUI, voice-api, pronounce) have a real parent console
            # to attach to. Without that, cmd.exe's `start` builtin fires children
            # with broken stdio inheritance and they often die with the bat.
            #
            # CREATE_BREAKAWAY_FROM_JOB lets the bat survive this daemon's death
            # (we os._exit immediately after Popen returns). CREATE_NEW_CONSOLE
            # already implies process-group breakaway, so we don't pair it with
            # DETACHED_PROCESS (which conflicts).
            #
            # stdout/stderr go to data/daemon-restart.log so failures inside the
            # bat (port conflicts, missing deps, boot exceptions) are diagnosable
            # — previously they vanished into DEVNULL.
            root = self._project_root()
            bat = root / "restart.bat"
            if bat.exists():
                log_path = root / "data" / "daemon-restart.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    log_fh = open(log_path, "ab", buffering=0)
                except Exception:
                    log_fh = subprocess.DEVNULL

                flags = 0
                if os.name == "nt":
                    flags = (
                        getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                    )
                try:
                    subprocess.Popen(
                        ["cmd.exe", "/c", str(bat)],
                        cwd=str(root),
                        creationflags=flags,
                        close_fds=True,
                        stdin=subprocess.DEVNULL,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                    )
                finally:
                    # Popen duplicates the handle; we can close our reference.
                    if log_fh is not subprocess.DEVNULL:
                        try:
                            log_fh.close()
                        except Exception:
                            pass
        # _exit, not sys.exit: this callback runs on pystray's native thread;
        # sys.exit would only kill that thread, leaving the daemon alive.
        os._exit(0)

    def _on_restart(self):
        self._shutdown(relaunch=True)

    def _on_quit(self):
        self._shutdown(relaunch=False)
