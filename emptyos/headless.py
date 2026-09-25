"""Headless-process console guard — stdlib only, importable without the kernel.

A Windows process with **no console at all** — the daemon the watchdog
respawns with ``DETACHED_PROCESS``, or a ``python -m emptyos start`` from any
GUI parent — passes that condition on to every console child it spawns, and
Windows then *allocates a fresh console* for each of them, with a window.
With Windows Terminal registered as the default terminal host, every such
child is a new tab. Measured on this box (2026-09-06): an unflagged child of a
DETACHED parent creates one conhost and reports a non-zero console HWND.

A ``CREATE_NO_WINDOW`` process (a sandbox-pool member, the dogfood ``:9001``
sidecar) is a different case: it *has* a console, just one with no window
(``GetConsoleCP()`` answers non-zero, ``GetConsoleWindow()`` answers 0), and
its children inherit it — measured: zero new conhosts. That distinction is
why ``has_console()`` probes the code page and not the window, and why the
guard must stay a no-op there: forcing ``CREATE_NO_WINDOW`` onto a child that
would have inherited adds a conhost per spawn and moves the child out of the
console group ``CTRL_BREAK_EVENT`` targets.

Three hard freezes (2026-08-01, 08-15, 09-06 01:33 — Kernel-Power 41) and a
fourth storm the same night (02:07) that was caught live all followed a
DETACHED respawn: 17 min, 4 min, 75 min and 3 min later. Process auditing
(Event 4688) on the 09-06 01:33 storm counted 7,787 console hosts created in
18 minutes, 4,890 of them with ``git.exe`` as the client; 2,960 of the
resolved clients had one grandparent, pid 74324 — the daemon the watchdog had
respawned detached at 00:18 — consistent with the single most frequent client
command, ``git rev-parse --abbrev-ref HEAD`` ×2,909, which is the
``api_status`` poll behind the fix-agent / app-builder pages. The hosts held
~20 GB, then commit ran out. Why the tabs did not drain as fast as they
arrived is inferred, not measured: a tab whose process exits non-zero stays
open (``closeOnExit`` unset → ``automatic``), and under a burst Windows
Terminal falls behind on the ones that do close.

About a hundred ``subprocess`` / ``create_subprocess_exec`` sites in
daemon-side code carry no window flag, and third-party libraries add more.
Flagging each one is a losing race, so this guard sets the rule once, at the
process level:

    if THIS process has no console, no child it spawns may open one.

``install_headless_subprocess_guard()`` wraps ``subprocess.Popen.__init__``
so a call that expresses no console intent gets ``CREATE_NO_WINDOW`` added.
asyncio's Proactor subprocess transport builds on ``subprocess.Popen``
(``asyncio.windows_utils.Popen`` is a subclass and forwards ``creationflags``
as a keyword), so ``create_subprocess_exec`` is covered by the same patch. A
caller that DID choose — ``CREATE_NEW_CONSOLE``, ``DETACHED_PROCESS`` or
``CREATE_NO_WINDOW`` already present — is left alone.

The guard is a no-op when the process has a console (a ``restart.bat``
foreground daemon, a developer shell, a ``CREATE_NO_WINDOW`` member):
children inherit that console and never open a window anyway.

Lives at the top level rather than under ``emptyos/sdk/`` for the same reason
as ``nethost.py`` and ``proactor_guard.py``: it must be importable at the very
top of ``eos start``, before the kernel (and anything it auto-starts) exists,
and by a unit test that never runs the SDK package's ``__init__``. Stdlib only
— keep it that way.
"""

from __future__ import annotations

import subprocess
import sys

# Win32 process-creation flags (subprocess exposes them only on Windows; the
# pure helpers below must import everywhere so the unit test runs on any OS).
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

_CONSOLE_INTENT = CREATE_NEW_CONSOLE | DETACHED_PROCESS | CREATE_NO_WINDOW

# ``subprocess.Popen(args, bufsize, executable, stdin, stdout, stderr,
# preexec_fn, close_fds, shell, cwd, env, universal_newlines, startupinfo,
# creationflags, ...)`` — creationflags is the 14th parameter, so index 13
# of the positional args after ``self``. Nobody passes it positionally, but
# a guard that could be bypassed by an unusual call shape is not a guard.
_CREATIONFLAGS_POS = 13

_installed = False


def no_window_flags() -> int:
    """The ``creationflags`` value that suppresses a console window.

    ``CREATE_NO_WINDOW`` on Windows, ``0`` elsewhere — safe to pass to
    ``subprocess.run`` / ``create_subprocess_exec`` unconditionally, since
    POSIX ``Popen`` accepts ``creationflags=0`` and rejects anything else.
    Use it at a call site that runs from console-less contexts even when the
    process-level guard is not installed (a script importing the SDK).
    """
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def hidden_creationflags(flags: int) -> int:
    """Pure: return ``flags`` with ``CREATE_NO_WINDOW`` added unless the caller
    already expressed a console intent (new console, detached, or no window)."""
    if flags & _CONSOLE_INTENT:
        return flags
    return flags | CREATE_NO_WINDOW


def has_console() -> bool:
    """True when this process is attached to a console (Windows), or always
    on other platforms, where a child cannot pop a window.

    Probes ``GetConsoleCP``, which is 0 exactly when no console is attached.
    ``GetConsoleWindow`` is the wrong probe: it is also 0 for a
    ``CREATE_NO_WINDOW`` process, which HAS a console (children inherit it)
    — using it would install the guard in every sandbox-pool member.
    """
    if sys.platform != "win32":
        return True
    import ctypes

    return bool(ctypes.windll.kernel32.GetConsoleCP())


def wrap_popen_init(orig):
    """Pure factory: the wrapper the guard installs over ``Popen.__init__``.

    Separated from ``install_*`` so a unit test can drive it against a fake
    ``orig`` and assert exactly what reaches the real constructor.
    """

    def __init__(self, *args, **kwargs):
        if "creationflags" in kwargs:
            kwargs["creationflags"] = hidden_creationflags(int(kwargs["creationflags"] or 0))
        elif len(args) > _CREATIONFLAGS_POS:
            args = list(args)
            args[_CREATIONFLAGS_POS] = hidden_creationflags(int(args[_CREATIONFLAGS_POS] or 0))
        else:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return orig(self, *args, **kwargs)

    __init__.__eos_headless_guard__ = True  # type: ignore[attr-defined]
    return __init__


def install_headless_subprocess_guard(*, force: bool = False) -> bool:
    """Install the guard if this is a console-less Windows process.

    Returns True when the guard is active after the call (installed now or
    earlier), False when it does not apply (non-Windows, or the process has a
    console and ``force`` is not set). Idempotent.
    """
    global _installed
    if sys.platform != "win32":
        return False
    if is_installed():
        return True
    if not force and has_console():
        return False
    subprocess.Popen.__init__ = wrap_popen_init(subprocess.Popen.__init__)  # type: ignore[method-assign]
    _installed = True
    return True


def is_installed() -> bool:
    """Whether the guard is on the LIVE ``Popen.__init__`` — read off the
    attribute, not a module flag, so a later reassignment (a monkeypatch, a
    reload) that silently removed the wrapper reads as not installed."""
    return getattr(subprocess.Popen.__init__, "__eos_headless_guard__", False) is True
