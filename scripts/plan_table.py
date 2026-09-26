"""Session-plan `## Tasks` table parser — one reading for every consumer.

Extracted from `check_plan_staleness.py` (which re-exports it under its old
private names) when `session_board.py` became the second reader: the board had
its own pipe-split that only counted `T<n>` ids by column index, so a plan whose
tasks are `P1..P8` showed no tasks at all. Row anchoring, the status
vocabulary, blocker-tag stripping and the wrapped-row count are all explained
at their definitions below; they moved verbatim.

Contract: `.claude/rules/session-plans.md` § File format.
Stdlib-only, so any `scripts/*.py` can import it via `sys.path[0]`.
"""
from __future__ import annotations

import re

# `.claude/rules/session-plans.md` § File format. Kept as literals rather than
# imported from fix_queue.DISPOSITIONS because the rule subtracts one value
# (`planned`, meaningless for a task that *is* the plan) — importing the superset
# would silently accept it.
STATUSES = frozenset({"queued", "active", "done", "blocked"})
DISPOSITIONS = frozenset({"shipped", "deferred", "declined", "dismissed", "done"})
OPEN_STATUSES = frozenset({"queued", "active", "blocked"})

# Variants seen in the wild that mean a real status but are not the contract's
# word. Recognised so the row still PARSES — dropping it cascades: a dropped row
# is an id nothing resolves to, so every task depending on it reports a phantom
# `unresolved_dep`. Measured 2026-08-07: one `in-progress` cell produced two
# findings, and the second one pointed at an innocent task.
STATUS_ALIASES = {
    "in-progress": "active", "in progress": "active", "wip": "active",
    "open": "queued", "todo": "queued", "pending": "queued",
    "cancelled": "done", "canceled": "done",
}

NO_DEPS = {"", "—", "-", "–", "none", "n/a"}

# `.claude/rules/session-plans.md` tells authors a blocked row carries a reason
# tag — `[blocked-human]` / `[decision-Kevin]` / `[open-code]` — in the TASK
# cell, and one author put it on the status cell instead. An exact-match lookup
# then fails on `blocked [decision-Kevin]`, so the row goes unreadable and
# (before the `unclosed_plan` guard) counted as CLOSED: the rule's own vocabulary
# made a blocked task look finished. Measured 2026-09-12: 12 blocker tags across
# all 12 plans, 1 of them on a status cell — rare, not common, and worth handling
# only because the failure is silent and lands on the destructive finding.
# Only a TRAILING bracketed tag is stripped.
#
# The normaliser runs over EVERY cell, so on its own it widens what can reduce
# to a status word — and a task cell is the dangerous one, because `deps` is
# read from `idx - 1`. Measured: a row `| T1 | Done [open-code] | — | queued |`
# anchored on column 1, reported `open: 0`, and fired `unclosed_plan` on a
# QUEUED task — the destructive advice the rest of this scanner exists to
# withhold. Worse, `session-plans.md` mandates the tag *in the task cell*, so
# the contract-conformant spelling was the one that broke.
# `_STATUS_MIN_COL` is what makes the normaliser safe; neither is correct alone.
BLOCKER_TAG = re.compile(r"\s*\[[^\]]*\]\s*$")

# A status never sits in the id or task column. Measured over all 12 plans
# (6 live + 6 under `done/`): 83 status cells, ALL at index 3, none below.
# The floor is 2 rather than 3 so a 3-column `| id | task | status |` table
# still parses — `deps` then reads the task cell, which yields no valid ids
# and is why `unresolved_dep` is scoped to ids that exist.
_STATUS_MIN_COL = 2


def _status_cell(cell: str) -> str:
    """Reduce ANY cell to what it would be as a status word — tag and emphasis
    stripped — so the caller can test it against the vocabulary.

    Named for the cell it is *looking for*, not the cell it is given: it is
    mapped over every cell in the row. Reading it as "this runs on the status
    cell" is what hid the original hazard, where a task cell reduced to a status
    word and anchored the row — see `_STATUS_MIN_COL`, which is what bounds it.
    """
    return BLOCKER_TAG.sub("", cell.strip().strip("*` ")).strip().lower()


