"""Both-direction pins for scripts/check_plan_staleness.py.

Per .claude/skills/eos-graduate-audit: a graduated checker ships with tests
pinning BOTH "fires on the real defect" and "silent on a healthy plan".

Three of these pin defects the scanner itself shipped with, all found by running
it against the live 3-plan corpus before registering it (the calibration step in
.claude/rules/audits.md):

  * `active_task: "T12 (claimed …)"` matched task **T1** via `str.startswith`,
    reporting a long-done task as a mismatched claim. Confident, specific, wrong.
  * An unrecognised status cell (`in-progress`) dropped the whole row, so every
    task depending on it reported a phantom `unresolved_dep` — the scanner
    accusing an innocent task of the parse failure's consequence.
  * A 4-column table (no `disposition` column) reported once per closed task —
    four findings for one authoring choice, burying the two that mattered.
"""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_plan_staleness.py"
_spec = importlib.util.spec_from_file_location("check_plan_staleness", SCRIPT)
cps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cps)

TODAY = datetime.date(2026, 8, 7)
HEADER = (
    "| id | task | depends_on | status | session | disposition |\n"
    "|----|------|-----------|--------|---------|-------------|\n"
)


def _plan(tmp_path: Path, rows: str, *, active: str = "", header: str = HEADER,
          name: str = "sample.md", track: str = "core-infra") -> Path:
    (tmp_path / name).write_text(
        f"---\ntype: session-plan\nplan: {Path(name).stem}\ntrack: {track}\n"
        f'active_task: "{active}"\n---\n\n# Sample\n\n## Tasks\n\n{header}{rows}\n',
        encoding="utf-8",
    )
    return tmp_path / name


def _codes(tmp_path: Path, **kw) -> list[str]:
    res = cps.scan_plans(tmp_path, today=TODAY, **kw)
    return sorted(f["code"] for f in res["findings"])


class TestSilentOnHealthy:
    def test_healthy_plan_reports_nothing(self, tmp_path):
        _plan(tmp_path,
              "| T1 | build the thing | — | done | 2026-08-01 | shipped |\n"
              "| T2 | next thing | T1 | queued | | |\n")
        assert _codes(tmp_path) == []

    def test_fresh_claim_is_not_stale(self, tmp_path):
        _plan(tmp_path,
              "| T1 | doing it | — | active | | |\n",
              active="T1 (claimed 2026-08-07)")
        assert _codes(tmp_path) == []

    def test_task_prose_containing_pipes_still_parses(self, tmp_path):
        """Task cells carry prose, code spans and `a | b` tables of their own.

        dev_tracks.parse_track_index learned this the same way; the scanner
        anchors on the status vocabulary rather than a column index, so a pipe
        anywhere in the prose shifts nothing.
        """
        _plan(tmp_path,
              "| T1 | ship `a | b` and the x|y flag | — | done | 2026-08-01 | shipped |\n"
              "| T2 | second | T1 | queued | | |\n")
        assert _codes(tmp_path) == []

    def test_sync_conflict_copy_is_skipped(self, tmp_path):
        """A conflict copy carries a STALE active_task; reading it as a separate
        plan invents a phantom claim that blocks real work."""
        _plan(tmp_path, "| T1 | x | — | queued | | |\n")
        _plan(tmp_path, "| T1 | x | — | active | | |\n",
              active="T1 (claimed 2026-01-01)",
              name="sample.sync-conflict-20260725-104221-6TUUOTZ.md")
        _plan(tmp_path, "| T1 | x | — | active | | |\n",
              active="T1 (claimed 2026-01-01)", name="sample 2.md")
        res = cps.scan_plans(tmp_path, today=TODAY)
        assert len(res["skipped"]) == 2
        assert res["findings"] == []


