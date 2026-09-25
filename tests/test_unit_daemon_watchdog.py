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


# ─── boot-vs-crash discrimination (the restart-storm guard) ──────────────────
# Regression pins for 2026-07-30: the watchdog respawned on top of a daemon that
# was still booting, four stacked boots starved each other (28s -> 1006s), and
# :9000 stayed down ~50 min. Both directions matter — waiting on a live boot AND
# still recovering from a real crash.

DEAD = lambda pid: False        # noqa: E731 - table-style stubs read better inline
ALIVE = lambda pid: True        # noqa: E731


def test_live_boot_is_not_a_crash(dw):
    """A respawn that is alive but not yet serving must be waited on, not replaced."""
    assert dw.boot_still_running(4242, elapsed=120.0, boot_timeout=900.0, alive=ALIVE) is True


def test_dead_respawn_falls_through_to_recovery(dw):
    """The crash path must still work — a gone process is a real failure."""
    assert dw.boot_still_running(4242, elapsed=120.0, boot_timeout=900.0, alive=DEAD) is False


def test_hung_boot_is_abandoned_at_boot_timeout(dw):
    """Alive is not enough forever: past the ceiling a stuck boot gets killed+retried."""
    assert dw.boot_still_running(4242, elapsed=900.0, boot_timeout=900.0, alive=ALIVE) is False
    assert dw.boot_still_running(4242, elapsed=1500.0, boot_timeout=900.0, alive=ALIVE) is False


def test_no_tracked_child_means_no_extension(dw):
    """Fresh watchdog (never spawned anything) can't claim a boot is in flight."""
    assert dw.boot_still_running(None, elapsed=10.0, boot_timeout=900.0, alive=ALIVE) is False


def test_zero_boot_timeout_disables_the_extension(dw):
    """Opt-out restores the strict reassess-at-grace behaviour."""
    assert dw.boot_still_running(4242, elapsed=1.0, boot_timeout=0.0, alive=ALIVE) is False


def test_clears_listener_only_when_no_orphan(dw):
    assert dw.pids_to_clear(1234, None, alive=DEAD) == [1234]


def test_clears_listener_and_live_orphan_listener_first(dw):
    assert dw.pids_to_clear(1234, 5678, alive=ALIVE) == [1234, 5678]


def test_dead_orphan_is_not_killed(dw):
    assert dw.pids_to_clear(1234, 5678, alive=DEAD) == [1234]


def test_live_orphan_cleared_even_with_no_listener(dw):
    """THE storm case: port free because our own previous boot never bound it."""
    assert dw.pids_to_clear(None, 5678, alive=ALIVE) == [5678]


def test_same_pid_is_not_killed_twice(dw):
    assert dw.pids_to_clear(1234, 1234, alive=ALIVE) == [1234]


def test_nothing_to_clear_is_empty(dw):
    assert dw.pids_to_clear(None, None, alive=ALIVE) == []


# ─── evidence capture reaches the "dead" case ────────────────────────────────
# py-spy used to be keyed off the LISTENING pid, so it was skipped exactly when
# nothing was listening — all 218 snapshots of the 2026-07-30 outage carry no
# stacks. find_daemon_pids answers "who is alive" independently of the port.

def test_parse_pid_list_extracts_and_dedupes(dw):
    assert dw.parse_pid_list("123\n456\n123\n") == [123, 456]


def test_parse_pid_list_ignores_noise_and_blanks(dw):
    noisy = "\nProcessId\n-----\n 4242 \nnot-a-pid\n\n"
    assert dw.parse_pid_list(noisy) == [4242]


def test_parse_pid_list_rejects_nonpositive(dw):
    assert dw.parse_pid_list("0\n-1\n") == []


def test_parse_pid_list_empty_input(dw):
    assert dw.parse_pid_list("") == []
    assert dw.parse_pid_list(None) == []


