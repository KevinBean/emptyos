"""Command Launcher — opens the command palette in a borderless app window on global hotkey.

Subscribes to `hotkey:pressed` (emitted by plugins/global-hotkey/). On each press:
  - If our previously-spawned window is alive, terminate it (toggle off).
  - Otherwise, spawn Chrome/Edge in `--app=` mode pointing at the configured target
    (`[plugins.command-launcher] target`, default "/hub/") with `?launcher=1`.

We don't try to cross-process focus on Windows (needs win32 APIs); a kill-and-respawn
toggle is good enough for v1 and matches the close-on-Esc behavior wired into
eos-keys.js's launcher-mode (window.close() runs inside the page).

`target` is configurable rather than hardcoded so a user whose "start anything"
surface is `portal` (the unified conversation door), not `hub` (the app
dashboard), can point the global hotkey there without a code change:
    [plugins.command-launcher]
    target = "/portal/"
Default stays "/hub/" — flagged in gap analysis (portal-not-in-global-launcher)
that Portal's entire pitch is one-keystroke reach, but flipping the shipped
default would silently change every existing user's hotkey behavior.

**Stands down while an EmptyOS Desktop shell holds the same shortcut** — its
quick-entry window is the same idea done better (preloaded, not respawned per
press). Presence is a per-port named mutex the shell holds only while its hotkey
is actually registered (``emptyos/desktop_presence.py``), so a shortcut another
program already took never leaves the user with no launcher at all.
``[plugins.command-launcher] defer_to_shell = false`` keeps this one regardless.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from emptyos.sdk import BasePlugin
from emptyos.sdk.browser_window import find_chromium, open_app_window


class CommandLauncherPlugin(BasePlugin):
    name = "command-launcher"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._proc: subprocess.Popen | None = None
        self._unsubscribe = None
        self._browser: tuple[str, str] | None = None

    async def connect(self):
        self._browser = find_chromium()
        if not self._browser:
            print("[CommandLauncher] No Chromium browser found — launcher disabled.")
            return

        self._unsubscribe = self.kernel.events.on("hotkey:pressed", self._on_hotkey)
        print(f"[CommandLauncher] Listening on hotkey:pressed (browser: {self._browser[1]})")

    async def disconnect(self):
        if self._unsubscribe:
            self._unsubscribe()
        self._close_window()

    async def available(self) -> bool:
        return self._browser is not None

    def _window_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _close_window(self):
        if self._window_alive():
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception as e:
                print(f"[CommandLauncher] terminate failed: {e}")
        self._proc = None

    def _spawn_window(self):
        if not self._browser:
            return

        host = self.config("host", "127.0.0.1")
        port = self.config("port", 9000)
        # /hub/ default rather than / because the daemon's / does a 302 redirect to
        # /hub/ which strips query params — IS_LAUNCHER would then be false in the
        # loaded page. `target` must be an absolute path with a trailing slash for
        # the same reason (any app's own index route behaves the same way).
        target = str(self.config("target", "/hub/") or "/hub/")
        if not target.startswith("/"):
            target = "/" + target
        if not target.endswith("/"):
            target = target + "/"
        url = f"http://{host}:{port}{target}?launcher=1"
        # No auth_token in the URL — that leaks the long-lived credential into
        # the browser argv + history. The shared isolated profile persists the
        # session cookie after a one-time login instead.
        width = self.config("width", 720)
        height = self.config("height", 480)

        # Per-launcher user data dir keeps the app window isolated from the user's
        # main Chrome profile (no logged-in accounts, no extensions). Lives under
        # the EmptyOS project's data/ dir so it survives restarts but isn't in vault.
        # Shared with scripts/eos_desktop.py so one login serves both surfaces.
        project_root = Path(__file__).resolve().parents[2]
        user_data = project_root / "data" / "launcher-profile"

        self._proc = open_app_window(
            url, browser=self._browser, width=width, height=height,
            profile_dir=user_data,
        )

    def _shell_owns_hotkey(self) -> bool:
        """Whether a desktop shell's own quick-entry window answers this press.

        Nothing can reserve a key combination away from the ``keyboard``
        package's low-level hook, so both listeners fire on the same press and
        the user would get two windows. Standing down is therefore the launcher
        declining to act, not the event failing to arrive.

        Checked per press, never cached: the shell comes and goes independently
        of the daemon, and the mutex is released even if it crashes.
        """
        if not self.config("defer_to_shell", True):
            return False
        try:
            from emptyos.desktop_presence import shell_quick_present

            return shell_quick_present(int(self.config("port", 9000)))
        except Exception:
            return False  # never let a probe failure cost the user their launcher

    async def _on_hotkey(self, event):
        # Event handler runs on the daemon's main loop. Subprocess ops are
        # blocking-but-fast — running them inline is fine (no need for to_thread).
        if self._shell_owns_hotkey():
            # A window this plugin spawned before the shell came up would
            # otherwise be stranded with no way to toggle it closed.
            self._close_window()
            return
        if self._window_alive():
            self._close_window()
        else:
            self._spawn_window()
