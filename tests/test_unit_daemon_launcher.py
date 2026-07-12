"""Unit tests for the shared daemon-readiness poll (emptyos.sdk.daemon_launcher).

Pure-logic — no daemon. Safe in CI.
"""
from __future__ import annotations

import time

import pytest

from emptyos.sdk import daemon_launcher as dl
from emptyos.sdk.daemon_launcher import poll_health, wait_health

pytestmark = pytest.mark.unit


def test_wait_health_false_on_dead_url_without_hanging():
    # Nothing listening on port 1 → returns False around the timeout, not a hang.
    t0 = time.time()
    assert wait_health("http://127.0.0.1:1", timeout=0.5) is False
    assert time.time() - t0 < 5


class _DeadProc:
    def poll(self):
        return 1  # already exited


def test_wait_health_aborts_when_proc_dead():
    # A dead daemon subprocess → abort immediately rather than wait out the timeout.
    t0 = time.time()
    assert wait_health("http://127.0.0.1:1", proc=_DeadProc(), timeout=10.0) is False
    assert time.time() - t0 < 2


# ── poll_health: the reason + payload contract the boot smoke relies on ─────


def _fake_get(sequence):
    """Return a _get stand-in that yields (answered, payload) tuples in order."""
    it = iter(sequence)

    def _get(base, query, timeout):
        try:
            return next(it)
        except StopIteration:
            return sequence[-1]

    return _get


def test_poll_health_reports_ready_with_the_payload(monkeypatch):
    monkeypatch.setattr(dl, "_get", _fake_get([(True, {"status": "ok", "apps": 3})]))
    r = poll_health("http://x", timeout=5, interval=0)
    assert (r.ok, r.reason) == (True, "ready")
    assert r.payload == {"status": "ok", "apps": 3}


def test_poll_health_ready_predicate_waits_for_apps(monkeypatch):
    # A 200 that reports status:starting must NOT count as ready under a predicate.
    monkeypatch.setattr(
        dl,
        "_get",
        _fake_get([(True, {"status": "starting", "apps": 0}), (True, {"status": "ok", "apps": 1})]),
    )
    r = poll_health("http://x", timeout=5, interval=0, ready=lambda b: b.get("status") == "ok")
    assert r.ok is True
    assert r.payload["status"] == "ok"


def test_poll_health_distinguishes_proc_died_from_timeout(monkeypatch):
    monkeypatch.setattr(dl, "_get", _fake_get([(False, None)]))
    died = poll_health("http://x", proc=_DeadProc(), timeout=5, interval=0)
    assert (died.ok, died.reason) == (False, "proc_died")

    timed_out = poll_health("http://x", timeout=0.05, interval=0.01)
    assert (timed_out.ok, timed_out.reason) == (False, "timeout")


def test_poll_health_keeps_the_last_payload_seen_on_timeout(monkeypatch):
    # A tree that answers "starting" forever but never mounts apps: the caller
    # wants that last body to print, not None.
    monkeypatch.setattr(dl, "_get", _fake_get([(True, {"status": "starting", "apps": 0})]))
    r = poll_health("http://x", timeout=0.05, interval=0.01, ready=lambda b: b.get("apps"))
    assert r.reason == "timeout"
    assert r.payload == {"status": "starting", "apps": 0}


def test_fetch_health_returns_none_on_non_json_200(monkeypatch):
    monkeypatch.setattr(dl, "_get", lambda *a: (True, None))
    assert dl.fetch_health("http://x") is None