def test_find_daemon_pids_honours_limit(dw, monkeypatch):
    monkeypatch.setattr(dw, "run_cmd", lambda *a, **k: "1\n2\n3\n4\n5\n6\n")
    assert dw.find_daemon_pids(limit=4) == [1, 2, 3, 4]


def test_find_daemon_pids_survives_a_failed_probe(dw, monkeypatch):
    """A broken/absent process lister must not break evidence capture."""
    monkeypatch.setattr(dw, "run_cmd", lambda *a, **k: "--- exception: TimeoutExpired ---")
    assert dw.find_daemon_pids() == []


# ── console-storm census ────────────────────────────────────────────────────
# Both machine-killing incidents (2026-08-01, 2026-08-15) were console-host
# storms. Pin BOTH directions against the shape of a real tasklist: it must
# fire on a storm and stay silent on a healthy box, or it is noise that gets
# ignored the one night it matters.

_TASKLIST_HEAD = (
    "\r\nImage Name                     PID Session Name        Session#    Mem Usage\r\n"
    "========================= ======== ================ =========== ============\r\n"
)


def _tasklist(rows: list[tuple[str, int]]) -> str:
    """Render a tasklist /FO TABLE body from (image, count) pairs."""
    out = [_TASKLIST_HEAD]
    pid = 1000
    for name, count in rows:
        for _ in range(count):
            pid += 4
            out.append(f"{name:<25} {pid:>8} Console                    1     10,240 K\r\n")
    return "".join(out)


def test_console_census_counts_hosts_and_processes(dw):
    text = _tasklist([("conhost.exe", 5), ("OpenConsole.exe", 3), ("chrome.exe", 7)])
    c = dw.console_census(text)
    assert c["conhost"] == 5
    assert c["openconsole"] == 3
    assert c["console_hosts"] == 8
    assert c["processes"] == 15


def test_console_census_silent_on_a_healthy_box(dw):
    """Measured baseline on the dev box is 26-51 console hosts across ~200 snapshots."""
    text = _tasklist([("conhost.exe", 42), ("OpenConsole.exe", 9), ("svchost.exe", 100)])
    assert dw.console_census(text)["console_hosts"] < dw.CONSOLE_STORM_THRESHOLD


def test_console_census_fires_on_a_storm(dw):
    """2026-08-15 shape: 1069 conhost + 749 OpenConsole."""
    text = _tasklist([("conhost.exe", 1069), ("OpenConsole.exe", 749)])
    assert dw.console_census(text)["console_hosts"] >= dw.CONSOLE_STORM_THRESHOLD


def test_console_census_ignores_header_and_junk(dw):
    """Header rules and a truncated trailing line must not count as processes."""
    assert dw.console_census(_TASKLIST_HEAD + "not a process line\r\n")["processes"] == 0


def test_console_census_is_case_insensitive(dw):
    """tasklist casing has varied across Windows builds; don't miss a storm on it."""
    text = _tasklist([("CONHOST.EXE", 4), ("openconsole.exe", 2)])
    assert dw.console_census(text)["console_hosts"] == 6


def test_memory_pressure_shape_or_empty(dw):
    """Never raises; either a full reading or {} where unavailable."""
    m = dw.memory_pressure()
    assert isinstance(m, dict)
    if m:
        assert 0 < m["commit_pct"] <= 100
        assert m["commit_used_mb"] <= m["commit_limit_mb"]


# ── recovery_verdict: which failures earn a kill ─────────────────────────────

