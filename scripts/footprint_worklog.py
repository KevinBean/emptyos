"""Reconstruct time-on-task from AI-session transcripts and log it to the worklog.

The coding agents already keep a timestamped record of every action they took on
your behalf. That record IS a timesheet — it just needs to be read. This turns it
into ``60_Worklogs/{year}/{date}.md`` entries.

    python scripts/footprint_worklog.py --topic "article" \
        --match "system-evolution-|how-architecture-emerges" \
        --project "EmptyOS Brand" --employer EmptyOS --dry-run

Method (why the numbers are defensible)
---------------------------------------
* An **event** is one JSONL record from a Claude Code or Codex session, with its
  ISO timestamp.
* An event is **on-topic** when its raw text matches ``--match``.
* Consecutive on-topic events <= ``--idle`` apart form one continuous **run**,
  counted at full wall-clock — the work *between* two mentions of a file is still
  work on that file. (Attributing by the *fraction* of lines that name the file
  badly undercounts: a session that is 100% one task names it in ~30% of lines.)
* A run gets ``--tail`` credit for its final event, so an isolated mention costs
  minutes, not zero.
* Runs from every tool are **unioned** on one timeline, so Claude and Codex
  running side by side are counted once. There is only one human.

Writes nothing without ``--apply`` (see .claude/rules/proposed-action.md). Day
files are plain markdown and merge-safe: an existing day keeps its other projects.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts/ modules
from footprint_common import scan  # noqa: E402  (iter_session_files/runs_from/union live here too)

STATUS_EMOJI = {
    "complete": "✅", "in-progress": "🔄", "todo": "📋", "next": "⏭️",
    "waiting": "⏳", "review": "👀", "blocked": "⛔",
}


# ── worklog day file ──────────────────────────────────────────────────
def day_path(vault: Path, d: date) -> Path:
    return vault / "60_Worklogs" / str(d.year) / f"{d.isoformat()}.md"


def new_day(d: date, employer: str) -> str:
    emp = f"employer: {employer}\n" if employer else ""
    return (
        f"---\ndate: {d.isoformat()}\ntags:\n  - worklog\n{emp}author: both\n---\n\n"
        f"# {d.isoformat()} {d.strftime('%A')}\n\n## Plan\n\n\n## Work\n\n"
    )


def _split(md: str) -> tuple[str, dict[str, str], list[str]]:
    """head (frontmatter+title) + {section: body} + section order."""
    lines = md.split("\n")
    head, secs, order, cur = [], {}, [], None
    for ln in lines:
        if ln.startswith("## "):
            cur = ln[3:].strip()
            secs[cur] = ""
            order.append(cur)
            continue
        if cur is None:
            head.append(ln)
        else:
            secs[cur] += ln + "\n"
    return "\n".join(head).rstrip() + "\n", secs, order


def _render(head: str, secs: dict[str, str], order: list[str]) -> str:
    out = head.rstrip() + "\n\n"
    for name in order:
        out += f"## {name}\n{secs[name].rstrip()}\n\n" if secs[name].strip() else f"## {name}\n\n"
    return out.rstrip() + "\n"


def upsert_day(md: str, project: str, items: list[tuple[str, str]], hours: float, note: str) -> str:
    """Merge one project's items + timesheet line into a day, leaving others alone."""
    head, secs, order = _split(md)
    for req in ("Plan", "Work", "Timesheet"):
        if req not in secs:
            secs[req] = ""
            order.append(req)

    # --- Work: replace only this project's ### group
    body = secs["Work"]
    group = f"### {project}\n" + "".join(
        f"- {STATUS_EMOJI.get(st, '')} {tx}\n".replace("-  ", "- ") for tx, st in items
    )
    pat = re.compile(rf"^### {re.escape(project)}\n(?:(?!^### ).*\n?)*", re.M)
    body = pat.sub(group, body) if pat.search(body) else (body.rstrip() + "\n\n" + group).lstrip("\n")
    secs["Work"] = body

    # --- Timesheet: upsert `- Project: Xh — note`
    line = f"- {project}: {hours:g}h" + (f" — {note}" if note else "")
    ts = [l for l in secs["Timesheet"].split("\n") if l.strip()]
    ts = [l for l in ts if not l.strip().lower().startswith(f"- {project.lower()}:")]
    ts.append(line)
    secs["Timesheet"] = "\n".join(ts) + "\n"
    return _render(head, secs, order)


