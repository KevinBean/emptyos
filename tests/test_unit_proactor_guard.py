"""Unit tests for the Windows Proactor peer-reset guard.

Daemon-free: the guard's wrapping half is a pure function, so both directions
are driven against a fake transport that mimics the stdlib shape. The one test
that touches the real ``asyncio`` class only asserts idempotence.
"""

from __future__ import annotations

import sys

import pytest

from emptyos.proactor_guard import (
    finish_transport_teardown,
    guard_call_connection_lost,
    install_proactor_reset_guard,
    is_peer_reset,
)


class FakeSock:
    def __init__(self, *, shutdown_winerror: int | None = None):
        self.shutdown_winerror = shutdown_winerror
        self.closed = False

    def shutdown(self, how):
        if self.shutdown_winerror is not None:
            err = ConnectionResetError("forcibly closed")
            err.winerror = self.shutdown_winerror
            raise err

    def close(self):
        self.closed = True


class FakeServer:
    def __init__(self):
        self.detached = []

    def _detach(self, transport):
        self.detached.append(transport)


class FakeTransport:
    """Mirrors CPython's ``_call_connection_lost`` control flow exactly."""

    def __init__(self, *, sock=None, server=None, protocol_error=None):
        self._sock = sock if sock is not None else FakeSock()
        self._server = server
        self._called_connection_lost = False
        self._protocol_error = protocol_error
        self.protocol_notified = False

    def _call_connection_lost(self, exc):
        try:
            self.protocol_notified = True
            if self._protocol_error is not None:
                raise self._protocol_error
        finally:
            if self._sock is not None:
                self._sock.shutdown(0)
            self._sock.close()
            self._sock = None
            server = self._server
            if server is not None:
                server._detach(self)
                self._server = None
            self._called_connection_lost = True


def _guarded(transport):
    """Bind the guard to one fake transport, the way the class patch does."""
    wrapped = guard_call_connection_lost(type(transport)._call_connection_lost)
    return lambda exc=None: wrapped(transport, exc)


# ── is_peer_reset ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("winerror", [10053, 10054, 121])
def test_peer_reset_winerrors_recognised(winerror):
    err = ConnectionResetError("gone")
    err.winerror = winerror
    assert is_peer_reset(err) is True


def test_unrelated_oserror_is_not_a_peer_reset():
    err = OSError("disk full")
    err.winerror = 112
    assert is_peer_reset(err) is False


def test_non_oserror_is_not_a_peer_reset():
    assert is_peer_reset(ValueError("nope")) is False


# ── the swallow direction ────────────────────────────────────────────────────


def test_peer_reset_is_swallowed():
    t = FakeTransport(sock=FakeSock(shutdown_winerror=10054))
    _guarded(t)()  # must not raise


def test_peer_reset_still_completes_the_skipped_teardown():
    """The whole point: shutdown() raising must not leak the socket."""
    sock = FakeSock(shutdown_winerror=10054)
    server = FakeServer()
    t = FakeTransport(sock=sock, server=server)

    # Unguarded, CPython's shape skips every statement after shutdown().
    with pytest.raises(ConnectionResetError):
        t._call_connection_lost(None)
    assert sock.closed is False, "precondition: the stdlib shape leaks the socket"
    assert server.detached == [], "precondition: the server never decrements"
    assert t._called_connection_lost is False

    # Guarded, the same failure finishes the teardown.
    sock2 = FakeSock(shutdown_winerror=10054)
    server2 = FakeServer()
    t2 = FakeTransport(sock=sock2, server=server2)
    _guarded(t2)()

    assert sock2.closed is True
    assert t2._sock is None
    assert server2.detached == [t2]
    assert t2._server is None
    assert t2._called_connection_lost is True


def test_protocol_is_still_notified_before_the_reset():
    t = FakeTransport(sock=FakeSock(shutdown_winerror=10054))
    _guarded(t)()
    assert t.protocol_notified is True


# ── the re-raise direction (the half a green suite would miss) ───────────────


def test_healthy_teardown_is_untouched():
    sock = FakeSock()
    server = FakeServer()
    t = FakeTransport(sock=sock, server=server)
    _guarded(t)()
    assert sock.closed is True
    assert server.detached == [t]
    assert t._called_connection_lost is True


def test_unrelated_oserror_propagates():
    err = OSError("disk full")
    err.winerror = 112
    t = FakeTransport(protocol_error=err)
    with pytest.raises(OSError, match="disk full"):
        _guarded(t)()


def test_protocol_reset_after_a_complete_finally_propagates():
    """A reset from the protocol is the caller's, not ours.

    The finally ran to completion, so ``_called_connection_lost`` is True — the
    discriminator that separates "shutdown blew up partway" from "the protocol
    raised".
    """
    err = ConnectionResetError("protocol said so")
    err.winerror = 10054
    t = FakeTransport(protocol_error=err)
    with pytest.raises(ConnectionResetError, match="protocol said so"):
        _guarded(t)()
    assert t._called_connection_lost is True


def test_missing_flag_attribute_re_raises():
    """Absent attribute means "not our shape" — fail closed, never swallow."""
    err = ConnectionResetError("gone")
    err.winerror = 10054

    def original(self, exc):
        raise err

    class Bare:
        pass

    with pytest.raises(ConnectionResetError):
        guard_call_connection_lost(original)(Bare(), None)


# ── teardown helper + installer ──────────────────────────────────────────────


def test_finish_teardown_survives_a_failing_close():
    class ExplodingSock(FakeSock):
        def close(self):
            raise OSError("already gone")

    t = FakeTransport(sock=ExplodingSock(), server=FakeServer())
    finish_transport_teardown(t)  # must not raise
    assert t._sock is None
    assert t._called_connection_lost is True


def test_finish_teardown_without_a_server():
    t = FakeTransport(server=None)
    finish_transport_teardown(t)
    assert t._server is None
    assert t._called_connection_lost is True


def test_install_is_idempotent():
    """Second call is a no-op, so a re-import can't stack wrappers."""
    install_proactor_reset_guard()
    assert install_proactor_reset_guard() is False


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows gate")
def test_install_no_ops_off_windows():
    assert install_proactor_reset_guard() is False
