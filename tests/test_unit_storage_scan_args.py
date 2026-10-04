"""Regression tests for the eos-storage-cleanup scanner's drive + reserve logic.

Three defects, all measured live on 2026-09-23 and all invisible in a passing
run, which is why they are pinned here rather than left to a hand-check:

1. ``powershell -File scan_storage.ps1 -Drives C,D`` delivers ONE string
   ``"C,D"`` rather than a two-element array, so the membership test matched no
   drive at all. Every per-drive section was skipped and the script still
   exited 0 -- a scan reporting success having measured nothing
   (``.claude/rules/audits.md`` Failure mode 3).

2. A drive set that resolves to nothing must abort. Two shapes: a letter that
   matches no drive (``ZZ``), and ``-Drives`` supplied but naming no letter at
   all (``","``), which read as "no filter requested" and scanned EVERY drive.

3. Reserve files (pagefile/hiberfil/swapfile) were probed with ``Get-Item``,
   which opens a handle that a locked system file refuses, so it silently
   returned nothing.

The scanner is PowerShell, so these drive the real script through its two
diagnostic switches, which resolve and exit before any disk walking. That keeps
the file sub-second; a full scan takes minutes and cannot gate.

**Defect 3 is pinned by EXECUTION, not by reading the source.** An earlier
version of this file grepped ``scan_storage.ps1`` for the string
``Get-ChildItem`` -- which the explanatory *comment* above the code satisfies on
its own, so deleting the entire reserve pipeline kept the test green. That is
the exact anti-pattern ``.claude/rules/audits.md`` names ("stop reading the file
and execute the handler"), and it survived a mutation run because the mutation
was a textual revert that happened to reintroduce the searched-for string.
"""

from __future__ import annotations

import string
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "eos-storage-cleanup"
    / "scan_storage.ps1"
)

# The precondition is Windows with lettered local disks -- NOT merely "a
# PowerShell binary exists". `pwsh` installs happily on macOS and Linux (this
# user has a Mac), where `Get-PSDrive -PSProvider FileSystem` yields `/` and
# every drive-letter assertion below FAILS rather than skips. A failing test
# reads as a regression, indistinguishable from something actually broken
# (`.claude/rules/testing.md`).
needs_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="needs Windows and lettered local disks"
)
needs_script = pytest.mark.skipif(
    not SCRIPT.exists(), reason=f"{SCRIPT} not present"
)