# ── main ──────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", required=True, help="regex identifying on-topic events")
    ap.add_argument("--project", required=True, help="worklog ### project heading")
    ap.add_argument("--topic", default="", help="human label used in the timesheet note")
    ap.add_argument("--employer", default="", help="frontmatter employer (omit = personal)")
    ap.add_argument("--item", action="append", default=[],
                    help="work item as 'status:text' (repeatable); lands on the LAST day")
    ap.add_argument("--vault", default="", help="vault root (default: emptyos.toml notes.path)")
    ap.add_argument("--claude-project", default="D--emptyos", help="~/.claude/projects/<dir>, '' = all")
    ap.add_argument("--idle", type=int, default=15, help="minutes of silence that end a run")
    ap.add_argument("--tail", type=float, default=3, help="minutes credited to a run's last event")
    ap.add_argument("--tz", type=float, default=10.0, help="UTC offset hours (Sydney = 10)")
    ap.add_argument("--min-hours", type=float, default=0.05, help="skip days below this")
    ap.add_argument("--since", default="", help="YYYY-MM-DD lower bound")
    ap.add_argument("--apply", action="store_true", help="write the day files (default: preview)")
    a = ap.parse_args()

    vault = Path(a.vault) if a.vault else None
    if vault is None:
        import tomllib
        cfg = Path(__file__).resolve().parent.parent / "emptyos.toml"
        with open(cfg, "rb") as fh:
            vault = Path(tomllib.load(fh)["notes"]["path"])
    if not vault.exists():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    tz = timezone(timedelta(hours=a.tz))
    pattern = re.compile(a.match, re.I)
    per_day, per_tool, runs, overlap = scan(
        pattern, timedelta(minutes=a.idle), timedelta(minutes=a.tail), tz,
        a.claude_project or None,
        date.fromisoformat(a.since) if a.since else None,
    )
    per_day = {d: v for d, v in per_day.items() if v["h"] >= a.min_hours}
    if not per_day:
        print("no on-topic sessions matched")
        return 1

    total = sum(v["h"] for v in per_day.values())
    days = sorted(per_day)
    label = a.topic or a.project

    print(f"\n=== FOOTPRINT: {label} ===")
    print(f"{'date':<12}{'dow':<5}{'span':<14}{'runs':>5}{'hours':>7}  tools")
    for d in days:
        v = per_day[d]
        print(f"{d}  {d.strftime('%a'):<5}{v['first']:%H:%M}-{v['last']:%H:%M}  "
              f"{v['runs']:>5}{v['h']:>7.2f}  {'+'.join(sorted(v['tools']))}")
    print(f"{'TOTAL':<12}{'':<19}{sum(v['runs'] for v in per_day.values()):>5}{total:>7.2f}")
    print(f"\nper tool: " + ", ".join(f"{t} {h:.2f}h" for t, h in sorted(per_tool.items()))
          + f"  (concurrent overlap removed: {overlap:.2f}h)")

    items = []
    for raw in a.item:
        st, _, tx = raw.partition(":")
        items.append((tx.strip(), st.strip()) if tx else (raw.strip(), "complete"))

    print(f"\n=== {'WRITING' if a.apply else 'PREVIEW (--apply to write)'} ===")
    for d in days:
        v = per_day[d]
        p = day_path(vault, d)
        md = p.read_text(encoding="utf-8") if p.exists() else new_day(d, a.employer)
        note = f"{label} — {v['first']:%H:%M}–{v['last']:%H:%M}, {v['runs']} session(s)"
        day_items = items if (items and d == days[-1]) else [
            (f"{label}: {v['runs']} working session(s)", "in-progress")
        ]
        out = upsert_day(md, a.project, day_items, round(v["h"] * 10) / 10, note)
        state = "new " if not p.exists() else "merge"
        print(f"  [{state}] {p.relative_to(vault)}  →  - {a.project}: {round(v['h']*10)/10:g}h")
        if a.apply:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(out, encoding="utf-8")
    if not a.apply:
        print("\n  nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
