"""Global Hotkey Plugin — integrates system-wide shortcuts into EmptyOS."""

from __future__ import annotations

import asyncio

from emptyos.sdk import BasePlugin

try:
    import keyboard

    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


class GlobalHotkeyPlugin(BasePlugin):
    name = "global-hotkey"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._loop = None

    async def connect(self):
        if not HAS_KEYBOARD:
            print("[Hotkey] 'keyboard' library not installed. Run: pip install keyboard")
            return

        self._loop = asyncio.get_running_loop()

        # We read the shortcut from config, defaulting to ctrl+space
        shortcut = self.config("shortcut", "ctrl+space")

        try:
            # keyboard library hooks into OS events natively
            keyboard.add_hotkey(shortcut, self._on_hotkey, args=(shortcut,))
            print(f"[Hotkey] Bound global shortcut: {shortcut}")
        except Exception as e:
            print(f"[Hotkey] Failed to bind shortcut: {e}")

    async def disconnect(self):
        if HAS_KEYBOARD:
            try:
                keyboard.unhook_all()
            except Exception:
                pass

    async def available(self) -> bool:
        return HAS_KEYBOARD

    def _on_hotkey(self, shortcut_name: str):
        """Called by the keyboard listener thread when shortcut is pressed."""
        if not self._loop:
            return

        print(f"[Hotkey] Triggered: {shortcut_name}")
        # Safely emit the event back into the main asyncio loop
        asyncio.run_coroutine_threadsafe(
            self.kernel.events.emit("hotkey:pressed", {"key": shortcut_name}), self._loop
        )