pytestmark = [needs_windows, needs_script]


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke the scanner through `-File`, which is where the argument-binding
    defect lived. Any other invocation would test a code path nobody uses."""
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _lines(proc: subprocess.CompletedProcess) -> list[str]:
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def _all_drives() -> list[str]:
    proc = _run("-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    return _lines(proc)


def _absent_letter() -> str:
    """A well-formed drive letter this machine does not have. Sharper than
    "ZZ": an implementation that rejected anything longer than one character
    would pass on "ZZ" without ever exercising the membership test."""
    present = {d.upper() for d in _all_drives()}
    for ch in reversed(string.ascii_uppercase):
        if ch not in present:
            return ch
    pytest.skip("every drive letter is in use")


# --------------------------------------------------------------- defect 1 ---

def test_comma_string_from_file_invocation_resolves_every_named_drive():
    """THE defect. `-Drives C,D` over `-File` arrives as the single string
    "C,D". Asserting only that C came back would pass a first-element-only
    split, which drops D -- so assert the whole set."""
    every = _all_drives()
    if len(every) < 2:
        pytest.skip("needs at least two local disks to prove the split")
    a, b = every[0], every[1]
    proc = _run("-Drives", f"{a},{b}", "-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    assert sorted(_lines(proc)) == sorted([a, b])


def test_single_drive_resolves_to_exactly_that_drive():
    every = _all_drives()
    proc = _run("-Drives", every[0], "-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    assert _lines(proc) == [every[0]]


@pytest.mark.parametrize("fmt", ["{}", "{}:", "{}:\\", " {} "])
def test_drive_spec_is_normalised(fmt):
    """Callers write the drive several ways; all mean the same volume."""
    target = _all_drives()[0]
    proc = _run("-Drives", fmt.format(target), "-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    assert _lines(proc) == [target], f"{fmt!r} did not normalise"


def test_lowercase_spec_matches_uppercase_drive():
    target = _all_drives()[0]
    proc = _run("-Drives", target.lower(), "-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    assert _lines(proc) == [target]


def test_no_drives_argument_resolves_the_full_local_disk_set():
    """The unfiltered run must return every local disk -- not merely
    "something". A broken build that deleted the whole filter would still
    satisfy a non-emptiness assertion."""
    proc = _run("-ListDrivesOnly")
    assert proc.returncode == 0, proc.stderr
    names = _lines(proc)
    assert names
    assert all(len(n) == 1 and n.isalpha() for n in names), names
    assert "C" in names, "Windows always has a C: local disk"


# --------------------------------------------------------------- defect 2 ---

def test_absent_drive_letter_aborts_with_exit_2():
    """Exit code 2 is the documented contract (`.claude/rules/agent-cli.md`
    exit-code-as-signal). Asserting merely `!= 0` would let `exit 1` through
    while the doc went silently wrong."""
    proc = _run("-Drives", _absent_letter(), "-ListDrivesOnly")
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "nothing to scan" in proc.stderr.lower()
    assert not _lines(proc), "printed drive names despite matching none"


@pytest.mark.parametrize("spec", [",", ":", " ", ",,"])
def test_drives_supplied_but_naming_nothing_aborts_instead_of_scanning_all(spec):
    """The live bypass: an empty resolve read as "no filter requested", so the
    script scanned EVERY drive at exit 0 -- the vacuous pass one input to the
    left of the one this file was written for."""
    proc = _run("-Drives", spec, "-ListDrivesOnly")
    assert proc.returncode == 2, (spec, proc.returncode, proc.stdout, proc.stderr)
    assert not _lines(proc), f"{spec!r} resolved to a drive list"


# --------------------------------------------------------------- defect 3 ---

def test_reserve_files_are_found_by_executing_the_scanner():
    """Executable pin for defect 3. Windows always keeps at least one of
    pagefile/hiberfil/swapfile on C:, and `Get-Item` returns NONE of them
    because they are locked. So an empty result here means the probe came
    back -- which reading the source cannot tell you, since the comment beside
    the code names both cmdlets."""
    proc = _run("-ListReservesOnly")
    assert proc.returncode == 0, proc.stderr
    found = _lines(proc)
    assert found, "no reserve files reported at all -- Get-Item regression?"
    assert any(Path(p).name.lower().startswith("pagefile") for p in found), found


def test_reserve_listing_is_confined_to_the_named_drive():
    every = _all_drives()
    target = every[0]
    proc = _run("-Drives", target, "-ListReservesOnly")
    assert proc.returncode == 0, proc.stderr
    for p in _lines(proc):
        assert p.upper().startswith(f"{target}:"), p


# ------------------------------------------------------------- invariants ---

def test_diagnostic_switches_write_nothing_and_return_promptly(tmp_path):
    """Both switches must exit before the scan AND before $OutFile is
    defaulted. Asserting only "the report is absent" would be satisfied by a
    build that ran the entire multi-minute walk and merely skipped the write,
    so bound the time too."""
    out = tmp_path / "should-not-exist.md"
    for switch in ("-ListDrivesOnly", "-ListReservesOnly"):
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPT), switch, "-OutFile", str(out)],
            capture_output=True, text=True, timeout=25,
        )
        assert proc.returncode == 0, (switch, proc.stderr)
        assert not out.exists(), f"{switch} wrote a report"
        assert "# Storage scan" not in proc.stdout, f"{switch} emitted the report header"


def test_script_is_ascii_only():
    """PowerShell 5.1 reads a BOM-less .ps1 as cp1252, so one non-ASCII byte
    cascades into bogus parse errors. The script's own header requires this."""
    raw = SCRIPT.read_bytes()
    bad = [i for i, b in enumerate(raw) if b > 0x7F]
    assert not bad, f"non-ASCII bytes at offsets {bad[:5]}"
