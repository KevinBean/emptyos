#!/usr/bin/env python3
"""Import Confluence "worklog" PDF-dump markdown into the canonical worklog format.

One-shot historical backfill: turns the messy text extracted from a Confluence
worklog PDF export (reverse-chronological dated entries, "Today's plan/update",
project-grouped status items, occasional timesheets) into:

  1. Per-day canonical notes the `worklog` app reads:
        {vault}/60_Worklogs/{year}/{YYYY-MM-DD}.md
  2. (optional) a single compact reverse-chronological archive note for a
        career-portfolio reference doc.

The messy parsing lives HERE, not in the shipped app — the app's parser only
ever reads the clean canonical format this script writes. Pure file I/O; never
boots the kernel.

Usage:
    python scripts/import_worklog_confluence.py \
        --root <dir-with-dump-*.md> \
        --vault "/path/to/vault" \
        --employer "<Employer Name>" \
        [--archive "20_Areas/Career/Previous-Roles/Worklog 2023 August-2024 July - ELEK.md"] \
        [--archive-source "Elek-Worklog-2023-08_2024-07.pdf"] \
        [--dry-run] [--overwrite] [--only 2023,2024]

Input files: any *.md under --root whose first lines are a tool-pdf-reader dump
(or pass explicit files as positional args). Idempotent: per-day files that
already exist are skipped unless --overwrite.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

# Single source of truth for the canonical status→emoji map: the worklog app's
# parser owns the format this importer targets, so import it from there (by
# path — this is a standalone script, not part of the daemon's app package)
# instead of re-declaring and risking drift.
_PARSER_PATH = Path(__file__).resolve().parent.parent / "apps" / "public" / "standard" / "worklog" / "parser.py"
_spec = importlib.util.spec_from_file_location("_worklog_parser", _PARSER_PATH)
_wp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wp)
STATUS_EMOJI = _wp.STATUS_EMOJI

# Confluence status words → canonical status. Order matters (longest/specific first).
_STATUS_WORDS = [
    ("ready for review", "review"),
    ("for review", "review"),
    ("in progress", "in-progress"),
    ("in-progress", "in-progress"),
    ("wait for", "waiting"),
    ("waiting on", "waiting"),
    ("waiting", "waiting"),
    ("blocked", "blocked"),
    ("complete", "complete"),
    ("completed", "complete"),
    ("done", "complete"),
    ("to do", "todo"),
    ("todo", "todo"),
    ("next", "next"),
    ("wip", "in-progress"),
]

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# A date header line, optionally with a trailing time range in parens.
# Two orderings occur in the wild: "27 Sep 2024" and "Sep 27 2024".
_DATE_RE = re.compile(
    r"^#*\s*(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})\s*(\([^)]*\))?\s*,?.*$"
)
_DATE_RE2 = re.compile(
    r"^#*\s*([A-Za-z]+)\.?\s+(\d{1,2})\s+(\d{4})\s*(\([^)]*\))?\s*,?.*$"
)
_PAGE_RE = re.compile(r"^##\s*Page\s+\d+\s*$", re.I)
# Segment headers inside a day block.
_PLAN_RE = re.compile(r"^#*\s*(today'?s|tomorrow'?s)\s+plan\b", re.I)
_UPDATE_RE = re.compile(r"^#*\s*today'?s\s+update\b", re.I)
_NOTE_RE = re.compile(r"^#*\s*note[:s]?\s*$", re.I)
# Project sub-header: "### 1. Foo", "1. Foo", "**Foo**", or a short "Foo:" label.
_PROJECT_HASH_RE = re.compile(r"^#{1,4}\s*(?:\d+\.\s*)?(.+?)\s*$")
_PROJECT_BOLD_RE = re.compile(r"^\*\*(.+?)\*\*:?\s*$")
_PROJECT_LABEL_RE = re.compile(r"^([A-Z][A-Za-z0-9 /&._-]{1,38}):\s*$")
# A timesheet line: "18 Jun - 8 hours - ..." or "AI Insights: 4h".
_TIMESHEET_HOURS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*h(?:ours?)?\b", re.I)


def detect_status(text: str) -> tuple[str, str | None]:
    """Return (clean_text, status|None). Handles prefix and suffix status words."""
    t = text.strip()
    low = t.lower()
    # Prefix: "Complete - foo" / "In progress - foo" / "Todo: foo"
    for word, status in _STATUS_WORDS:
        if low.startswith(word):
            rest = t[len(word):].lstrip()
            if rest[:1] in ("-", "–", ":"):
                return rest[1:].strip(), status
    # Suffix: "foo - Ready for review" / "foo - Complete"
    m = re.search(r"\s[-–]\s*([A-Za-z][A-Za-z ]+?)\s*$", t)
    if m:
        tail = m.group(1).strip().lower()
        for word, status in _STATUS_WORDS:
            if tail == word:
                return t[: m.start()].rstrip(), status
    return t, None


def normalize_text(text: str) -> str:
    """Fold curly quotes / nbsp to ASCII so section-marker regexes match
    ("Today’s update" with a curly apostrophe was silently missed)."""
    return (
        text.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("\xa0", " ")
    )


def clean_lines(block: str) -> list[str]:
    """Strip page markers / dump noise; drop empty-only runs."""
    out = []
    for raw in block.split("\n"):
        line = raw.rstrip()
        if _PAGE_RE.match(line.strip()):
            continue
        if line.strip() in ("", "\xa0"):
            out.append("")
            continue
        out.append(line)
    return out


def looks_like_project(line: str) -> str | None:
    """Return a project name if the line is a project sub-header, else None."""
    s = line.strip()
    m = _PROJECT_BOLD_RE.match(s)
    if m:
        return m.group(1).strip()
    if s.startswith("#"):
        m = _PROJECT_HASH_RE.match(s)
        if m:
            return m.group(1).strip().rstrip(":")
    # Short "Label:" with no status word and no sentence-y content.
    m = _PROJECT_LABEL_RE.match(s)
    if m:
        label = m.group(1).strip()
        if detect_status(label)[1] is None and " - " not in label:
            return label
    # Numbered header "1. Analytics Dashboard ..." — the dominant later format.
    # A project header only when the remainder is NOT a status item.
    m = re.match(r"^\d+\.\s+(.+)$", s)
    if m:
        rest = m.group(1).strip()
        if detect_status(rest)[1] is None and len(rest) <= 90:
            return rest
    return None


def parse_block(block: str) -> dict:
    """Parse one day's text into {plan, work:[{project, items:[{text,status}]}],
    notes:[str], timesheet:[{project,hours}], hours_note}."""
    lines = clean_lines(block)
    plan_lines: list[str] = []
    note_lines: list[str] = []
    work: list[dict] = []
    timesheet: list[dict] = []
    cur_group: dict | None = None
    section = "update"  # default; many early days are just "Today's update"
    hours_note = ""

    def ensure_group(name: str) -> dict:
        nonlocal cur_group
        for g in work:
            if g["project"].lower() == name.lower():
                cur_group = g
                return g
        g = {"project": name, "items": []}
        work.append(g)
        cur_group = g
        return g

    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Segment switches
        if _PLAN_RE.match(s):
            section = "plan"
            paren = re.search(r"\(([^)]*)\)", s)
            if paren and not hours_note:
                hours_note = paren.group(1).strip()
            continue
        if _UPDATE_RE.match(s):
            section = "update"
            paren = re.search(r"\(([^)]*)\)", s)
            if paren and not hours_note:
                hours_note = paren.group(1).strip()
            cur_group = None
            continue
        if _NOTE_RE.match(s):
            section = "note"
            continue
        # Timesheet line: "18 Jun - 8 hours - desc"
        mt = re.match(r"^(\d{1,2}\s+[A-Za-z]+)\s*[-–]\s*(\d+(?:\.\d+)?)\s*hours?\b\s*[-–]?\s*(.*)$", s)
        if mt:
            timesheet.append({"project": mt.group(1).strip(), "hours": float(mt.group(2)), "note": mt.group(3).strip()})
            continue

        if section == "plan":
            proj = looks_like_project(s)
            if proj:
                plan_lines.append(f"**{proj}**")
            else:
                plan_lines.append(s)
            continue
        if section == "note":
            note_lines.append(s)
            continue

        # section == "update": project grouping + status items
        proj = looks_like_project(s)
        if proj:
            ensure_group(proj)
            continue
        text, status = detect_status(s)
        if cur_group is None:
            ensure_group("General")
        cur_group["items"].append({"text": text, "status": status})

    return {
        "plan": "\n".join(plan_lines).strip(),
        "work": work,
        "notes": note_lines,
        "timesheet": timesheet,
        "hours_note": hours_note,
    }


def split_days(text: str):
    """Yield (date_obj, raw_header_line, block_text) for each dated entry."""
    lines = text.split("\n")
    idxs = []
    for i, line in enumerate(lines):
        s = line.strip()
        m = _DATE_RE.match(s)
        if m:
            day, mon, year = m.group(1), m.group(2).lower(), m.group(3)
        else:
            m2 = _DATE_RE2.match(s)
            if not m2:
                continue
            mon, day, year = m2.group(1).lower(), m2.group(2), m2.group(3)
        mon_n = _MONTHS.get(mon)
        if not mon_n:
            continue
        try:
            d = date(int(year), mon_n, int(day))
        except ValueError:
            continue
        idxs.append((i, d, line.strip()))
    for n, (i, d, header) in enumerate(idxs):
        end = idxs[n + 1][0] if n + 1 < len(idxs) else len(lines)
        block = "\n".join(lines[i + 1:end])
        yield d, header, block


def render_day(d: date, parsed: dict, employer: str, sources: list[str]) -> str:
    fm = [
        "---",
        f"date: {d.isoformat()}",
        "tags:",
        "  - worklog",
        f"employer: {employer}",
        "author: user",
    ]
    if sources:
        fm.append("source: Confluence export")
    fm.append("---")
    body = [f"# {d.isoformat()} {d.strftime('%A')}", ""]
    if parsed["plan"]:
        body += ["## Plan", "", parsed["plan"], ""]
    # Work
    if parsed["work"]:
        body += ["## Work", ""]
        for g in parsed["work"]:
            if not g["items"]:
                continue
            body.append(f"### {g['project']}")
            for it in g["items"]:
                emoji = STATUS_EMOJI.get(it["status"], "") if it["status"] else ""
                prefix = f"{emoji} " if emoji else ""
                body.append(f"- {prefix}{it['text']}")
            body.append("")
    if parsed["timesheet"]:
        body += ["## Timesheet", ""]
        for ts in parsed["timesheet"]:
            note = f" — {ts['note']}" if ts.get("note") else ""
            body.append(f"- {ts['project']}: {ts['hours']:g}h{note}")
        body.append("")
    if parsed["notes"]:
        body += ["## Notes", ""]
        for n in parsed["notes"]:
            body.append(f"- {n}")
        body.append("")
    if parsed["hours_note"]:
        body += [f"> Logged time: {parsed['hours_note']}", ""]
    return "\n".join(fm) + "\n\n" + "\n".join(body).rstrip() + "\n"


def render_archive_day(d: date, parsed: dict) -> str:
    """Compact reverse-chron rendering for the career-archive note (one day)."""
    out = [f"## {d.strftime('%-d %b %Y') if sys.platform != 'win32' else d.strftime('%#d %b %Y')}", ""]
    if parsed["plan"]:
        out += ["**Plan:** " + parsed["plan"].replace("\n", "; "), ""]
    for g in parsed["work"]:
        if not g["items"]:
            continue
        out.append(f"**{g['project']}**")
        for it in g["items"]:
            emoji = STATUS_EMOJI.get(it["status"], "") if it["status"] else ""
            prefix = f"{emoji} " if emoji else ""
            out.append(f"- {prefix}{it['text']}")
        out.append("")
    if parsed["timesheet"]:
        out.append("**Timesheet:** " + "; ".join(f"{t['project']} {t['hours']:g}h" for t in parsed["timesheet"]))
        out.append("")
    if parsed["notes"]:
        out += ["_Notes:_ " + " ".join(parsed["notes"]), ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="explicit dump .md files (else --root)")
    ap.add_argument("--root", help="dir to scan for dump *.md")
    ap.add_argument("--vault", required=True, help="vault root for writes")
    ap.add_argument("--employer", default="Elek")
    ap.add_argument("--archive", help="vault-rel path for the compact archive note")
    ap.add_argument("--archive-title", default="Worklog (imported)")
    ap.add_argument("--archive-source", help="PDF filename to cite in the archive note")
    ap.add_argument("--only", help="comma year filter, e.g. 2023,2024")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    files = [Path(f) for f in args.files]
    if args.root:
        files += sorted(Path(args.root).glob("*.md"))
    files = [f for f in files if f.exists()]
    if not files:
        print("No input files.", file=sys.stderr)
        return 2
    years = set(args.only.split(",")) if args.only else None
    vault = Path(args.vault)

    all_days: dict[date, dict] = {}
    for f in files:
        text = normalize_text(f.read_text(encoding="utf-8"))
        for d, header, block in split_days(text):
            if years and str(d.year) not in years:
                continue
            parsed = parse_block(block)
            # last-writer wins on duplicate dates across files (rare overlap)
            all_days[d] = parsed

    written = skipped = 0
    for d in sorted(all_days):
        rel = f"60_Worklogs/{d.year}/{d.isoformat()}.md"
        target = vault / rel
        if target.exists() and not args.overwrite:
            skipped += 1
            continue
        content = render_day(d, all_days[d], args.employer, [args.archive_source] if args.archive_source else [])
        nproj = sum(1 for g in all_days[d]["work"] if g["items"])
        nitem = sum(len(g["items"]) for g in all_days[d]["work"])
        if args.dry_run:
            print(f"  WOULD WRITE {rel}  ({nproj} proj, {nitem} items)")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        written += 1

    print(f"\nDays parsed: {len(all_days)}  |  to-write: {written}  |  skipped(existing): {skipped}")
    if all_days:
        ds = sorted(all_days)
        print(f"Date span: {ds[0].isoformat()} → {ds[-1].isoformat()}")

    # Compact archive note (reverse-chron)
    if args.archive:
        arc = vault / args.archive
        head = [
            "---",
            "tags:",
            "  - career",
            "  - worklog",
            "  - previous-role",
            f"company: {args.employer}",
            "type: worklog",
            "source: Confluence export",
            f"created: {date.today().isoformat()}",
            "---",
            "",
            f"# {args.archive_title}",
            "",
        ]
        if args.archive_source:
            head += [f"> Source PDF: [[{args.archive_source}]]", ""]
        parts = []
        for d in sorted(all_days, reverse=True):
            parts.append(render_archive_day(d, all_days[d]))
        arc_text = "\n".join(head) + "\n".join(parts)
        if args.dry_run:
            print(f"\n  WOULD WRITE archive {args.archive}  ({len(all_days)} days, {len(arc_text)} bytes)")
        else:
            arc.parent.mkdir(parents=True, exist_ok=True)
            arc.write_text(arc_text, encoding="utf-8")
            print(f"\nWrote archive note: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
