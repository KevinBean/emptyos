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


class TestUnreadableRowIsNotClosed:
    """An unreadable row must never be counted as done.

    `unclosed_plan` is the one finding here whose advice is destructive — it
    tells a human to archive a plan. A row the parser could not read carries
    `status: ""`, which is not in OPEN_STATUSES, so before the guard a plan
    whose only remaining work sat in that row was reported "all N tasks closed
    — move to _plans/done/". Measured 2026-09-12 on the live
    `conversation-ingest-backlog` plan (T2 truncated mid-row; its own prose
    says 179 records remain).
    """

    def test_unreadable_row_withholds_the_archive_advice(self, tmp_path):
        # T1 genuinely done; T2's status cell is absent (row truncated).
        _plan(tmp_path,
              "| T1 | finished | — | done | 2026-08-01 | shipped |\n"
              "| T2 | truncated mid-row and the status cell never arri\n")
        codes = _codes(tmp_path)
        assert "unparsable_row" in codes, "the actionable finding must still fire"
        assert "unclosed_plan" not in codes, (
            "advised archiving a plan on the strength of a row nobody read"
        )

    def test_archive_advice_still_fires_when_every_row_parsed(self, tmp_path):
        """The guard must not silence the finding on a genuinely finished plan."""
        _plan(tmp_path,
              "| T1 | a | — | done | 2026-08-01 | shipped |\n"
              "| T2 | b | T1 | done | 2026-08-02 | shipped |\n")
        assert "unclosed_plan" in _codes(tmp_path)

    def test_unreadable_count_is_reported_beside_open(self, tmp_path):
        """`open: 0` alone reads as finished; the uncertainty must be visible."""
        _plan(tmp_path,
              "| T1 | finished | — | done | 2026-08-01 | shipped |\n"
              "| T2 | truncated and the status cell never arrived here\n")
        plan = cps.scan_plans(tmp_path, today=TODAY)["plans"][0]
        assert plan["open"] == 0
        assert plan["unreadable"] == 1