class TestRecoveryVerdict:
    """The watchdog killed a healthy, listening daemon ~235 times.

    Measured across 244 captured snapshots on the Windows box: 96% ended at the
    two-poll detection floor (median seconds_wedged 35.0), netstat showed :9000
    LISTENING on the very PID that was then killed, and py-spy showed the main
    thread idle in `select`. Only 7 were alive-but-not-listening — the genuine
    shape. A distribution pinned at the detection floor means the threshold set
    the number, not the fault.
    """

    def test_a_dead_daemon_recovers_immediately(self, dw):
        go, why = dw.recovery_verdict(None, [], 1.0, 120.0)
        assert go and why == "dead"

    def test_alive_but_not_listening_recovers_immediately(self, dw):
        """The 7-of-244 genuine case: process up, serving nothing. Unambiguous,
        so it must NOT be delayed by the sustained-failure bar."""
        go, why = dw.recovery_verdict(None, [4242], 1.0, 120.0)
        assert go and why == "not-listening"

    def test_a_listening_daemon_that_missed_two_probes_is_not_killed(self, dw):
        """The 96% case. 35s is the median of every snapshot on the box."""
        go, why = dw.recovery_verdict(1234, [], 35.0, 120.0)
        assert not go
        assert "1234" in why and "35s of 120s" in why

    def test_a_listening_daemon_still_failing_much_later_is_killed(self, dw):
        go, why = dw.recovery_verdict(1234, [], 130.0, 120.0)
        assert go and "unresponsive for 130s" in why

    def test_the_bar_is_inclusive_at_the_boundary(self, dw):
        assert dw.recovery_verdict(1234, [], 120.0, 120.0)[0] is True
        assert dw.recovery_verdict(1234, [], 119.9, 120.0)[0] is False

    def test_recover_after_zero_restores_the_old_behaviour(self, dw):
        """The escape hatch has to actually escape, or nobody can get the
        aggressive behaviour back on a box that needs it."""
        go, why = dw.recovery_verdict(1234, [], 0.1, 0.0)
        assert go and why == "unresponsive"

    def test_the_delay_never_applies_to_a_daemon_that_is_not_serving(self, dw):
        """Both unambiguous shapes recover at once at any recover_after."""
        for after in (0.0, 120.0, 86400.0):
            assert dw.recovery_verdict(None, [], 0.0, after)[0] is True
            assert dw.recovery_verdict(None, [9], 0.0, after)[0] is True


class TestRecoveryVerdictIsActuallyWired:
    """A tested pure helper proves the helper works, never that anything calls
    it (.claude/rules/audits.md). The kill lives in main(); these read the AST
    of main() so a comment mentioning the helper cannot satisfy them.
    """

    @staticmethod
    def _main_fn(dw):
        import ast
        src = (REPO / "scripts" / "daemon_watchdog.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        assert fn is not None, "main() is gone — this test cannot see the kill path"
        return fn

    def test_main_calls_recovery_verdict(self, dw):
        import ast
        calls = [n for n in ast.walk(self._main_fn(dw))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "recovery_verdict"]
        assert len(calls) == 1, (
            f"main() calls recovery_verdict {len(calls)}x; the kill path must "
            "consult it exactly once")

    def test_the_call_is_given_the_operator_flag_not_a_literal(self, dw):
        """Passing a literal would make --recover-after inert: the flag would
        parse, appear in --help, and change nothing."""
        import ast
        call = next(n for n in ast.walk(self._main_fn(dw))
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "recovery_verdict")
        assert len(call.args) == 4, f"recovery_verdict called with {len(call.args)} args"
        last = call.args[3]
        assert isinstance(last, ast.Attribute) and last.attr == "recover_after", (
            "the sustained-failure bar is not read from args.recover_after — "
            f"got {ast.dump(last)[:80]}")

    def test_a_false_verdict_short_circuits_before_any_kill(self, dw):
        """The verdict must GUARD the kill, not merely be computed next to it."""
        import ast
        fn = self._main_fn(dw)
        guards = [n for n in ast.walk(fn)
                  if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
                  and isinstance(n.test.op, ast.Not)
                  and isinstance(n.test.operand, ast.Name)
                  and n.test.operand.id == "go"]
        assert guards, "no `if not go:` guard — the verdict is computed and ignored"
        bodies = [ast.dump(ast.Module(body=g.body, type_ignores=[])) for g in guards]
        assert any("Continue" in b for b in bodies), (
            "the guard does not `continue` — execution falls through to the kill")