class TestFiresOnDefect:
    def test_all_tasks_closed_but_not_moved_to_done(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | done | 2026-08-01 | shipped |\n")
        assert "unclosed_plan" in _codes(tmp_path)

    def test_stale_claim(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | active | | |\n",
              active="T1 (claimed 2026-07-01)")
        assert "stale_claim" in _codes(tmp_path)

    def test_active_row_with_empty_active_task(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | active | | |\n")
        assert "claim_mismatch" in _codes(tmp_path)

    def test_active_task_naming_no_row(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | queued | | |\n",
              active="T9 (claimed 2026-08-07)")
        assert "claim_mismatch" in _codes(tmp_path)

    def test_unresolved_dependency(self, tmp_path):
        _plan(tmp_path, "| T1 | x | T7 | queued | | |\n")
        assert "unresolved_dep" in _codes(tmp_path)

    def test_dependency_cycle(self, tmp_path):
        _plan(tmp_path,
              "| T1 | x | T2 | queued | | |\n"
              "| T2 | y | T1 | queued | | |\n")
        res = cps.scan_plans(tmp_path, today=TODAY)
        cycles = [f for f in res["findings"] if f["code"] == "dep_cycle"]
        assert len(cycles) == 1, "a 2-cycle is one finding, not one per member"

    def test_done_without_disposition(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | done | 2026-08-01 | |\n"
                        "| T2 | y | — | queued | | |\n")
        assert "done_no_disposition" in _codes(tmp_path)

    def test_off_vocabulary_disposition(self, tmp_path):
        _plan(tmp_path, "| T1 | x | — | done | 2026-08-01 | stale-already-fixed |\n"
                        "| T2 | y | — | queued | | |\n")
        assert "bad_disposition" in _codes(tmp_path)


class TestCalibrationRegressions:
    """The three defects found by running against the live corpus."""

    def test_t12_claim_does_not_prefix_match_t1(self, tmp_path):
        """`"T12 (claimed …)".startswith("T1")` is True — the bug this pins."""
        _plan(tmp_path,
              "| T1 | first | — | done | 2026-08-01 | shipped |\n"
              "| T12 | twelfth | — | active | | |\n",
              active="T12 (claimed 2026-08-07)")
        assert _codes(tmp_path) == []

    def test_status_alias_parses_and_does_not_cascade(self, tmp_path):
        """`in-progress` is reported as bad_status — and the row still resolves,
        so a task depending on it must NOT report unresolved_dep."""
        _plan(tmp_path,
              "| T2 | doing | — | in-progress | | |\n"
              "| T5 | later | T2 | queued | | |\n",
              active="T2 (claimed 2026-08-07)")
        codes = _codes(tmp_path)
        assert "bad_status" in codes
        assert "unresolved_dep" not in codes

    def test_missing_disposition_column_reported_once(self, tmp_path):
        """Four closed tasks in a 4-column table: one finding, not four."""
        four_col = ("| id | task | depends_on | status |\n"
                    "|----|------|-----------|--------|\n")
        _plan(tmp_path,
              "| T1 | a | — | done |\n| T2 | b | — | done |\n"
              "| T3 | c | — | done |\n| T4 | d | — | queued |\n",
              header=four_col)
        res = cps.scan_plans(tmp_path, today=TODAY)
        codes = [f["code"] for f in res["findings"]]
        assert codes.count("no_disposition_column") == 1
        assert "done_no_disposition" not in codes

    def test_cycle_scan_is_not_exponential(self, tmp_path):
        """A dense but CYCLE-FREE dependency graph must not stall the scan.

        The first implementation enumerated every path: 0.001s at 12 tasks,
        0.041s at 18, 0.715s at 22. This check runs in preflight's `always`
        scope, so a large plan would have stalled every session start — on a
        graph containing no cycle at all.
        """
        import time
        rows = [
            {"id": f"T{i}", "deps": [f"T{j}" for j in range(i)], "status": "queued"}
            for i in range(28)
        ]
        start = time.time()
        assert cps._cycles(rows) == []
        assert time.time() - start < 1.0

    def test_cycle_still_found_in_a_dense_graph(self, tmp_path):
        """The cheap traversal must not lose the cycle it exists to find."""
        rows = [
            {"id": f"T{i}", "deps": [f"T{j}" for j in range(i)], "status": "queued"}
            for i in range(20)
        ]
        rows[0]["deps"] = ["T19"]          # close a long loop
        assert cps._cycles(rows), "cycle in a dense graph went unreported"