class TestBlockedRowCarryingItsReasonTag:
    """`.claude/rules/session-plans.md` mandates a blocker tag on a blocked row.

    Authors put it on the status cell (`blocked [decision-Kevin]`) as often as
    the task cell. An exact-match lookup failed there, so the rule's own
    vocabulary made a blocked task unreadable — and therefore, before the
    `unclosed_plan` guard above, indistinguishable from a finished one.
    Measured on the live `markitup-source-adapters` T5.
    """

    def test_blocked_with_a_reason_tag_parses_as_blocked(self, tmp_path):
        rows, notes, _, _ = cps._parse_tasks(
            HEADER + "| T1 | needs a call | — | blocked [decision-Kevin] | | |\n", 0
        )
        assert notes == [], f"row went unreadable: {notes}"
        assert rows[0]["status"] == "blocked"

    def test_a_normalised_status_is_still_reported_as_drift(self, tmp_path):
        """Parse it, but do not launder it.

        `session-plans.md` puts the blocker tag in the TASK cell, so a tag on
        the status cell is vocabulary drift — and the file's own precedent
        (`in-progress`) is to read the row AND report it. Normalising silently
        would let the checker hide exactly the drift its docstring watches for.
        """
        _plan(tmp_path,
              "| T1 | needs a call | — | blocked [decision-Kevin] | | |\n")
        bad = [f for f in cps.scan_plans(tmp_path, today=TODAY)["findings"]
               if f["code"] == "bad_status"]
        assert bad, "drift was normalised away instead of reported"
        assert "[decision-kevin]" in bad[0]["detail"], (
            "the message must quote the LITERAL cell, not the normalised one"
        )

    def test_a_tagged_blocked_row_counts_as_open(self, tmp_path):
        """The whole point: a blocked task is outstanding work, not closure."""
        _plan(tmp_path,
              "| T1 | done bit | — | done | 2026-08-01 | shipped |\n"
              "| T2 | waiting on a decision | — | blocked [blocked-human] | | |\n")
        res = cps.scan_plans(tmp_path, today=TODAY)
        assert res["plans"][0]["open"] == 1
        # Deliberately NOT asserting `unclosed_plan` is absent here: with the tag
        # unstripped the row goes unreadable, and the unreadable guard suppresses
        # that finding anyway — so the assertion would survive a full reversion
        # of the normaliser and pin nothing (audits.md: "a fixture covered by two
        # exclusions pins neither"). `open == 1` is the pin.
        assert res["plans"][0]["unreadable"] == 0

    def test_emphasis_markers_do_not_hide_a_status(self, tmp_path):
        rows, notes, _, _ = cps._parse_tasks(
            HEADER + "| T1 | a thing | — | **done** | 2026-08-01 | shipped |\n", 0
        )
        assert notes == []
        assert rows[0]["status"] == "done"

    def test_only_a_trailing_tag_is_stripped(self, tmp_path):
        """The stated safety property of BLOCKER_TAG, pinned.

        A greedy `\\s*\\[.*$` passes every other test in this file while
        swallowing arbitrary trailing text, so the property the surrounding
        comment leans on would be one careless edit from gone.
        """
        assert cps._status_cell("blocked [a]") == "blocked"
        # No closing bracket: nothing may be stripped.
        assert cps._status_cell("blocked [unterminated") == "blocked [unterminated"
        # A tag that is not trailing stays put, so the cell stops being a status.
        assert cps._status_cell("[decision-Kevin] blocked") != "blocked"

    def test_a_task_cell_can_never_anchor_the_row(self, tmp_path):
        """The regression the normaliser introduced, and the floor that closed it.

        `session-plans.md` mandates the blocker tag IN THE TASK CELL, so this
        spelling is contract-conformant. Before `_STATUS_MIN_COL` it anchored the
        row on column 1: status `done`, `open: 0`, and `unclosed_plan` fired on a
        QUEUED task — the destructive advice this scanner exists to withhold.
        """
        for task in ("Done [open-code]", "Blocked [decision-Kevin]",
                     "`done`", "**Blocked**"):
            _plan(tmp_path, f"| T1 | {task} | — | queued | | |\n")
            res = cps.scan_plans(tmp_path, today=TODAY)
            assert res["plans"][0]["open"] == 1, f"{task!r} anchored the wrong column"
            assert "unclosed_plan" not in [f["code"] for f in res["findings"]], (
                f"{task!r} produced archive advice for a queued task"
            )


class TestReportedLineAddressesTheFile:
    """A line number a human cannot open is not an actionable finding.

    `_parse_tasks` walks the post-frontmatter body, so every reported number was
    short by the frontmatter block — 7 lines on a typical plan, landing the
    reader in unrelated prose above the table.
    """

    def test_line_number_includes_the_frontmatter_offset(self, tmp_path):
        path = _plan(tmp_path, "| T1 | truncated before any status cell arrives\n")
        res = cps.scan_plans(tmp_path, today=TODAY)
        note = next(f for f in res["findings"] if f["code"] == "unparsable_row")
        reported = int(note["detail"].split("line ")[1].split(":")[0])
        lines = path.read_text(encoding="utf-8").splitlines()
        assert "truncated before any status" in lines[reported - 1], (
            f"line {reported} is {lines[reported - 1]!r}, not the offending row"
        )


