"""The mutation runner restores its target byte for byte, whatever the line endings.

`.claude/skills/eos-mutation-verify/run_mutations.py` read and wrote the target
as text. On Windows that turned every restored LF file into CRLF, so git showed
it modified and a hash taken before the run no longer matched — and the runner's
own restore check compared two strings that had been through the same newline
translation, so it reported success. A tool whose job is temporarily breaking
source files must hand back exactly the bytes it took.

Daemon-free. Each case runs the real runner against a throwaway module and test
in a temporary directory, with a multi-line anchor so a CRLF target also proves the anchor
translation (an untranslated anchor is a NO-OP, which fails `rc == 0`).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / ".claude" / "skills" / "eos-mutation-verify" / "run_mutations.py"

pytestmark = pytest.mark.skipif(not RUNNER.exists(), reason="mutation runner not present")

_TEST_BODY = (
    "import importlib.util\n"
    "import pathlib\n"
    "\n"
    "\n"
    "def test_value_is_one():\n"
    "    p = pathlib.Path(__file__).with_name('target_mod.py')\n"
    "    spec = importlib.util.spec_from_file_location('target_mod', p)\n"
    "    m = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(m)\n"
    "    assert m.value() == 1\n"
)


def _write_tests(tests: pathlib.Path) -> None:
    """Write the throwaway test file, with a `pytest.ini` beside it. Without an
    ini file pytest sets no confcutdir, walks every ancestor from the drive
    root and lists each level in full — all of %TEMP% included, where other
    processes create and delete entries. One vanishing between the listing and
    its stat fails collection: measured 1 run in 3 on 2026-09-28. The ini file
    makes this directory the rootdir, and the walk stops here."""
    tests.write_bytes(_TEST_BODY.encode("utf-8"))
    tests.with_name("pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def _runner():
    spec = importlib.util.spec_from_file_location("run_mutations_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_the_target_is_restored_byte_for_byte(newline):
    # A plain temporary directory, not the tmp_path fixture: this test runs the
    # runner, and the runner cannot pass --basetemp to the pytest it launches.
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "target_mod.py"
        target.write_bytes(newline.join(["def value():", "    return 1", ""]).encode("utf-8"))
        tests = pathlib.Path(tmp) / "test_target_mod.py"
        _write_tests(tests)
        before = target.read_bytes()

        rc = _runner().verify(
            str(target), str(tests),
            [("returns two", "def value():\n    return 1", "def value():\n    return 2",
              "value_is_one")],
        )

        assert rc == 0, "the mutation must apply and be caught"
        assert target.read_bytes() == before


def test_an_anchor_matching_twice_is_ambiguous_not_a_survivor(capsys):
    # `return 1` appears in two functions and the test only calls the second;
    # mutating the first match would read as SURVIVED for a line never touched.
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "target_mod.py"
        target.write_bytes(b"def other():\n    return 1\n\n\ndef value():\n    return 1\n")
        tests = pathlib.Path(tmp) / "test_target_mod.py"
        _write_tests(tests)
        before = target.read_bytes()

        rc = _runner().verify(str(target), str(tests),
                              [("returns two", "    return 1", "    return 2", "value_is_one")])
        out = capsys.readouterr().out
        assert "AMBIGUOUS" in out and "SURVIVED" not in out, out
        assert rc == 1 and target.read_bytes() == before

        # A CRLF target with a multi-line anchor matching three times: the count
        # must be taken after the anchor is translated to CRLF, or it reads 0
        # and the first match is mutated anyway.
        target.write_bytes(b"def a():\r\n    return 1\r\n\r\n\r\ndef b():\r\n    return 1\r\n\r\n\r\n"
                           b"def value():\r\n    return 1\r\n")
        crlf_before = target.read_bytes()
        rc = _runner().verify(str(target), str(tests),
                              [("returns two", "():\n    return 1", "():\n    return 2", "value_is_one")])
        out = capsys.readouterr().out
        assert "AMBIGUOUS" in out and "matches 3 places" in out, out
        assert rc == 1 and target.read_bytes() == crlf_before
        target.write_bytes(before)

        # Widened to the one occurrence, the same mutation is caught.
        rc = _runner().verify(
            str(target), str(tests),
            [("returns two", "def value():\n    return 1", "def value():\n    return 2",
              "value_is_one")])
        assert rc == 0 and target.read_bytes() == before


def test_overlapping_anchor_matches_are_counted():
    # str.count skips overlaps: 'a=1\na=1' occurs twice in three repeated lines.
    occ = _runner()._occurrences
    assert occ("a=1\na=1\na=1", "a=1\na=1") == 2
    assert occ("x = 1\n", "x = 1") == 1
    assert occ("nothing here", "x") == 0


def test_pytest_children_skip_the_real_vault_leak_purge(monkeypatch):
    """Every pytest session ends by purging the REAL vault while a daemon is up
    (tests/conftest.py leak backstop). Under a mutation that runs deliberately
    broken code against the user's notes, so the runner must switch it off."""
    mod = _runner()
    seen = {}

    class Done:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen.update(kw["env"])

        def wait(self, timeout=None):
            return 0
    monkeypatch.delenv("EOS_SKIP_LEAK_GUARD", raising=False)
    monkeypatch.setattr(mod.subprocess, "Popen", Done)
    mod._run_pytest("tests/x.py", "")
    assert seen.get("EOS_SKIP_LEAK_GUARD") == "1"
    assert seen.get("PYTHONDONTWRITEBYTECODE") == "1"


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _setup(tmp: str) -> tuple[pathlib.Path, pathlib.Path]:
    target = pathlib.Path(tmp) / "target_mod.py"
    target.write_bytes(b"def value():\n    return 1\n")
    tests = pathlib.Path(tmp) / "test_target_mod.py"
    _write_tests(tests)
    return target, tests


