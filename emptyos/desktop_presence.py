"""Is an EmptyOS Desktop tray attached to this daemon? — stdlib only.

The native desktop shell (``products/_shared/shell.py``) holds a per-port named
mutex **for as long as its own tray icon is running** — not merely while the
process exists. The daemon's ``plugins/system-tray`` hides its icon while that
mutex is held, so the user sees exactly one EmptyOS tray icon: the shell's when
there is one, the daemon's otherwise. The shell claims it only once its icon
reports being on screen, and releases it if that icon later goes away, so a
shell without a tray (no pystray) never hides the daemon's icon. (pystray does
not surface a failed ``Shell_NotifyIcon`` add, so "on screen" is pystray's word
for it; pystray re-adds the icon when Explorer restarts.)

This module is that name and the probe, in a place both sides can import: the
shell runs from its own venv and must never import ``emptyos.sdk``, and the
daemon's plugins need the probe without the shell's code. Top-level and
stdlib-only for the same reason as ``headless.py`` and ``nethost.py``.

**Why a mutex, not a heartbeat or an ``/api`` flag.** The kernel releases a
mutex when its owning process ends — crash included — so a shell that died
cannot keep the daemon's icon hidden, and the probe needs no network, no auth
and no lease. (A shell that is *hung* but alive still holds it; its tray icon is
still on screen in that case, served by its own thread.) An HTTP flag would need
all three and would lie after a crash.
"""

from __future__ import annotations

import sys

SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5


def tray_presence_name(port: int) -> str:
    """The named mutex a shell holds while its tray icon for ``port`` is up.

    Per-port, so a shell attached to :9000 never silences a sandbox member's
    tray, and ``Local\\`` so it is scoped to the interactive session.
    """
    return f"Local\\EmptyOS.Desktop.tray-{int(port)}"


def quick_presence_name(port: int) -> str:
    """The named mutex a shell holds while **its global hotkey is registered**.

    Separate from the tray name on purpose: the two stand-downs are unrelated.
    A shell may have a tray and no hotkey (the shortcut was already taken by
    another program) or a hotkey and no tray (pystray missing), and each plugin
    must defer only to the thing that actually replaced it.

    ``plugins/command-launcher`` checks this before spawning its own window, so
    one press opens one surface. The daemon's hook-based listener still *fires*
    — nothing can reserve a combination away from a low-level keyboard hook — so
    the deference has to be the launcher declining to act, not the hotkey
    failing to arrive.
    """
    return f"Local\\EmptyOS.Desktop.quick-{int(port)}"


def presence_from_open(handle: int, error: int) -> bool:
    """What an ``OpenMutexW`` result means. Access denied still means *it exists*:
    a shell running at a different integrity level than the daemon creates a
    mutex the daemon may not open, and reading that as "absent" would put two
    tray icons on screen."""
    return bool(handle) or error == ERROR_ACCESS_DENIED


def _mutex_present(name: str) -> bool:
    """Whether a named mutex exists right now. Always False off Windows."""
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    error = ctypes.get_last_error()
    if handle:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    return presence_from_open(handle or 0, error)


def shell_tray_present(port: int) -> bool:
    """True while a desktop shell's tray is up for ``port``."""
    return _mutex_present(tray_presence_name(port))


def shell_quick_present(port: int) -> bool:
    """True while a desktop shell holds the global hotkey for ``port``."""
    return _mutex_present(quick_presence_name(port))