def _parse_tasks(
    body: str, line_offset: int
) -> tuple[list[dict], list[str], bool, int]:
    """Parse the `## Tasks` table.

    Returns (rows, unparsable_notes, has_disposition, rows_left_unwalked).

    `line_offset` is the number of lines `parse_frontmatter` consumed, so a
    reported line number addresses the FILE a human opens rather than the body
    the parser sees. Without it every number is short by the frontmatter block
    (7 lines on a typical plan) and lands the reader in unrelated prose — which
    makes the one actionable finding here, `unparsable_row`, point at the wrong
    place. REQUIRED rather than defaulted to 0: a default can only ever produce
    body-relative numbers silently, which is the bug it exists to prevent.

    Anchors each row on the cell holding a known *status*, never on column
    index. Task cells are long prose carrying `|` in code spans and tables of
    their own; `dev_tracks.parse_track_index` already learned this the same way
    ("col-3 prose embeds pipes, so a naive pipe-split is only trusted for the
    first two cells"). Anchoring on the vocabulary means a pipe anywhere in the
    prose shifts nothing.

    Fail-soft per row, per the devboard `_collect_tracks_sync` model: one
    malformed row must never hide the others.

    `rows_left_unwalked` counts `|`-prefixed lines still below where the walk
    stopped. The walk ends at the first non-pipe line, which is right for prose
    after a table and WRONG for a row whose own content wraps onto a
    continuation line — the remaining rows then vanish silently. Measured
    2026-09-12 on `conversation-ingest-backlog`: a 19,483-char T2 cell spills
    onto a 4,690-char line carrying no leading `|`, so a 13-row table reported
    2 rows and `open: 0`. Counting rather than swallowing the break keeps the
    existing end-of-table semantics (a `## Notes` table below must stay
    unparsed) while making the loss reportable.
    """
    rows: list[dict] = []
    notes: list[str] = []
    in_table = False
    has_disposition = False
    lines = body.splitlines()
    stopped_at = -1
    for n_line, ln in enumerate(lines):
        # `n_line` indexes `lines` (for the look-ahead below); `i` is the line
        # number in the FILE, which is what a finding must quote.
        i = n_line + 1 + line_offset
        stripped = ln.strip()
        if not in_table:
            low = stripped.lower()
            if low.startswith("| id") and "task" in low:
                in_table = True
                has_disposition = "disposition" in low
            continue
        if not stripped.startswith("|"):
            if rows:
                stopped_at = n_line
                break  # table ended (or a row wrapped — see `rows_left_unwalked`)
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":", " "}:
            continue  # separator row
        tid = cells[0].strip("*` ")
        if not tid or tid.lower() == "id":
            continue
        norm = [_status_cell(c) for c in cells]
        idx = next(
            (n for n, c in enumerate(norm)
             if n >= _STATUS_MIN_COL and c in STATUSES), None
        )
        raw = ""
        if idx is None:
            idx = next(
                (n for n, c in enumerate(norm)
                 if n >= _STATUS_MIN_COL and c in STATUS_ALIASES), None
            )
            if idx is not None:
                # The LITERAL cell, not the normalised one: `bad_status` quotes
                # this back to the human, and a normalised quote would report a
                # cell the file does not contain.
                raw = cells[idx].strip().lower()
        elif norm[idx] != cells[idx].strip().lower():
            # Recognised only because `_status_cell` normalised it — i.e. the
            # cell deviates from the contract's four words. The file's own
            # precedent (`in-progress`) is to PARSE the drift and still report
            # it; normalising silently would let the checker launder exactly the
            # vocabulary drift its docstring says it watches for.
            raw = cells[idx].strip().lower()
        if idx is None:
            notes.append(f"line {i}: row {tid!r} has no recognisable status cell")
            rows.append({"id": tid, "deps": [], "status": "", "disposition": "",
                         "raw_status": "", "line": i, "unreadable": True})
            continue
        rows.append({
            "id": tid,
            "deps": [
                d.strip().strip("*` ")
                for d in cells[idx - 1].split(",")
                if d.strip().lower() not in NO_DEPS
            ],
            "status": STATUS_ALIASES.get(raw, norm[idx]) if raw else norm[idx],
            "disposition": (cells[idx + 2].strip().lower() if len(cells) > idx + 2 else ""),
            "raw_status": raw,
            "line": i,
            "unreadable": False,
        })
    # Only a NON-BLANK stopping line means a row wrapped. Calibrated against the
    # 3 healthy plans that have a second table under `## Notes`: every one of
    # them ends its task table at a BLANK line, and counting pipe rows past that
    # reported 6 / 9 / 12 phantom rows — a checker firing on healthy input, which
    # `.claude/rules/audits.md` says gets itself switched off. The corrupted plan
    # stops on a 4,690-char prose continuation with `| T3 |` on the very next
    # line. Count only to the next blank line or heading, so a later unrelated
    # table can never be swept in.
    left = 0
    if stopped_at >= 0 and lines[stopped_at].strip():
        for x in lines[stopped_at + 1:]:
            t = x.strip()
            if not t or t.startswith("#"):
                break
            # A `|---|---|` separator is a pipe row but not a task row, and the
            # finding quotes this number to a human as rows they cannot see.
            # `replace`, not `strip`: the row-level check above tests a single
            # CELL (no pipes in it), and copying its `strip("|")` here left the
            # interior pipes of `|----|------|` in the set, so the separator was
            # still counted. Caught by the test, not by review.
            if t.startswith("|") and not set(t.replace("|", "")) <= {"-", ":", " "}:
                left += 1
    return rows, notes, has_disposition, left


# The public name. The underscored one stays because check_plan_staleness
# re-exports it and its tests address it there.
parse_tasks = _parse_tasks
