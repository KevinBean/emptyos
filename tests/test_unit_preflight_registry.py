"""preflight's registry could name a scope the CLI refused, and lose a check silently.

Two defects found by the first-ever ``preflight --all`` run (2026-09-05):

* ``check_deferred_work.py`` declared scope ``deferred`` and ``check-csp-inline.py``
  declared ``export``, but neither string was in ``ALL_SCOPES`` — so
  ``--scope deferred`` exited 2 "unknown scope" for a check that existed.
* ``_select`` dropped any row whose script was missing on disk, with no output
  at all. A renamed or deleted checker made the run one check shorter and left
  it green.

Both are pinned in both directions: the registry/CLI sets must be *equal* (a
scope in ``ALL_SCOPES`` that no check names is drift too), and a missing script
must surface as FAIL for a gate and error otherwise — the same fail-closed
shape a timeout already has.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROBE = f"_pf_registry_probe_{os.getpid()}"


@pytest.fixture(scope="module")
def preflight():
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "preflight_registry_under_test", REPO / "scripts" / "preflight.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── F-01: every registered scope is selectable, and nothing is listed for no reason ──

def test_registered_scopes_equal_all_scopes(preflight):
    # Derived here from CHECKS, not from a helper in the module under test — a
    # helper returning set(ALL_SCOPES) would make both directions tautologies
    # (audits.md § Failure mode 3: the expected value must not come from the
    # file under test).
    registered = {s for c in preflight.CHECKS for s in c["scope"]}
    listed = set(preflight.ALL_SCOPES)
    assert registered - listed == set(), (
        f"scope(s) named by a CHECKS row but refused by --scope: {sorted(registered - listed)}")
    assert listed - registered == set(), (
        f"scope(s) in ALL_SCOPES that no check declares: {sorted(listed - registered)}")


def test_the_two_scopes_that_were_missing_are_selectable(preflight):
    """The concrete regression: the two strings that were absent."""
    for scope in ("deferred", "export"):
        assert scope in preflight.ALL_SCOPES
        assert preflight._select({scope}, gate_only=False), f"--scope {scope} selects nothing"


def test_all_scopes_has_no_duplicates(preflight):
    assert len(preflight.ALL_SCOPES) == len(set(preflight.ALL_SCOPES))


# ── F-02: a missing script is reported, never dropped ──

def test_select_keeps_a_row_whose_script_is_missing(preflight):
    row = {"script": f"{PROBE}_missing.py", "scope": ["always"], "gate": True}
    assert not (REPO / "scripts" / row["script"]).exists()
    preflight.CHECKS.append(row)
    try:
        selected = preflight._select({"always"}, gate_only=False)
        assert row in selected, "a registered check vanished from the run because its file is missing"
    finally:
        preflight.CHECKS.remove(row)


def test_missing_gate_script_fails_closed(preflight):
    res = preflight._run_one({"script": f"{PROBE}_gate.py", "scope": ["always"], "gate": True}, 5)
    assert res["state"] == "FAIL"
    assert "missing" in res["summary"] and f"{PROBE}_gate.py" in res["summary"]


def test_missing_advisory_script_is_an_error_not_silence(preflight):
    res = preflight._run_one({"script": f"{PROBE}_adv.py", "scope": ["always"], "gate": False}, 5)
    assert res["state"] == "error"
    assert "missing" in res["summary"]


def test_missing_script_counts_against_exit_code_via_main(preflight, monkeypatch, capsys):
    """The exit code is the signal agents branch on — a missing gate must move it."""
    row = {"script": f"{PROBE}_main.py", "scope": ["always"], "gate": True, "label": "probe-missing"}
    monkeypatch.setattr(sys, "argv", ["preflight.py", "--scope", "always", "--gate-only"])
    # Run only the probe: stub every real check so the test stays fast and hermetic.
    real_run_one = preflight._run_one

    def fake_run_one(c, timeout):
        if c is row:
            return real_run_one(c, timeout)
        return {**c, "rc": 0, "state": "ok", "summary": "", "detail": [], "dt": 0.0}

    monkeypatch.setattr(preflight, "_run_one", fake_run_one)
    preflight.CHECKS.append(row)      # inside the try below so no failure can leak it
    try:
        rc = preflight.main()
    finally:
        preflight.CHECKS.remove(row)
    out = capsys.readouterr().out
    assert rc == 1, f"one missing gate script should exit 1, got {rc}"
    assert "probe-missing" in out and "missing on disk" in out
