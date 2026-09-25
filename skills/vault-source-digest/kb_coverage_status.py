#!/usr/bin/env python3
"""kb_coverage_status.py — mechanical Status updater for a vault-source-digest
coverage ledger (the `<SLUG>.coverage.md` table `digest_pdf.py` writes into
`{vault}/30_Resources/EmptyOS/kb/sources/_fulltext/`).

Purpose: remove the transcription risk of hand-retyping a table row (special
characters, exact whitespace) via a text-editor Edit call, across dozens of
updates in a long digest session. This script addresses a row by its `ID`
column and rewrites ONLY that row's Status cell — every other line in the
file is preserved byte-for-byte.

Ownership: only the orchestrating Claude session runs this (sub-agents
writing clause notes just report back which IDs they finished; the parent
applies the update). That single-writer convention is what keeps a parallel
fan-out race-free without any file locking here.

Deliberately single-purpose: does NOT add/remove/reorder rows, and does NOT
touch Clause/Title/Page/Hint. Regenerating those is `digest_pdf.py
--regen-coverage`'s job (which resets Status too — never use it mid-digest).

Usage
-----
    python kb_coverage_status.py <ledger.md> --set ID STATUS [--set ID STATUS ...]

    python kb_coverage_status.py CIGRE_TB_669_2016.coverage.md \
        --set 5 written:cigre-tb-669-2016-3-1-1 \
        --set 6 written:cigre-tb-669-2016-3-1-2 \
        --set 12 skipped:pure-navigation-front-matter \
        --set 20 folded-into:cigre-tb-669-2016-8

STATUS must match one of:
    pending
    written:<slug>
    skipped:<reason>
    folded-into:<slug>

Exit codes: 0 ok | 2 usage/validation error (nothing written).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STATUS_RE = re.compile(r"^(pending|written:\S+|skipped:.+|folded-into:\S+)$")
# Matches one ledger row: "| ID | Clause | Title | Page | Hint | Status |"
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|(.*)\|(.*)\|(.*)\|(.*)\|(.*)\|\s*$")


def err(msg: str, code: int = 2):
    print(f"kb_coverage_status: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_rows(lines: list[str]) -> dict[int, int]:
    """Map row ID -> line index, for every table data row in the ledger."""
    ids: dict[int, int] = {}
    for i, ln in enumerate(lines):
        m = ROW_RE.match(ln)
        if not m:
            continue
        try:
            rid = int(m.group(1))
        except ValueError:
            continue
        ids[rid] = i
    return ids


def apply_updates(lines: list[str], ids: dict[int, int],
                   updates: list[tuple[int, str]]) -> list[str]:
    """Rewrite the Status cell (last `|`-delimited cell) of each targeted
    row. Every other cell and every other line is untouched. Assumes every
    (id, status) in `updates` has already been validated to exist / match
    STATUS_RE — callers must gate on that before calling this."""
    out = list(lines)
    for rid, status in updates:
        idx = ids[rid]
        m = ROW_RE.match(out[idx])
        id_s, clause, title, page, hint = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        out[idx] = f"|{id_s}|{clause}|{title}|{page}|{hint}| {status} |"
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Mechanically update Status cells in a vault-source-digest coverage ledger.")
    ap.add_argument("ledger", help="path to the <SLUG>.coverage.md ledger file")
    ap.add_argument("--set", dest="sets", nargs=2, action="append", metavar=("ID", "STATUS"),
                     default=[], help="row ID + new Status (repeatable)")
    args = ap.parse_args()

    ledger_path = Path(args.ledger)
    if not ledger_path.is_file():
        err(f"ledger not found: {ledger_path}")
    if not args.sets:
        err("no --set given; nothing to do")

    # Parse + validate every requested update BEFORE writing anything —
    # mirrors digest_pdf.py's own gate-first-write-once discipline, so a
    # typo'd ID or malformed status can't half-apply a batch.
    updates: list[tuple[int, str]] = []
    for id_s, status in args.sets:
        try:
            rid = int(id_s)
        except ValueError:
            err(f"not a valid row ID: {id_s!r}")
        if not STATUS_RE.match(status):
            err(f"malformed status for ID {rid}: {status!r} "
                f"(expected pending | written:<slug> | skipped:<reason> | folded-into:<slug>)")
        updates.append((rid, status))

    text = ledger_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    ids = parse_rows(lines)

    missing = [rid for rid, _ in updates if rid not in ids]
    if missing:
        err(f"unknown row ID(s) in {ledger_path.name}: {missing} "
            f"(known IDs: {sorted(ids)[:5]}{'...' if len(ids) > 5 else ''}) — nothing written")

    old_status = {}
    for rid, _ in updates:
        m = ROW_RE.match(lines[ids[rid]])
        old_status[rid] = m.group(6).strip()

    new_lines = apply_updates(lines, ids, updates)
    ledger_path.write_text("\n".join(new_lines), encoding="utf-8")

    for rid, status in updates:
        print(f"OK  ID {rid}: {old_status[rid]!r} -> {status!r}")


if __name__ == "__main__":
    main()