def _reap(pidfile: pathlib.Path) -> None:
    """Kill a grandchild a failed test left behind, so it does not outlive the run."""
    if not pidfile.exists():
        return
    pid = int(pidfile.read_text())
    if _pid_alive(pid):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)


def _in_thread(fn, limit: float) -> dict:
    result = {}
    worker = threading.Thread(target=lambda: result.update(rc=fn()), daemon=True)
    worker.start()
    worker.join(limit)
    result["returned"] = not worker.is_alive()
    return result


def test_a_mutant_over_budget_is_killed_and_reported_timeout_not_caught(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        before = target.read_bytes()
        rc = _runner().verify(str(target), str(tests), [
            ("loops forever", "    return 1", "    while True:\n        pass", "value_is_one"),
        ], timeout=15)
        out = capsys.readouterr().out
        assert rc == 1, out                       # not "as expected"
        assert "TIMEOUT" in out and "over the 15s budget" in out, out
        assert "RED" not in out
        assert target.read_bytes() == before


def test_the_timeout_kills_the_mutant_s_child_processes_too():
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        pidfile = pathlib.Path(tmp) / "child.pid"
        hang = ("    import subprocess, sys, time\n"
                "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                f"    open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
                "    time.sleep(300)")
        try:
            result = _in_thread(lambda: _runner().verify(
                str(target), str(tests), [("spawns and hangs", "    return 1", hang, "value_is_one")],
                timeout=15), 120)
            assert result["returned"], "verify never returned"
            assert result["rc"] == 1
            pid = int(pidfile.read_text())
            deadline = time.monotonic() + 10
            while _pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.5)
            assert not _pid_alive(pid), f"grandchild {pid} survived the kill"
        finally:
            _reap(pidfile)


