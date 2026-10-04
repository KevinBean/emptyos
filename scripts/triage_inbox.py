"""Inbox triage — group every inbox line by producer, and archive machine output.

Plan `project-session-integration` P7, project `inbox-triage`. The capture inbox
(`10_Projects/inbox/inbox.md`) held ~1,100 lines of machine output next to ~30
human tasks. P6 moved the producers' write paths out of the inbox; this script
clears the backlog they already left there.

Every open line (`- [ ]`) falls into one group, first match wins:

  dogfood          dogfood-agent friction (`#dogfood…`)
  job-scout-empty  a Job Scout "no fresh fits" run note
  job-lead         a Job Scout lead (`[JOB] …`)
  staff            a staff-agent proposal (Growth / Connect / Root)
  human            everything else — triaged with Kevin one by one

and a closed line (`- [x]`, `- [-]`) is group `closed`. Any other mark (`[/]`,
`[>]`) is open. The producer markers (`PRODUCERS`) are the one definition of
"machine output": the session board's orphan count reads `MACHINE_LINE` here.

Every group but `human` goes to the archive, `10_Projects/inbox/log/triage-
archive.md` (Kevin, 2026-09-28: archive, don't file — the leads are not imported
into the listing store). Lines move verbatim except an open checkbox, written
`\\[ \\]` so the vault-wide task scan no longer counts them as open tasks;
stripping the two backslashes restores the original line. A closed line keeps
its checkbox, so its completion history stays readable. Nothing is deleted.

Human lines carry hints, never a verdict: `duplicate` (same text as an earlier
human line), `past-due` (a 📅 date before today; never on a 🔁 task, whose 📅 is
the next occurrence), `test-capture` (reads like a smoke-test line left by a
session).

The report also lists open recurring (🔁) tasks in `50_Journal/`, grouped by
text, for Kevin to close the obsolete ones. Which are obsolete is his call.

Run: python scripts/triage_inbox.py [--report PATH] [--json]      (dry run)
     python scripts/triage_inbox.py --apply dogfood,staff,…        (moves)
The dry run writes nothing but `--report`. `--apply` refuses `human`, writes the
archive first and the inbox second (a crash between leaves a duplicate, never a
loss), re-checks both files before each write, and re-reads the inbox after it.

What it cannot do is take the daemon's lock on the inbox, so a capture the
daemon writes in the milliseconds between the last check and the file replace
is overwritten, and not detectable afterwards. Since P6 only human captures
write the inbox: run `--apply` when you are not capturing.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from scanner_lib import emit_json  # noqa: E402
from vault_paths import require_vault_root  # noqa: E402

from emptyos.runtime.atomic_io import atomic_write_text  # noqa: E402

INBOX = Path("10_Projects/inbox/inbox.md")
ARCHIVE = Path("10_Projects/inbox/log/triage-archive.md")

# Machine producers' line markers, measured on the inbox 2026-09-28 (~1,100 of
# 1,137 open lines). Case-sensitive, and a human task that merely mentions
# "dogfooding" or a job is not taken for machine output. Order matters:
# `[JOB SCOUT]` must be tested before `[JOB]`.
PRODUCERS = (
    ("dogfood", r"#dogfood(?:-[\w-]+)?(?![\w-])"),
    ("job-scout-empty", r"\[JOB SCOUT\]"),
    ("job-lead", r"\[JOB\]"),
    # "🌱 Growth:" / "🌱 Growth (ESCALATION):" / "🕸️ Connect:" / "🌿 Root:"
    ("staff", r"^(?:🌱 Growth|🕸️ Connect|🌿 Root)\b"),
)
_COMPILED = tuple((group, re.compile(p)) for group, p in PRODUCERS)
MACHINE_LINE = re.compile("|".join(f"(?:{p})" for _, p in PRODUCERS))
MACHINE_GROUPS = (*(g for g, _ in PRODUCERS), "closed")

TASK = re.compile(r"^\s*- \[(.)\] (.*)$")
CHECKBOX = re.compile(r"^(\s*)- \[(.)\] ")
DUE = re.compile(r"📅 (\d{4}-\d{2}-\d{2})")
TEST_CAPTURE = re.compile(r"\bsmoke\b|^test the\b|^verify the\b", re.I)
ARCHIVE_HEAD = ("---\ntags:\n  - inbox-archive\n---\n\n# Inbox triage archive\n\n"
                "Machine output moved out of the inbox by `scripts/triage_inbox.py`. "
                "Open checkboxes are escaped so these are not open tasks.\n")


def classify(text: str) -> str | None:
    """The producer group whose marker the task text carries, or None for a human line."""
    for group, rx in _COMPILED:
        if rx.search(text):
            return group
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def triage(lines: list[str], today: datetime.date) -> dict:
    """Group the inbox's task lines; `lines` is the file, one entry per line."""
    groups: dict[str, list[dict]] = defaultdict(list)
    seen_human: dict[str, int] = {}
    for n, ln in enumerate(lines, 1):
        m = TASK.match(ln)
        if not m:
            continue
        mark, text = m.group(1), m.group(2)
        row: dict = {"line": n, "text": text}
        if mark in "xX-":
            groups["closed"].append(row)
            continue
        group = classify(text)
        if group is None:
            hints = []
            key = _norm(text)
            if key in seen_human:
                hints.append(f"duplicate of line {seen_human[key]}")
            else:
                seen_human[key] = n
            due = DUE.search(text)
            if due and due.group(1) < today.isoformat() and "🔁" not in text:
                hints.append("past-due")
            if TEST_CAPTURE.search(text):
                hints.append("test-capture")
            row["hints"] = hints
            group = "human"
        groups[group].append(row)
    return dict(groups)


