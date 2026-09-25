"""Single-instance lock + second-launch hand-off for the desktop shell (Windows).

Double-clicking EmptyOS while it is already open must focus the open window,
not start a second shell with a second WebView2 profile lock. Two pieces:

- a **named mutex** decides who is primary (``CreateMutexW`` +
  ``ERROR_ALREADY_EXISTS``). The kernel releases it when the owning process
  ends, crash included, so a dead shell never blocks the next launch;
- a **named pipe** (``multiprocessing.connection`` ``AF_PIPE``) carries the
  second launch's request to the primary.

**What the pipe accepts, and why that is the boundary.** The default pipe DACL
lets SYSTEM, Administrators and the owner write; the stdlib does not set
``PIPE_REJECT_REMOTE_CLIENTS``, so an SMB logon holding the owner's credentials
could connect too. So the pipe is treated as untrusted input: messages travel
as **JSON bytes, never pickles** (``Connection.recv()`` unpickles — code
execution for any writer), capped at :data:`MAX_MESSAGE_BYTES`, and every one
goes through :func:`shell_core.validate_ipc`, whose whole vocabulary is "show,
optionally at this same-origin path". The worst a writer can do is bring the
window forward and open a daemon page in it — a GET the user's own window
could make, with nothing but a path the writer chose.

Off Windows both degrade to "always primary, nothing to send", which is the
behaviour a platform without this feature should have.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable

from .shell_core import validate_ipc

ERROR_ALREADY_EXISTS = 183
ASFW_ANY = 0xFFFFFFFF

#: A "show" message is ~60 bytes; a 2 KB path is the largest legal one.
MAX_MESSAGE_BYTES = 4096

#: A client that connects and sends nothing is dropped after this, so it can't
#: hold the (serial) accept loop and make every later second launch time out.
RECV_TIMEOUT_S = 2.0


class InstanceLock:
    """A held named mutex. ``acquire`` returns ``None`` when another process holds it."""

    def __init__(self, handle: int):
        self._handle = handle
        self._lock = threading.Lock()

    @classmethod
    def acquire(cls, name: str) -> InstanceLock | None:
        if sys.platform != "win32":
            return cls(0)
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return None
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return None
        return cls(handle)

    def release(self) -> None:
        """Idempotent and thread-safe: two releasers must never close one handle
        twice — by then its value may belong to something else."""
        with self._lock:
            handle, self._handle = self._handle, 0
        if handle and sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def decode_message(raw: bytes) -> dict | None:
    """Bytes off the pipe → a validated message, or ``None``. Never unpickles."""
    try:
        return validate_ipc(json.loads(raw.decode("utf-8")))
    except Exception:
        return None


def serve(address: str, on_message: Callable[[dict], None]) -> Callable[[], None]:
    """Accept second-launch messages on a daemon thread. Returns a stop function.

    Raises if the pipe cannot be created (e.g. another user squatting the name);
    the caller decides whether the window can live without hand-off.
    """
    if sys.platform != "win32":
        return lambda: None
    from multiprocessing.connection import Listener

    listener = Listener(address=address, family="AF_PIPE")
    stopped = threading.Event()

    def loop() -> None:
        while not stopped.is_set():
            try:
                conn = listener.accept()
            except Exception:
                if stopped.is_set():
                    return
                time.sleep(0.2)  # a pipe that keeps failing must not spin a core
                continue
            msg = None
            try:
                if conn.poll(RECV_TIMEOUT_S):
                    msg = decode_message(conn.recv_bytes(maxlength=MAX_MESSAGE_BYTES))
            except Exception:
                msg = None  # oversized, malformed, or the client went away
            finally:
                conn.close()
            if msg is not None:
                try:
                    on_message(msg)
                except Exception:
                    pass

    threading.Thread(target=loop, name="shell-ipc", daemon=True).start()

    def stop() -> None:
        stopped.set()
        try:
            listener.close()
        except Exception:
            pass

    return stop


def send(address: str, msg: dict) -> bool:
    """Deliver ``msg`` to the primary. ``False`` when nobody is listening.

    Grants foreground rights first: the second launch was started by the user,
    so it holds them, and passing them on is what lets the primary's window
    actually come to the front instead of just flashing in the taskbar.
    """
    if sys.platform != "win32" or validate_ipc(msg) is None:
        return False
    import ctypes
    from multiprocessing.connection import Client

    try:
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass
    try:
        conn = Client(address=address, family="AF_PIPE")
    except Exception:
        return False
    try:
        conn.send_bytes(json.dumps(msg).encode("utf-8"))
        return True
    except Exception:
        return False
    finally:
        conn.close()