def test_a_grandchild_outliving_a_clean_pass_is_neither_a_timeout_nor_a_block(capsys):
    """pytest passes and exits; a detached grandchild it started keeps running
    with pytest's output handle. Waiting on output EOF read that as a timeout
    and then blocked for the grandchild's whole life. The row must come back
    SURVIVED, promptly."""
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        pidfile = pathlib.Path(tmp) / "child.pid"
        flags = ("creationflags=0x00000008 | 0x00000200" if sys.platform == "win32"
                 else "start_new_session=True")
        # The grandchild must really hold pytest's output: capture off (-s) and
        # stdout passed explicitly, or Windows does not hand it the handle.
        tests.with_name("pytest.ini").write_text("[pytest]\naddopts = -s\n", encoding="utf-8")
        leak = ("    import subprocess, sys\n"
                "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'],"
                f" stdout=sys.stdout, stderr=sys.stdout, {flags})\n"
                f"    open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
                "    return 1  # still passes")
        try:
            result = _in_thread(lambda: _runner().verify(
                str(target), str(tests),
                [("leaks a process", "    return 1", leak, "value_is_one")], timeout=20), 75)
            out = capsys.readouterr().out
            assert result["returned"], "verify blocked on the grandchild"
            assert "SURVIVED" in out and "TIMEOUT" not in out, out
        finally:
            _reap(pidfile)


def test_a_suite_red_before_any_mutation_stops_the_batch(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        target.write_bytes(b"def value():\n    return 2\n")
        rc = _runner().verify(str(target), str(tests), [
            ("returns three", "    return 2", "    return 3", "value_is_one"),
        ])
        out = capsys.readouterr().out
        assert rc == 2 and "not green before any mutation" in out, out
        assert "RED" not in out
        assert target.read_bytes() == b"def value():\n    return 2\n"


def _fake(monkeypatch, mod, outcomes, baseline=1.0):
    """Replace pytest with a script of return codes; record each run's timeout."""
    seen = []
    script = iter(outcomes)

    def fake_pytest(tests, expect, timeout=None):
        seen.append(timeout)
        return next(script), ""
    clock = iter([0.0, baseline])
    monkeypatch.setattr(mod, "_run_pytest", fake_pytest)
    monkeypatch.setattr(mod, "_now", lambda: next(clock))
    return seen


def test_a_suite_with_no_tests_is_named_as_such(monkeypatch, capsys):
    mod = _runner()
    _fake(monkeypatch, mod, [5])
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        assert mod.verify(str(target), str(tests), [("x", "    return 1", "    return 2", "")]) == 2
    assert "collected no tests" in capsys.readouterr().out


@pytest.mark.parametrize("baseline, given, expected", [
    (100.0, None, 500.0),     # factor x the unmutated run
    (1.0, None, 30.0),        # never under the floor
    (100.0, 7.0, 7.0),        # an explicit timeout wins
])
@pytest.mark.parametrize("row, outcomes", [
    (("returns two", "    return 1", "    return 2", "value_is_one"), [0, 1, 0]),
    # survives the named test, so the whole-file re-check runs too
    (("returns two", "    return 1", "    return 2", "value_is_one"), [0, 0, 1, 0]),
    # a green row: named test, then the whole file
    (("comment", "    return 1", "    return 1  # ok", "value_is_one", "green"), [0, 0, 0, 0]),
], ids=["caught", "survivor-recheck", "green-recheck"])
def test_every_run_gets_the_budget(monkeypatch, baseline, given, expected, row, outcomes):
    mod = _runner()
    seen = _fake(monkeypatch, mod, outcomes, baseline)
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        mod.verify(str(target), str(tests), [row], timeout=given)
    assert seen == [given] + [expected] * (len(outcomes) - 1)


@pytest.mark.parametrize("row, outcomes, note", [
    (("returns two", "    return 1", "    return 2", "value_is_one"), [0, 0, None, 0],
     "whole-file re-check over"),
    (("comment", "    return 1", "    return 1  # ok", "value_is_one", "green"), [0, 0, None, 0],
     "whole-file re-run over"),
    (("comment", "    return 1", "    return 1  # ok", "value_is_one", "green"), [0, None, 0],
     "over the 30s budget"),
], ids=["red-recheck", "green-recheck", "green-named"])
def test_a_timeout_in_any_run_is_reported_timeout(monkeypatch, capsys, row, outcomes, note):
    mod = _runner()
    _fake(monkeypatch, mod, outcomes)
    with tempfile.TemporaryDirectory() as tmp:
        target, tests = _setup(tmp)
        assert mod.verify(str(target), str(tests), [row]) == 1
    out = capsys.readouterr().out
    assert "TIMEOUT" in out and note in out, out
    assert "MISTARGETED" not in out and "FALSE-POSITIVE" not in out, out
