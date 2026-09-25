"""Single-instance lock + second-launch pipe for the desktop shell — real Win32 objects.

Uses a unique mutex/pipe name per test, so a running EmptyOS Desktop shell on
this machine never interferes (and is never touched).
"""

from __future__ import annotations

import json
import pickle
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "products"))

from _shared import single_instance as si  # noqa: E402

pytestmark = pytest.mark.unit
win_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 mutex + named pipe")

EXECUTED: list[str] = []


class _Payload:
    """Unpickling this records that code ran — the whole attack in one object."""

    def __reduce__(self):
        return (EXECUTED.append, ("pwned",))


def _name() -> str:
    return f"Local\\EmptyOS.Desktop.test-{uuid.uuid4().hex}"


def _pipe() -> str:
    return f"\\\\.\\pipe\\EmptyOS.Desktop.test-{uuid.uuid4().hex}"


# ── decode: the pipe's actual input contract ─────────────────────────────────
def test_decode_accepts_only_validated_json():
    assert si.decode_message(json.dumps({"cmd": "show"}).encode()) == {"cmd": "show"}
    assert si.decode_message(json.dumps({"cmd": "show", "path": "/kb/"}).encode()) == \
        {"cmd": "show", "path": "/kb/"}
    assert si.decode_message(json.dumps({"cmd": "quit"}).encode()) is None
    assert si.decode_message(b"\xff\xfe not json") is None


def test_decode_never_unpickles():
    EXECUTED.clear()
    assert si.decode_message(pickle.dumps(_Payload())) is None
    assert si.decode_message(pickle.dumps({"cmd": "show"})) is None
    assert EXECUTED == []


# ── real mutex + pipe ────────────────────────────────────────────────────────
@win_only
def test_second_acquire_fails_until_release():
    name = _name()
    first = si.InstanceLock.acquire(name)
    assert first is not None
    assert si.InstanceLock.acquire(name) is None
    first.release()
    again = si.InstanceLock.acquire(name)
    assert again is not None
    again.release()


@win_only
def test_release_is_idempotent_across_threads():
    """Two releasers (the watcher and run()'s teardown) must close the handle once."""
    name = _name()
    lock = si.InstanceLock.acquire(name)
    ts = [threading.Thread(target=lock.release) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert lock._handle == 0
    again = si.InstanceLock.acquire(name)       # truly released
    assert again is not None
    again.release()


@win_only
def test_send_without_a_listener_is_false():
    assert si.send(_pipe(), {"cmd": "show"}) is False


def _collect(addr):
    got: list[dict] = []
    stop = si.serve(addr, got.append)
    return got, stop


def _wait_for(got, n, timeout=6.0):
    deadline = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < deadline:
        time.sleep(0.05)


@win_only
def test_round_trip_delivers_valid_messages_only():
    addr = _pipe()
    got, stop = _collect(addr)
    try:
        assert si.send(addr, {"cmd": "restart_daemon"}) is False  # refused before sending
        assert si.send(addr, {"cmd": "show", "path": "/kb/"}) is True
        _wait_for(got, 1)
        assert got == [{"cmd": "show", "path": "/kb/"}]
    finally:
        stop()


@win_only
def test_listener_drops_raw_hostile_input():
    """Another process can write anything to the pipe, bypassing send()'s check."""
    from multiprocessing.connection import Client

    EXECUTED.clear()
    addr = _pipe()
    got, stop = _collect(addr)
    try:
        oversized_but_valid = json.dumps({"cmd": "show", "pad": "x" * si.MAX_MESSAGE_BYTES}).encode()
        for raw in (pickle.dumps(_Payload()),                                     # code execution attempt
                    json.dumps({"cmd": "show", "path": "//evil.example/"}).encode(),
                    oversized_but_valid):                                         # would validate, if read
            c = Client(address=addr, family="AF_PIPE")
            c.send_bytes(raw)
            c.close()
        c = Client(address=addr, family="AF_PIPE")
        c.send(_Payload())                     # the stdlib's own pickling send()
        c.close()
        assert si.send(addr, {"cmd": "show", "path": "/kb/"}) is True
        _wait_for(got, 1)
        time.sleep(0.3)
        # Only the legitimate message, which differs from anything the hostile
        # ones would decode to — so a dropped cap or check can't hide here.
        assert got == [{"cmd": "show", "path": "/kb/"}]
        assert EXECUTED == []
    finally:
        stop()


@win_only
def test_a_silent_client_cannot_block_later_launches():
    from multiprocessing.connection import Client

    addr = _pipe()
    got, stop = _collect(addr)
    try:
        silent = Client(address=addr, family="AF_PIPE")   # connects, never sends
        delivered = threading.Event()

        def later():
            if si.send(addr, {"cmd": "show"}):
                delivered.set()

        threading.Thread(target=later, daemon=True).start()
        _wait_for(got, 1, timeout=si.RECV_TIMEOUT_S + 4)
        assert got == [{"cmd": "show"}], "a stalled client held the accept loop"
        silent.close()
    finally:
        stop()
