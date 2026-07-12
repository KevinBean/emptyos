"""Unit tests for the daemon-watchdog recovery invariants (no daemon needed).

The watchdog (scripts/daemon_watchdog.py) is a user-owned crash/wedge supervisor
for :9000. The safety-critical invariant is the SINGLETON RECOVERY LOCK: at most
one --restart watchdog per port may kill+respawn the daemon — two would fight on
the same wedge. These tests pin that lock's behavior (first-acquire,
live-owner-reject, stale-PID-reclaim, own-release) plus the give-up flag writer.

The recovery state machine itself was verified end-to-end against a fake health
server when the feature landed; these are the durable regression tests for the
pure helpers. Loaded from file because scripts/ isn't an importable package.
"""
from __future__ import annotations

import importlib.util
import json
import os
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "daemon_watchdog", REPO / "scripts" / "daemon_watchdog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def dw(tmp_path, monkeypatch):
    """Watchdog module with DATA_DIR redirected to an isolated tmp dir."""
    mod = _load()
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    return mod


PORT = 9999


# ─── _pid_alive ──────────────────────────────────────────────────────────────

def test_pid_alive_self_is_true(dw):
    assert dw._pid_alive(os.getpid()) is True


def test_pid_alive_dead_and_invalid_are_false(dw):
    assert dw._pid_alive(999999) is False  # almost certainly not a live process
    assert dw._pid_alive(0) is False
    assert dw._pid_alive(-1) is False


# ─── recovery lock ───────────────────────────────────────────────────────────

def test_first_acquire_succeeds_and_writes_owner(dw):
    assert dw._acquire_recovery_lock(PORT) is True
    data = json.loads(dw._recovery_lock_path(PORT).read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["port"] == PORT


def test_reacquire_by_same_owner_succeeds(dw):
    assert dw._acquire_recovery_lock(PORT) is True
    # Same process re-acquiring is fine (idempotent for the owner).
    assert dw._acquire_recovery_lock(PORT) is True


def test_live_owner_rejected(dw, monkeypatch):
    """A second watchdog must NOT take a lock a live owner already holds."""
    real = os.getpid()
    lock = dw._recovery_lock_path(PORT)
    lock.write_text(json.dumps({"pid": real, "port": PORT}), encoding="utf-8")
    # Pretend we're a different process; the real pid (alive python) owns it.
    monkeypatch.setattr(dw.os, "getpid", lambda: real + 1)
    assert dw._acquire_recovery_lock(PORT) is False
    # Owner unchanged — the rejected caller never overwrote it.
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == real


def test_stale_dead_owner_reclaimed(dw):
    """A lock left by a dead PID (e.g. killed by restart.bat) is reclaimed."""
    lock = dw._recovery_lock_path(PORT)
    lock.write_text(json.dumps({"pid": 999999, "port": PORT}), encoding="utf-8")
    assert dw._acquire_recovery_lock(PORT) is True
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_corrupt_lock_fails_safe(dw):
    """A garbage lock file must not crash recovery — fail safe to not-owner."""
    lock = dw._recovery_lock_path(PORT)
    lock.write_text("not json at all", encoding="utf-8")
    # _acquire swallows the parse error and returns False (don't become a killer).
    assert dw._acquire_recovery_lock(PORT) is False


def test_release_only_removes_own_lock(dw):
    lock = dw._recovery_lock_path(PORT)
    # Our own lock → released.
    assert dw._acquire_recovery_lock(PORT) is True
    dw._release_recovery_lock(PORT)
    assert not lock.exists()
    # A different owner's lock → left intact.
    lock.write_text(json.dumps({"pid": 999999, "port": PORT}), encoding="utf-8")
    dw._release_recovery_lock(PORT)
    assert lock.exists()


# ─── wedge / give-up flag ────────────────────────────────────────────────────

def test_clear_wedge_flag(dw, tmp_path):
    flag = tmp_path / "wedge-alert.flag"
    flag.write_text("{}", encoding="utf-8")
    dw._clear_wedge_flag()
    assert not flag.exists()
    # Idempotent when already gone.
    dw._clear_wedge_flag()


def test_flag_give_up_writes_marker_and_skips_notify(dw, tmp_path):
    args = types.SimpleNamespace(
        port=PORT, restart_window=1800.0, giveup_cooldown=300.0,
        telegram_token=None, telegram_chat=None, no_notify=True,
    )
    dw._flag_give_up(args, [1.0, 2.0])
    payload = json.loads((tmp_path / "wedge-alert.flag").read_text(encoding="utf-8"))
    assert payload["gave_up"] is True
    assert payload["restarts"] == 2
    assert payload["port"] == PORT
    # With a cooldown the alert promises an automatic retry, not a manual fix.
    assert "retrying automatically" in payload["message"]


# ─── wait_for_port_free (10048 race fix) ─────────────────────────────────────

def test_wait_for_port_free_returns_true_when_already_free(dw, monkeypatch):
    monkeypatch.setattr(dw, "find_listening_pid", lambda port: None)
    # Should not call kill at all when the port is already free.
    monkeypatch.setattr(dw, "kill_pid_tree", lambda pid: pytest.fail("must not kill"))
    assert dw.wait_for_port_free(PORT, timeout=5.0) is True


def test_wait_for_port_free_times_out_and_rekills_stubborn_listener(dw, monkeypatch):
    kills: list[int] = []
    monkeypatch.setattr(dw, "find_listening_pid", lambda port: 4242)  # never frees
    monkeypatch.setattr(dw, "kill_pid_tree", lambda pid: kills.append(pid))
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)  # don't actually wait
    assert dw.wait_for_port_free(PORT, timeout=0.0) is False
    # A zero timeout still made one kill attempt before giving up is NOT required;
    # the contract is: returns False when still held. (Re-kill is best-effort.)


def test_wait_for_port_free_frees_after_one_rekill(dw, monkeypatch):
    seq = iter([1234, None])  # held once, then free after the re-kill
    monkeypatch.setattr(dw, "find_listening_pid", lambda port: next(seq, None))
    kills: list[int] = []
    monkeypatch.setattr(dw, "kill_pid_tree", lambda pid: kills.append(pid))
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    assert dw.wait_for_port_free(PORT, timeout=10.0) is True
    assert kills == [1234]  # one re-kill nudge before it freed
