"""System Tray Plugin — puts EmptyOS in the native taskbar tray."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import webbrowser
from pathlib import Path

from emptyos.sdk import BasePlugin

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
        self._icon.run()

    def _on_open(self):
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
            # Spawn restart.bat fully detached so it outlives this process.
            # By exiting first, port 9000 is already free when the bat runs —
            # its kill-loop becomes a no-op and boot is faster.
            root = self._project_root()
            bat = root / "restart.bat"
            if bat.exists():
                flags = 0
                if os.name == "nt":
                    flags = (
                        getattr(subprocess, "DETACHED_PROCESS", 0)
                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                    )
                subprocess.Popen(
                    [str(bat)],
                    cwd=str(root),
                    creationflags=flags,
                    close_fds=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        # _exit, not sys.exit: this callback runs on pystray's native thread;
        # sys.exit would only kill that thread, leaving the daemon alive.
        os._exit(0)

    def _on_restart(self):
        self._shutdown(relaunch=True)

    def _on_quit(self):
        self._shutdown(relaunch=False)