class TestWrappedRowStopsTheWalk:
    """A row whose own content wraps hides every row below it, silently.

    Measured 2026-09-12 on `conversation-ingest-backlog`: a 19,483-char T2 cell
    spills onto a 4,690-char line carrying no leading `|`, so the walk stops and
    a 13-row table reports 2 rows / `open: 0`. Nothing named the loss, and the
    JSON `/eos-session-resume` Step 0 reads still said the plan was finished.
    """

    def test_wrapped_row_is_reported(self, tmp_path):
        _plan(tmp_path,
              "| T1 | a cell whose content wraps onto\n"
              "the next line with no leading pipe\n"
              "| T2 | still queued | — | queued | | |\n")
        res = cps.scan_plans(tmp_path, today=TODAY)
        trunc = [f for f in res["findings"] if f["code"] == "table_truncated"]
        assert trunc, "rows below a wrapped row vanished with no finding"
        assert "1 `|` row(s)" in trunc[0]["detail"]

    def test_a_wrapped_row_also_withholds_the_archive_advice(self, tmp_path):
        """Counts are a floor once the walk stops, so closure is unknowable.

        Every walked row here PARSES and is `done`, so the unreadable guard is
        not engaged and `not left` is the only thing that can withhold the
        finding. An earlier version of this fixture left the wrapping row
        statusless — it was then covered by the unreadable guard too, and
        survived deletion of `not left` (audits.md: "a fixture covered by two
        exclusions pins neither"). Caught by mutation, not by review.
        """
        _plan(tmp_path,
              "| T1 | done | — | done | 2026-08-01 | shipped |\n"
              "| T2 | also done, and its cell wraps | — | done | 2026-08-02 | shipped |\n"
              "a continuation line carrying no leading pipe\n"
              "| T3 | still queued | — | queued | | |\n")
        codes = _codes(tmp_path)
        assert "unparsable_row" not in codes, "fixture must not engage the other guard"
        assert "table_truncated" in codes
        assert "unclosed_plan" not in codes

    def _write(self, tmp_path, tail: str) -> None:
        (tmp_path / "sample.md").write_text(
            '---\ntype: session-plan\nplan: sample\ntrack: core-infra\n'
            'active_task: ""\n---\n\n# Sample\n\n## Tasks\n\n' + HEADER +
            "| T1 | a | — | done | 2026-08-01 | shipped |\n" + tail,
            encoding="utf-8",
        )

    def test_a_blank_line_ends_the_table_for_good(self, tmp_path):
        """The blank-line calibration, isolated from the heading guard.

        All 3 healthy plans carrying a second table end their task table at a
        BLANK line, and a counter that simply counted `|` rows below reported
        6 / 9 / 12 phantom rows on them. There is deliberately NO heading here:
        with one, the heading guard also stops the scan and this fixture would
        pass with the blank-line check deleted — which is exactly how it first
        shipped, and mutation is what caught it.
        """
        self._write(tmp_path, "\n| col | col |\n|---|---|\n| x | y |\n| p | q |\n")
        codes = [f["code"] for f in cps.scan_plans(tmp_path, today=TODAY)["findings"]]
        assert "table_truncated" not in codes, "swept in a table past a blank line"
        assert "unclosed_plan" in codes, "the healthy finding must survive"

    def test_a_separator_row_is_not_counted_as_a_lost_row(self, tmp_path):
        """The number is quoted to a human as rows they cannot see.

        A `|---|---|` separator is a pipe row but not a task row. Found by a
        NO-OP mutation: the battery named an exclusion the code did not have.
        """
        self._write(tmp_path,
                    "a continuation line carrying no leading pipe\n"
                    "|----|------|\n"
                    "| T2 | still queued | — | queued | | |\n")
        trunc = [f for f in cps.scan_plans(tmp_path, today=TODAY)["findings"]
                 if f["code"] == "table_truncated"]
        assert trunc, "expected the wrapped-row finding"
        assert "1 `|` row(s)" in trunc[0]["detail"], trunc[0]["detail"]

    def test_a_heading_ends_the_scan(self, tmp_path):
        """The heading guard, isolated from the blank-line calibration.

        The stopping line is a non-blank continuation, so the blank-line check
        does NOT apply and only the heading can stop the count.
        """
        self._write(tmp_path,
                    "a continuation line carrying no leading pipe\n"
                    "## Notes\n"
                    "| col | col |\n|---|---|\n| x | y |\n")
        codes = [f["code"] for f in cps.scan_plans(tmp_path, today=TODAY)["findings"]]
        assert "table_truncated" not in codes, "counted rows under a later heading"
