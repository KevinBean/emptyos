#!/usr/bin/env python3
"""Aging audit for the deferred-work registry (`docs/DEFERRED-WORK.md`).

The sibling of ``check_dark_flags.py``. A dark flag asks "was this ever turned
on?"; a deferred row asks "did its trigger ever fire?" — and the second question
had no mechanical half at all. Registered as deferred itself on 2026-06-27, with
the trigger *"this registry grows big enough that aging rows need mechanical
flagging"*. Promoted 2026-08-05, when the eos-insights readiness pass measured
the registry at **134 deferred rows, 117 added in one month, 3 ever closed** —
an intake of roughly four a day against a drain of one every seven weeks. At
that ratio the readiness recheck is a hand-read of 134 prose triggers, which is
how a row that was ready sat unnoticed next to a hundred that weren't.

Read the split of labour carefully, because it is the whole design:

  * **This scanner never judges readiness.** A trigger is prose ("a *felt* recall
    gap in vault search", "a **second** paying buyer") and deliberately so —
    encoding it as a predicate would either be wrong or would flatten the
    judgment into a checkbox. Readiness stays with a human (or the eos-insights
    §9 pass), exactly as the original deferral specified.
  * **It flags what is mechanical**: how old a row is, and whether the table
    itself has drifted out of shape.

A "does the Reference path exist now?" signal was built first and **cut after
measuring it**: it fired on 77 of 128 deferred rows (60%), because a Reference
is usually a *model to copy* rather than a precondition. Per
`.claude/rules/audits.md` a heuristic that fires on the majority is noise, and
shipping it would have made the whole scanner ignorable. Age and table shape
are the two things that are genuinely mechanical; that is all this reports.

Classification (per row):

  closed       Status is not `deferred` (built / shipped / dropped / triggered)
  AGING        deferred and older than --age-days (default 90)
  soaking      deferred, younger than --age-days — the normal state
  undated      deferred with no parseable Added date — the row cannot age at all

Malformed rows are reported separately: a row with fewer cells than the header
declares has its Added/Status in the wrong columns, so it can never be aged or
closed correctly by anything — including this scanner.

Exit code = AGING + undated + malformed (exit-code-as-signal). Registered
gate=False in preflight: an old deferred row is information, never a build
break — the entire point of the registry is that deferring is a valid outcome.

Pure file I/O. Does NOT import emptyos.kernel (no syslog handle; safe while the
daemon is up — .claude/rules/daemon-handling.md).

Usage::

    python scripts/check_deferred_work.py                # human table
    python scripts/check_deferred_work.py --json         # agent-cli envelope
    python scripts/check_deferred_work.py --age-days 60
    python scripts/check_deferred_work.py --all          # include closed rows
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime

from check_base import REPO_ROOT
from scanner_lib import emit_json

DOC = REPO_ROOT / "docs" / "DEFERRED-WORK.md"

# Fallback only. The real column names are read from the table's own header row
# so a doc edit that adds or renames a column doesn't silently mis-slot every
# Added/Status value.
_DEFAULT_COLUMNS = ["feature", "trigger", "reference", "verdict", "added", "status"]

_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def _slug(cell: str) -> str:
    """`Build / deploy trigger` -> `trigger`; `Full verdict / source` -> `verdict`."""
    c = re.sub(r"[*`]", "", cell).strip().lower()
    for key in ("feature", "trigger", "reference", "verdict", "added", "status"):
        if key in c:
            return key
    return re.sub(r"[^a-z0-9]+", "_", c).strip("_") or "col"


def parse_rows(text: str) -> tuple[list[dict], list[tuple[int, str]]]:
    """Return (rows, malformed) — malformed carries the 1-based line number so a
    reader can go fix the row rather than hunt for it."""
    rows: list[dict] = []
    malformed: list[tuple[int, str]] = []
    columns = list(_DEFAULT_COLUMNS)
    seen_header = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line.startswith("|") or _SEPARATOR.match(line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        slugs = [_slug(c) for c in cells]
        if "feature" in slugs[:1] and "status" in slugs:  # the header row
            columns = slugs
            seen_header = True
            continue
        if not seen_header:
            continue
        if len(cells) < len(columns):
            malformed.append((lineno, cells[0][:96]))
            continue
        rows.append(dict(zip(columns, cells[: len(columns)])))
    return rows, malformed


def _added_date(row: dict) -> date | None:
    m = _DATE.search(row.get("added", ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def classify(row: dict, today: date, age_days: int) -> tuple[str, int | None]:
    """Return (class, age_in_days)."""
    status = re.sub(r"[*`]", "", row.get("status", "")).strip().lower()
    if status and status != "deferred":
        return "closed", None

    added = _added_date(row)
    if added is None:
        return "undated", None
    age = (today - added).days
    return ("AGING" if age >= age_days else "soaking"), age


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--age-days", type=int, default=90,
                    help="a deferred row older than this is AGING (default 90)")
    ap.add_argument("--all", action="store_true", help="include closed rows in the table")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope on stdout")
    args = ap.parse_args()

    if not DOC.exists():
        msg = f"{DOC.relative_to(REPO_ROOT)} not found"
        return emit_json(False, "not_found", msg, None) if args.json else (print(msg) or 1)

    today = date.today()
    parsed, malformed = parse_rows(DOC.read_text(encoding="utf-8"))

    rows = []
    for r in parsed:
        cls, age = classify(r, today, args.age_days)
        added = _added_date(r)
        rows.append({
            "feature": re.sub(r"[*`]", "", r.get("feature", ""))[:78],
            "trigger": r.get("trigger", "")[:150],
            "class": cls,
            "age_days": age,
            "added": added.isoformat() if added else "",
            "status": r.get("status", ""),
        })

    flagged = [r for r in rows if r["class"] in ("AGING", "undated")]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    order = ("AGING", "undated", "soaking", "closed")
    summary = f"{len(rows)} rows: " + ", ".join(
        f"{counts[k]} {k}" for k in order if k in counts
    )
    if malformed:
        summary += f" · {len(malformed)} malformed"

    if args.json:
        return emit_json(
            not (flagged or malformed),
            "aging",
            summary,
            {
                "rows": rows,
                "malformed": [{"line": n, "starts": s} for n, s in malformed],
                "age_days": args.age_days,
            },
        )

    print(f"deferred-work audit - {len(rows)} rows (aging after {args.age_days}d)\n")
    shown = rows if args.all else [r for r in rows if r["class"] not in ("closed", "soaking")]
    shown.sort(key=lambda r: (order.index(r["class"]) if r["class"] in order else 9,
                              -(r["age_days"] or 0)))
    for r in shown:
        age = f"{r['age_days']}d" if r["age_days"] is not None else "-"
        print(f"  {r['class']:<9} {age:>5}  {r['feature']}")
    if malformed:
        print("\nmalformed rows — fewer cells than the header declares, so their")
        print("Added/Status sit in the wrong columns and can never be aged or closed:")
        for lineno, starts in malformed:
            print(f"  docs/DEFERRED-WORK.md:{lineno}  {starts}")
    if flagged:
        print(f"\n{len(flagged)} row(s) worth a human readiness read. This scanner does NOT")
        print("judge readiness — triggers are prose on purpose. For each: decide, then set")
        print("Status to `triggered` (build it), or leave it deferred.")
    if not flagged and not malformed:
        print("  (nothing aging, no malformed rows)")
    print(f"\n{summary}")
    return len(flagged) + len(malformed)


if __name__ == "__main__":
    sys.exit(main())
