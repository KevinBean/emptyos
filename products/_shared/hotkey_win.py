"""Global hotkey for the desktop shell — ``RegisterHotKey`` on its own thread.

The quick-entry window (Ctrl+Space by default) needs a shortcut that fires while
EmptyOS has no focus at all. Two mechanisms exist on Windows and they are not
interchangeable:

- **``RegisterHotKey``** (this module) asks the OS to reserve the combination and
  post ``WM_HOTKEY`` to one thread. It is a *reservation*: exactly one process
  can hold a combination, so a second registrant fails loudly rather than both
  firing. It sees no other keystroke — nothing to filter, nothing to log.
- **A low-level keyboard hook** (what the daemon's ``global-hotkey`` plugin uses
  via the ``keyboard`` package) watches *every* key system-wide and decides in
  Python whether this one matched. Nothing reserves anything, so two listeners
  both fire on the same press — which is exactly why the shell must make the
  daemon's launcher stand down (``emptyos/desktop_presence.py``) rather than
  relying on one of them losing a race.

The reservation is the reason this module exists in the shell rather than reusing
the plugin: a hook that reads every keystroke in the user's session is a large
thing to run for one shortcut, and it cannot tell you the shortcut was already
taken.

**Two limits worth stating plainly, because neither is a bug to be fixed here.**
A combination already held by another process cannot be registered — the thread
reports that and the shell says so, instead of a hotkey that silently does
nothing. And User Interface Privilege Isolation means a non-elevated process
receives no hotkey while an *elevated* window has focus; the shell would have to
run elevated to change that, which is a far worse trade for a text box.

**Import discipline — stdlib only** (``ctypes``), like every module the shell's
own venv loads, and importable on **any** platform: ``ctypes.wintypes`` is
reached inside :meth:`HotkeyThread._run`, past the platform guard, never at
module scope (see the note beside the imports). Nothing here imports ``webview``
either, so the parsing half is testable anywhere and the thread half on any
Windows box.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Callable

# NOTE: `ctypes.wintypes` is deliberately NOT imported here. It defines
# `VARIANT_BOOL` with the type code "v", which exists only in the Windows build
# of `_ctypes` — so the import raises ValueError on POSIX, and a module-scope
# one would abort `pytest --collect-only` on macOS/Linux, which CI runs first
# and which fails the WHOLE suite on a single collection error
# (`.claude/rules/testing.md`). It is imported inside `_run`, past the
# platform guard, which is the only place it is needed.

#: Win32 modifier bits (``RegisterHotKey``'s ``fsModifiers``).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
#: Without this, HOLDING the combination repeats at the keyboard's repeat rate —
#: a quick window that toggles would flicker open/closed while the keys are down.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

#: The one hotkey id this thread registers. Ids are per-thread, so a fixed value
#: cannot collide with another process's.
_HOTKEY_ID = 1

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "option": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "command": MOD_WIN, "cmd": MOD_WIN,
}

#: Named keys. Letters, digits and F1-F24 are derived below rather than listed,
#: so the table holds only what has no rule.
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse_shortcut(shortcut: object) -> tuple[int, int] | None:
    """``"ctrl+space"`` → ``(MOD_CONTROL | MOD_NOREPEAT, 0x20)``, or ``None``.

    Refuses a combination with **no modifier**. A bare ``RegisterHotKey`` on a
    plain key reserves it process-wide for the whole session: registering
    ``space`` would take the space bar away from every other program until the
    shell exits. There is no configuration worth that, so the parser will not
    produce one.

    Aliases follow the daemon plugin's ``normalize_shortcut`` (``control``→ctrl,
    ``option``→alt, ``command``/``windows``/``super``→win) so one spelling works
    in both places; a user moving the shortcut between ``[plugins.global-hotkey]``
    and ``[desktop]`` should not have to learn a second vocabulary.
    """
    if not isinstance(shortcut, str):
        return None
    parts = [p.strip().casefold() for p in shortcut.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    key: int | None = None
    for part in parts:
        bit = _MODIFIERS.get(part)
        if bit is not None:
            mods |= bit
            continue
        if key is not None:
            return None  # two non-modifier keys: not a shortcut
        key = _key_code(part)
        if key is None:
            return None
    # ``not mods`` is belt-and-braces, and stated so nobody deletes it as dead
    # code: no input reaches it today, because ≥2 parts with no modifier means
    # ≥2 keys (refused above) and <2 parts is refused at the top. It is kept
    # because it is the rule the docstring states — if either of those two
    # branches is ever relaxed, this is what still refuses a bare key.
    if key is None or not mods:
        return None
    return mods | MOD_NOREPEAT, key


def _key_code(name: str) -> int | None:
    if len(name) == 1:
        ch = name.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    if name.startswith("f") and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1  # VK_F1 .. VK_F24
    return _NAMED_KEYS.get(name)


class HotkeyThread:
    """Holds one global shortcut and calls ``on_press`` when it fires.

    The registration, the message loop and the unregistration all happen on this
    thread, because ``RegisterHotKey`` binds the hotkey to the calling *thread*
    and ``WM_HOTKEY`` is posted to that thread's own queue — a message with no
    window, which is why the loop passes a NULL ``hWnd`` to ``GetMessageW``.

    ``on_press`` runs **on the message thread**, so it must return promptly: while
    it runs, no further hotkey is dispatched. Callers hand off anything slow.
    """

    def __init__(self, shortcut: str, on_press: Callable[[], None],
                 log: Callable[[str], None] = lambda _m: None):
        # Deliberately NOT ``shortcut or DEFAULT_SHORTCUT``. The default belongs
        # to config resolution (``eos_desktop.desktop_config_from_dict``), and a
        # second place deciding it means an empty value — which is how the whole
        # feature says "off" — would register Ctrl+Space anyway. Empty simply
        # fails to parse, which is the honest answer.
        self.shortcut = shortcut
        self._on_press = on_press
        self._log = log
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._error: str | None = None
        self._registered = False

    # -- lifecycle -------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        """Register and begin dispatching. ``False`` (with :attr:`error` set)
        when the platform, the spelling or another process says no."""
        if sys.platform != "win32":
            self._error = "global hotkeys are Windows-only in this shell"
            return False
        combo = parse_shortcut(self.shortcut)
        if combo is None:
            self._error = (f"{self.shortcut!r} is not a usable shortcut — "
                           "it needs at least one modifier, e.g. ctrl+space")
            return False
        self._thread = threading.Thread(target=self._run, args=combo,
                                        name="shell-hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            # A timeout must SAY so. Without this the caller logs "off: None"
            # and, worse, has no reason to keep the handle — while the thread
            # may still register a moment later, leaving a live hotkey nobody
            # owns and nobody can stop.
            self._error = f"timed out after {timeout}s waiting to register {self.shortcut}"
        return self._registered

    @property
    def error(self) -> str | None:
        return self._error

    def is_registered(self) -> bool:
        """Whether the hotkey is live **right now**. False once the message loop
        ends for any reason, which is what lets a caller notice that its
        "a shell owns this shortcut" claim has stopped being true."""
        return self._registered

    def stop(self) -> None:
        """Ask the message loop to end. ``PostThreadMessageW`` rather than a flag:
        ``GetMessageW`` blocks until a message arrives, so a flag would not be read
        until the next keypress — which may never come."""
        tid = self._tid
        if not tid:
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)

    # -- the thread ------------------------------------------------------------
    def _run(self, mods: int, vk: int) -> None:
        from ctypes import wintypes  # Windows-only; see the note beside the imports

        # use_last_error=True, and it is load-bearing: ``ctypes.windll.user32``
        # is the cached no-error-capture handle, so ``get_last_error()`` after a
        # call through it reads ctypes' own copy — which nothing ever wrote, and
        # which is therefore always 0. Registering a combination another program
        # holds would then report "error 0" instead of the one failure the user
        # can actually do something about.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
            err = ctypes.get_last_error()
            # 1409 is ERROR_HOTKEY_ALREADY_REGISTERED — by far the likeliest, and
            # the only one whose remedy is the user's rather than ours.
            self._error = (f"{self.shortcut} is already held by another program"
                           if err == 1409 else
                           f"could not register {self.shortcut} (error {err})")
            self._ready.set()
            return
        self._registered = True
        self._ready.set()
        self._log(f"hotkey {self.shortcut} registered")
        try:
            msg = wintypes.MSG()
            # GetMessageW returns 0 on WM_QUIT and -1 on error; anything else is a
            # message. Looping on truthiness alone would spin forever on -1.
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    self._dispatch()
        finally:
            self._registered = False
            try:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass
            self._log(f"hotkey {self.shortcut} released")

    def _dispatch(self) -> None:
        try:
            self._on_press()
        except Exception as e:
            # One bad press must not end the loop — the hotkey would then be
            # registered (so nothing else can take it) and dead.
            self._log(f"hotkey handler failed: {e!r}")
