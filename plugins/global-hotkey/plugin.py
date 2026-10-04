"""Global Hotkey Plugin — integrates system-wide shortcuts into EmptyOS."""

from __future__ import annotations

import asyncio
import sys
import threading

from emptyos.sdk import BasePlugin

try:
    # Never import `keyboard` on macOS. Its Darwin backend builds a
    # KeyController at module scope (_darwinkeyboard.py), whose KeyMap calls
    # Carbon CFDataGetBytes on a keyboard layout that can come back NULL --
    # a SIGBUS in C, which is a fatal signal rather than a catchable
    # exception, so the `except` below cannot guard it. It takes down the
    # whole process: importing this plugin aborted every `pytest --collect-only`
    # run on macOS, and would kill the daemon if the plugin were enabled there.
    # Windows and Linux are fine (Linux raises a normal ImportError as non-root).
    if sys.platform == "darwin":
        raise ImportError("keyboard's macOS backend SIGBUSes at import")

    import keyboard

    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


class GlobalHotkeyPlugin(BasePlugin):
    name = "global-hotkey"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._loop = None
        self._bindings: dict[str, dict] = {}
        self._shortcut_owners: dict[str, str] = {}
        self._bindings_lock = threading.RLock()

    async def connect(self):
        if not HAS_KEYBOARD:
            print("[Hotkey] 'keyboard' library not installed. Run: pip install keyboard")
            return

        self._loop = asyncio.get_running_loop()

        # We read the shortcut from config, defaulting to ctrl+space
        shortcut = self.config("shortcut", "ctrl+space")

        result = self.register_hotkey("global-hotkey.default", shortcut)
        if result.get("ok"):
            print(f"[Hotkey] Bound global shortcut: {result['shortcut']}")
        else:
            print(f"[Hotkey] Failed to bind shortcut: {result.get('error', 'unknown error')}")

    async def disconnect(self):
        for owner in list(self._bindings):
            self.unregister_owner(owner)
        await super().disconnect()

    async def available(self) -> bool:
        return HAS_KEYBOARD

    @staticmethod
    def normalize_shortcut(shortcut: str) -> str:
        aliases = {"control": "ctrl", "option": "alt", "windows": "win",
                   "command": "win"}
        parts = [aliases.get(p.strip().casefold(), p.strip().casefold())
                 for p in str(shortcut or "").split("+") if p.strip()]
        return "+".join(parts)

    def register_hotkey(self, owner: str, shortcut: str, payload: dict | None = None) -> dict:
        """Register one owner-scoped binding without disturbing other owners."""
        if not HAS_KEYBOARD:
            return {"ok": False, "error": "keyboard library not installed"}
        owner = str(owner or "").strip()
        normalized = self.normalize_shortcut(shortcut)
        if not owner or len(normalized.split("+")) < 2:
            return {"ok": False, "error": "owner and a modifier shortcut are required"}
        with self._bindings_lock:
            conflict = self._shortcut_owners.get(normalized)
            if conflict and conflict != owner:
                return {"ok": False, "error": "shortcut already registered",
                        "conflict_owner": conflict, "shortcut": normalized}
            existing = self._bindings.get(owner)
            if existing and existing["shortcut"] == normalized:
                existing["payload"] = dict(payload or {})
                return {"ok": True, "owner": owner, "shortcut": normalized,
                        "already_registered": True}
            if existing:
                self.unregister_owner(owner)
            try:
                handle = keyboard.add_hotkey(
                    normalized, self._on_hotkey,
                    args=(owner, normalized, dict(payload or {})),
                )
            except Exception as e:
                return {"ok": False, "error": f"could not register shortcut: {e}"}
            self._bindings[owner] = {"shortcut": normalized, "handle": handle,
                                     "payload": dict(payload or {})}
            self._shortcut_owners[normalized] = owner
        return {"ok": True, "owner": owner, "shortcut": normalized}

    def unregister_owner(self, owner: str) -> dict:
        """Remove only this owner's hook; never invokes ``unhook_all``."""
        with self._bindings_lock:
            binding = self._bindings.pop(str(owner), None)
            if binding is None:
                return {"ok": True, "removed": False}
            self._shortcut_owners.pop(binding["shortcut"], None)
            try:
                keyboard.remove_hotkey(binding["handle"])
            except Exception as e:
                return {"ok": False, "removed": True, "error": str(e)}
        return {"ok": True, "removed": True, "shortcut": binding["shortcut"]}

    def bindings(self) -> list[dict]:
        with self._bindings_lock:
            return [{"owner": owner, "shortcut": row["shortcut"]}
                    for owner, row in sorted(self._bindings.items())]

    def _on_hotkey(self, owner: str, shortcut_name: str, payload: dict | None = None):
        """Called by the keyboard listener thread when shortcut is pressed."""
        if not self._loop:
            return

        print(f"[Hotkey] Triggered: {shortcut_name}")
        # Safely emit the event back into the main asyncio loop
        asyncio.run_coroutine_threadsafe(
            self.kernel.events.emit("hotkey:pressed", {
                "key": shortcut_name, "owner": owner, **dict(payload or {}),
            }), self._loop
        )
