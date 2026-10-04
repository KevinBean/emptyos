"""Session-plan `## Tasks` table parser — one reading for every consumer.

Extracted from `check_plan_staleness.py` (which re-exports it under its old
private names) when `session_board.py` became the second reader: the board had
its own pipe-split that only counted `T<n>` ids by column index, so a plan whose
tasks are `P1..P8` showed no tasks at all. Row anchoring, the status
vocabulary, blocker-tag stripping and the wrapped-row count are all explained
at their definitions below; they moved verbatim.

Contract: `.claude/rules/session-plans.md` § File format.

Moved from `scripts/` to the top level (2026-09-28) when devboard became the
third reader: an app must not import from `scripts/`. It stays stdlib-only
(it imports `re`), so it lives beside `frontmatter.py` rather than inside
`emptyos/sdk/` — no import-time saving is claimed: every current reader loads
the SDK for other reasons. See `.claude/rules/top-level-modules.md`.
"""
from __future__ import annotations

import re

# `.claude/rules/session-plans.md` § File format. Kept as literals rather than
# imported from fix_queue.DISPOSITIONS because the rule subtracts one value
# (`planned`, meaningless for a task that *is* the plan) — importing the superset
# would silently accept it.
STATUSES = frozenset({"queued", "active", "done", "blocked"})
DISPOSITIONS = frozenset({"shipped", "deferred", "declined", "dismissed", "done"})


def disposition_word(cell: str) -> str:
    """The vocabulary word of a disposition cell. `/eos-session-wrapup` has long
    written `shipped — <note>` (11 rows across five plans, 2026-09-28): the
    word before the first ` — ` is the disposition, the rest is a note. Only the
    em dash with spaces separates — `shipped-ish` is still one (bad) word."""
    return cell.split(" — ", 1)[0].strip()


def is_sync_conflict(stem: str) -> bool:
    """A vault-sync conflict copy (`x.sync-conflict-…`, `x 2`): a stale snapshot
    carrying stale claims, which every plan reader must skip
    (`.claude/rules/session-plans.md` § No `_plans/_index.md`)."""
    return any(m in stem for m in ("sync-conflict", ".conflict")) or bool(re.search(r" \d+$", stem))


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

# A claim lives on its own row: `status: active` plus a `session` cell of
# `YYYY-MM-DD #sid8` — the claim date and the first 8 hex characters of the
# Claude Code session id (the form the status bar shows). One marker per plan
# (`active_task`) could name only one task, so two sessions working one plan in
# parallel had to leave one of them unmarked (P1 + P6, 2026-09-26). The same
# cell carries the closing session once the row is `done`.
CLAIM_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
CLAIM_SID = re.compile(r"#([0-9a-fA-F]{8})\b")


def parse_claim(text: str) -> tuple[str, str]:
    """`(date, sid8)` from a session cell or a legacy `active_task` value.

    Either part is `""` when absent: a claim written by a session that had no
    id to hand (Codex, or a hook that did not fire) still carries its date, and
    the date is what the staleness check needs.
    """
    d = CLAIM_DATE.search(text or "")
    s = CLAIM_SID.search(text or "")
    return (d.group(1) if d else "", s.group(1).lower() if s else "")


def legacy_claim_row(rows: list[dict], active_task: str) -> dict | None:
    """The row a legacy frontmatter `active_task` names, or None.

    Exact leading token, never `startswith`: an `active_task` of
    "T12 (claimed …)" starts with "T1", so a prefix match names the WRONG row —
    and T1 is typically long done, so it reads as a confident, specific, false
    finding.
    """
    head = re.match(r"[\w.-]+", active_task or "")
    return next((r for r in rows if head and r["id"] == head.group(0)), None)


def row_claims(rows: list[dict], active_task: str = "") -> list[dict]:
    """One `{id, date, sid, stamped}` per `active` row.

    `stamped` is False when the row carries no dated claim and no legacy
    `active_task` names it — a half-written claim or close. A legacy marker
    lends its date to the row it names only when the row has none of its own:
    the row is the claim now.
    """
    named = legacy_claim_row(rows, active_task)
    out = []
    for r in rows:
        if r["status"] != "active":
            continue
        date, sid = parse_claim(r["session"])
        stamped = bool(date) or named is r
        if not date and named is r:
            date, sid = parse_claim(active_task)
        out.append({"id": r["id"], "date": date, "sid": sid, "stamped": stamped})
    return out

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
                         "session": "", "raw_status": "", "line": i,
                         "unreadable": True})
            continue
        rows.append({
            "id": tid,
            # The contract's second column; the board shows it as "next task".
            "task": cells[1] if idx > 1 else "",
            "deps": [
                d.strip().strip("*` ")
                for d in cells[idx - 1].split(",")
                if d.strip().lower() not in NO_DEPS
            ],
            "status": STATUS_ALIASES.get(raw, norm[idx]) if raw else norm[idx],
            "session": (cells[idx + 1].strip() if len(cells) > idx + 1 else ""),
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