def archive_line(line: str) -> str:
    """The inbox line with an OPEN checkbox escaped, so it is no longer an open
    task. A closed line (`[x]`, `[-]`) keeps its checkbox: it was never open, and
    the task index reads it as completion history."""
    return CHECKBOX.sub(
        lambda m: m[0] if m[2] in "xX-" else f"{m[1]}- \\[{m[2]}\\] ", line, count=1)


def split_lines(text: str) -> list[str]:
    """Lines with their endings, broken on `\\n` only — as the editor and the
    daemon count them. `str.splitlines` also breaks on `\\x0b`, `\\x0c`, `\\x85`,
    U+2028…, which would move half a line and leave the tail behind."""
    return [ln for ln in re.split(r"(?<=\n)", text) if ln]


class InboxChanged(RuntimeError):
    pass


def apply(vault: Path, move_groups: list[str], today: datetime.date) -> dict[str, int]:
    """Move every line of `move_groups` from the inbox to the archive."""
    bad = [g for g in move_groups if g not in MACHINE_GROUPS]
    if bad or not move_groups:
        raise ValueError(f"not an archivable group: {', '.join(bad) or '(none given)'} "
                         f"(choose from {', '.join(MACHINE_GROUPS)})")
    inbox = vault / INBOX
    raw = inbox.read_bytes()
    try:
        lines = split_lines(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise ValueError(f"inbox is not valid UTF-8 ({e}); nothing written") from e
    groups = triage([ln.rstrip("\r\n") for ln in lines], today)
    moving = {g: [r["line"] for r in groups.get(g, [])] for g in move_groups}
    gone = {n for ns in moving.values() for n in ns}
    if not gone:
        return {g: 0 for g in move_groups}

    section = [f"\n## {today.isoformat()} — moved from the inbox\n"]
    for g, ns in moving.items():
        if ns:
            section.append(f"\n### {g} ({len(ns)})\n\n")
            section += [archive_line(lines[n - 1].rstrip("\r\n")) + "\n" for n in ns]
    archive = vault / ARCHIVE
    archive_raw = archive.read_bytes() if archive.exists() else None
    head = archive_raw.decode("utf-8") if archive_raw is not None else ARCHIVE_HEAD
    kept = "".join(ln for n, ln in enumerate(lines, 1) if n not in gone)

    if inbox.read_bytes() != raw:
        raise InboxChanged("inbox changed before the move; nothing written — re-run")
    if (archive.read_bytes() if archive.exists() else None) != archive_raw:
        raise InboxChanged("archive changed before the move; nothing written — re-run")
    atomic_write_text(archive, head + "".join(section))
    if inbox.read_bytes() != raw:
        raise InboxChanged(f"inbox changed after the archive was written; the inbox is "
                           f"untouched and today's section in {ARCHIVE.as_posix()} duplicates "
                           f"it — remove that section, then re-run")
    atomic_write_text(inbox, kept)
    # A daemon write racing the replace can put a stale copy back (all moved
    # lines return) — say so rather than report success.
    if inbox.read_bytes() != kept.encode("utf-8"):
        raise InboxChanged("inbox changed while it was being rewritten; check it: the "
                           "archive holds the moved lines, and any that came back are "
                           "duplicates of it")
    return {g: len(ns) for g, ns in moving.items()}


def journal_routines(vault: Path) -> list[dict]:
    """Open recurring (🔁) tasks under `50_Journal/`, grouped by text with dates
    stripped, most frequent first."""
    root = vault / "50_Journal"
    found: dict[str, dict] = {}
    if not root.is_dir():
        return []
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(vault).as_posix()
        for ln in text.splitlines():
            m = TASK.match(ln)
            if not m or m.group(1) != " " or "🔁" not in m.group(2):
                continue
            body = re.sub(r"[📅⏳🛫✅] \d{4}-\d{2}-\d{2}", "", m.group(2)).strip()
            entry = found.setdefault(_norm(body), {"text": body, "count": 0, "files": []})
            entry["count"] += 1
            if rel not in entry["files"]:
                entry["files"].append(rel)
    return sorted(found.values(), key=lambda e: (-e["count"], e["text"]))


def render(groups: dict, routines: list[dict], today: datetime.date) -> str:
    total = sum(len(v) for v in groups.values())
    out = [f"# Inbox triage — dry run {today.isoformat()}", "",
           f"Source: `{INBOX.as_posix()}` · {total} task lines · nothing has been moved.", "",
           "| Group | Lines | Exact duplicates | Home |", "|---|---:|---:|---|"]
    for g in (*MACHINE_GROUPS, "human"):
        rows = groups.get(g, [])
        if rows:
            dups = len(rows) - len({_norm(r["text"]) for r in rows})
            home = "stays — triage with Kevin" if g == "human" else f"`{ARCHIVE.as_posix()}`"
            out.append(f"| {g} | {len(rows)} | {dups} | {home} |")

    out += ["", "## Human tasks", "", "| Line | Task | Hints |", "|---:|---|---|"]
    for r in groups.get("human", []):
        out.append(f"| {r['line']} | {r['text'].replace('|', '\\|')} | {', '.join(r['hints'])} |")

    out += ["", "## Recurring journal routines (open, 🔁)", ""]
    if routines:
        out += ["| Count | Routine | Files |", "|---:|---|---|"]
        for e in routines:
            files = ", ".join(f"`{f}`" for f in e["files"][:3])
            more = f" +{len(e['files']) - 3}" if len(e["files"]) > 3 else ""
            out.append(f"| {e['count']} | {e['text'].replace('|', '\\|')} | {files}{more} |")
    else:
        out.append("None found under `50_Journal/`.")

    for g in MACHINE_GROUPS:
        rows = groups.get(g, [])
        if rows:
            out += ["", f"## {g} ({len(rows)})", ""]
            out += [f"- L{r['line']}: {r['text'][:160]}" for r in rows]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", type=Path, help="write the full per-line plan as markdown here")
    ap.add_argument("--apply", metavar="GROUPS",
                    help=f"comma-separated groups to archive ({', '.join(MACHINE_GROUPS)})")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = require_vault_root()
    inbox = vault / INBOX
    if not inbox.exists():
        msg = f"no inbox at {inbox}"
        if args.json:
            return emit_json(False, "no_inbox", msg)
        print(msg, file=sys.stderr)
        return 1
    today = datetime.date.today()

    if args.apply is not None:
        try:
            moved = apply(vault, [g.strip() for g in args.apply.split(",") if g.strip()], today)
        except OSError as e:
            e = InboxChanged(f"{type(e).__name__}: {e} — if the archive gained today's "
                             f"section but the inbox still has the lines, remove that "
                             f"section before re-running")
            if args.json:
                return emit_json(False, "apply_failed", str(e))
            print(e, file=sys.stderr)
            return 1
        except (ValueError, InboxChanged) as e:
            if args.json:
                return emit_json(False, "apply_refused", str(e))
            print(e, file=sys.stderr)
            return 1
        if args.json:
            return emit_json(True, "inbox_archived", f"{sum(moved.values())} lines archived", moved)
        for g, n in moved.items():
            print(f"{n:5d}  {g} → {ARCHIVE.as_posix()}")
        return 0

    text = inbox.read_bytes().decode("utf-8", errors="replace")
    groups = triage([ln.rstrip("\r\n") for ln in split_lines(text)], today)
    routines = journal_routines(vault)
    counts = Counter({g: len(v) for g, v in groups.items()})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render(groups, routines, today), encoding="utf-8")
    if args.json:
        return emit_json(True, "inbox_triage", f"{sum(counts.values())} lines, dry run",
                         {"counts": dict(counts), "groups": groups, "routines": routines})
    for g, n in counts.most_common():
        print(f"{n:5d}  {g}")
    print(f"{len(routines):5d}  recurring journal routines")
    if args.report:
        print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
