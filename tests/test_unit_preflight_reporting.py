"""preflight's one-line-per-check view hid every finding but the last.

``_run_one`` kept only ``tail[-1]``, clamped to ``SUMMARY_CHARS``. A check
reporting nine findings displayed one. ``check-tier-folder`` is a hard gate, so
it did go red — but it showed 1 of its 9 violations, and the other eight were
fixed only when a commit went looking for them. A ``check_gap_freshness`` run
naming six missing apps showed the sixth.

Two halves are pinned here, because the first version of this file tested only
the first and a reviewer showed the second could be dead-coded with every test
still green:

* ``_run_one`` must *carry* every output line, and
* ``detail_block`` must *render* them — capped, with the truncation stated.

``detail_block`` is a pure function for exactly that reason: the rendering is
the half that was hiding things, so it has to be reachable from a test.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Probes must live in scripts/ — `_run_one` resolves REPO/"scripts"/<script> — so
# the name carries the pid: two sessions running this suite would otherwise share
# one filename and unlink each other's file mid-run. `scripts/_*.py` is gitignored,
# which also means a killed session leaves debris `git status` will not show.
PROBE = f"_pf_probe_{os.getpid()}"


@pytest.fixture(scope="module")
def preflight():
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "preflight", REPO / "scripts" / "preflight.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── half 1: the data reaches the caller ────────────────────────────────────

def test_run_one_carries_every_output_line_not_just_the_last(preflight):
    """The regression: nine findings must not arrive as one."""
    script = REPO / "scripts" / f"{PROBE}_a.py"
    script.write_text(
        "print('\\n'.join(f'finding {i}' for i in range(1, 10)))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    try:
        res = preflight._run_one({"script": f"{PROBE}_a.py", "gate": True}, 60)
    finally:
        script.unlink(missing_ok=True)

    assert res["state"] == "FAIL"
    assert res["summary"] == "finding 9"     # the one line the old shape kept
    body = [ln for ln in res["detail"] if ln.strip()]
    assert len(body) == 9, "every finding must reach the caller, not just the last"
    assert body[0] == "finding 1"


def test_indented_output_keeps_its_indentation(preflight):
    """`.strip()` on the whole blob de-indented only the first line."""
    script = REPO / "scripts" / f"{PROBE}_b.py"
    script.write_text("print('  a')\nprint('  b')\nraise SystemExit(1)\n", encoding="utf-8")
    try:
        res = preflight._run_one({"script": f"{PROBE}_b.py"}, 60)
    finally:
        script.unlink(missing_ok=True)
    assert res["detail"] == ["  a", "  b"], "first line must not lose its indent"


# ── half 2: the data is actually rendered ──────────────────────────────────

def _res(state, lines, **kw):
    return {"script": "probe.py", "state": state, "detail": lines, **kw}


def test_detail_block_renders_every_line_under_the_cap(preflight):
    out = preflight.detail_block(_res("FAIL", [f"finding {i}" for i in range(1, 10)]))
    assert len(out) == 9
    assert out[0].strip() == "finding 1"
    assert all(ln.startswith("      ") for ln in out), "findings are indented under the check"


def test_detail_block_states_what_it_truncated(preflight):
    """A bounded view must name what it dropped (.claude/rules/audits.md)."""
    n = preflight.DETAIL_LINES + 7
    out = preflight.detail_block(_res("warn", [f"line {i}" for i in range(n)]))
    assert len(out) == preflight.DETAIL_LINES + 1
    assert "… 7 more line(s) — run: python scripts/probe.py" in out[-1]


def test_truncation_notice_repeats_the_checks_own_args(preflight):
    """16 registry entries carry args; the command offered must reproduce the run."""
    n = preflight.DETAIL_LINES + 2
    out = preflight.detail_block(
        _res("warn", [f"line {i}" for i in range(n)], args=["--user-skills"]))
    assert out[-1].endswith("python scripts/probe.py --user-skills")


def test_a_passing_check_renders_nothing(preflight):
    """The compact healthy view is why this stayed broken — keep it."""
    assert preflight.detail_block(_res("ok", ["OK: clean", "extra"])) == []


def test_a_single_short_line_is_not_repeated_under_itself(preflight):
    """It is already the summary — printing it twice is noise, not information."""
    assert preflight.detail_block(_res("warn", ["3 findings, all advisory"])) == []


def test_a_single_over_long_line_is_not_silently_cut(preflight):
    """The `len(body) > 1` guard used to drop the tail of a lone long finding.

    The summary clamps at SUMMARY_CHARS with no marker, so a one-line check whose
    finding runs past it lost the end — the silent cap the block exists to end.
    """
    long_line = "x" * (preflight.SUMMARY_CHARS + 40)
    out = preflight.detail_block(_res("warn", [long_line]))
    assert out, "an over-long single finding must still be shown in full"
    assert out[0].strip() == long_line


def test_no_detail_for_a_check_that_printed_nothing(preflight):
    assert preflight.detail_block(_res("FAIL", [])) == []
    assert preflight.detail_block(_res("error", ["", "   "])) == []

def test_the_summary_line_is_not_echoed_under_itself(preflight):
    """`detail` still holds the line `summary` was taken from.

    The check's own row already shows it; repeating it as the last finding is
    the duplication the single-line guard exists to avoid, one case over.
    """
    lines = ["finding a", "finding b", "3 findings, all advisory"]
    out = preflight.detail_block(
        {"script": "p.py", "state": "warn", "detail": lines,
         "summary": "3 findings, all advisory"})
    assert [ln.strip() for ln in out] == ["finding a", "finding b"]


def test_a_summary_that_is_not_the_last_line_is_left_alone(preflight):
    """Only drop it when it really is the tail — never reorder a check's output."""
    lines = ["header", "finding a", "finding b"]
    out = preflight.detail_block(
        {"script": "p.py", "state": "warn", "detail": lines, "summary": "header"})
    assert [ln.strip() for ln in out] == lines


def test_detail_block_survives_a_result_missing_script(preflight):
    """Documented as a standalone pure renderer, so it must not KeyError."""
    out = preflight.detail_block(
        {"state": "warn", "detail": [f"l{i}" for i in range(preflight.DETAIL_LINES + 3)]})
    assert out[-1].strip().startswith("… 3 more line(s)")
