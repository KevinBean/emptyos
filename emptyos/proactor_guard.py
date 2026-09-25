"""Windows Proactor peer-reset guard — stdlib only, importable without the kernel.

CPython's ``_ProactorBasePipeTransport._call_connection_lost``
(``Lib/asyncio/proactor_events.py``) calls ``sock.shutdown(SHUT_RDWR)`` as the
**first statement of a bare ``finally:``**, with no exception guard::

    finally:
        if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
            self._sock.shutdown(socket.SHUT_RDWR)   # <-- raises here
        self._sock.close()                          #     never runs
        self._sock = None                           #     never runs
        server = self._server
        if server is not None:
            server._detach(self)                    #     never runs
            self._server = None
        self._called_connection_lost = True         #     never runs

When the peer has already reset the connection — routine on Windows whenever a
browser tab, a ``curl``, or a websocket client dies mid-request — ``shutdown``
raises ``ConnectionResetError: [WinError 10054]``. Two consequences, and only
the first one is cosmetic:

1. The exception escapes the ``call_soon`` callback to the loop's exception
   handler, printing ``Exception in callback
   _ProactorBasePipeTransport._call_connection_lost()`` plus a full traceback
   that names stdlib frames and gives the operator nothing to act on.
2. **The rest of the teardown is skipped.** The socket survives until GC
   finalizes it (a delayed close, and a pile-up under load), and the server's
   active-transport count never decrements — which is what can leave
   ``Server.wait_closed()`` waiting at shutdown.

The guard here wraps the method so a peer reset *finishes the teardown the
stdlib skipped*, and swallows only that exact shape. Anything else — including
an error raised by the protocol's own ``connection_lost`` — propagates
untouched.

This lives at the top level rather than under ``emptyos/sdk/`` for the same
reason as ``nethost.py`` and ``frontmatter.py``: it must be importable by a
unit test without running the SDK package's ``__init__`` (which pulls in
``base_app``) or opening a kernel syslog handle. Stdlib only — keep it that way.
"""

from __future__ import annotations

import functools
import sys

# WSAECONNABORTED / WSAECONNRESET / ERROR_NETNAME_DELETED. The third is the
# very condition the stdlib comment says the shutdown() call exists to avoid,
# so it shows up here when the race is lost rather than won.
_PEER_RESET_WINERRORS = frozenset({10053, 10054, 121})


def is_peer_reset(exc: BaseException) -> bool:
    """True for the OS errors that mean "the other end is already gone"."""
    if not isinstance(exc, OSError):
        return False
    return getattr(exc, "winerror", None) in _PEER_RESET_WINERRORS


def finish_transport_teardown(transport) -> None:
    """Run the cleanup CPython's ``finally`` skipped when ``shutdown()`` raised.

    Mirrors the statements after the ``shutdown`` call, in order, each
    individually defensive — this runs during teardown, where a second failure
    must not mask the first.
    """
    sock, transport._sock = getattr(transport, "_sock", None), None
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    server, transport._server = getattr(transport, "_server", None), None
    if server is not None:
        try:
            server._detach(transport)
        except Exception:
            pass
    transport._called_connection_lost = True


def guard_call_connection_lost(original):
    """Wrap a ``_call_connection_lost`` implementation with the reset guard.

    Pure — takes and returns a function, touches no global state — so both
    directions (swallow / re-raise) are unit-testable against a fake transport.
    """

    @functools.wraps(original)
    def _call_connection_lost(self, exc):
        try:
            return original(self, exc)
        except OSError as err:
            # ``_called_connection_lost`` is the last statement of that
            # ``finally``, so it is still False exactly when shutdown() blew up
            # partway through. If it is already True the finally ran to
            # completion and this error came from the protocol's own
            # connection_lost — not ours to swallow. Absent attribute is
            # treated as "not our shape" and re-raised.
            if not is_peer_reset(err) or getattr(self, "_called_connection_lost", True):
                raise
            finish_transport_teardown(self)
            return None

    _call_connection_lost._eos_reset_guard = True  # type: ignore[attr-defined]
    return _call_connection_lost


def install_proactor_reset_guard() -> bool:
    """Patch the live transport class. Idempotent.

    Returns True when this call installed the guard, and False when it was
    unnecessary or impossible: not Windows, already installed, or CPython moved
    the internals. A False return is never an error — the daemon runs exactly as
    it did before, noisy traceback included.
    """
    if sys.platform != "win32":
        return False
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport as transport_cls
    except Exception:
        return False

    original = getattr(transport_cls, "_call_connection_lost", None)
    if original is None or getattr(original, "_eos_reset_guard", False):
        return False

    transport_cls._call_connection_lost = guard_call_connection_lost(original)
    return True
