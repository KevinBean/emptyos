#!/usr/bin/env python3
"""Late-work check — how much committing happens after a chosen cutoff.

Deliberately a ~120-line script and not an app, a hub panel, or a cron job. The
2026-08-16 insights pass found that building mechanism is the thing that crowds
out the behaviour it is meant to support, so this stays something you run by
hand when you want the number.

    python scripts/late_work_check.py                # last 8 weeks vs 22:30
    python scripts/late_work_check.py --weeks 4
    python scripts/late_work_check.py --cutoff 23:00 --weeknights

Reads `git log` author dates in local time. Stdlib only; never imports the
kernel, so it is safe to run while the daemon is up.

Why git and not `footprint_common.py` (which reconstructs real session hours from
Claude transcripts, and is the more accurate hours source): `~/.claude` rotates at
roughly a month, so transcripts cannot answer a multi-week trend — see
`feedback_session_logs_are_not_evidence_of_a_past_period`. Git history is durable
and regenerates for free. The tradeoff is that a commit is a *proxy* for working:
this measures when work was committed, not hours worked, and a batch commit at
23:00 overstates while a long uncommitted session understates.

The headline metric is **nights, not commits** — "how many active nights ran past
the cutoff". A commit-share percentage looked equivalent and was not: it moves with
how much you happened to commit that week, so a quiet late night and a busy late
night score differently. Nights answer the behavioural question directly and are
stable against denominator choices.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import subprocess
import sys

# The window that counts as "late": from CUTOFF on one day to MORNING_END the next.
MORNING_END_H = 4

# Evenings followed by a work morning (Mon=0 … Sun=6): Sun, Mon, Tue, Wed, Thu.
WORK_EVE_WEEKDAYS = frozenset({6, 0, 1, 2, 3})

# A week with this few active nights is a boundary artefact (the --since cut lands
# mid-week); its percentage is meaningless — shown in the table, kept out of the trend.
MIN_WEEK_NIGHTS = 2


def parse_cutoff(text: str) -> tuple[int, int]:
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
        return h, m
    except Exception:
        raise SystemExit(f"bad --cutoff {text!r}; want HH:MM, e.g. 22:30")


def git_commits(weeks: int) -> list[dt.datetime]:
    """Author timestamps, local time, newest first."""
    try:
        out = subprocess.run(
            ["git", "log", f"--since={weeks} weeks ago",
             "--pretty=format:%ad", "--date=format:%Y-%m-%d %H:%M"],
            capture_output=True, text=True, check=True,
        ).stdout
    except FileNotFoundError:
        raise SystemExit("git not found on PATH")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"git log failed: {e.stderr.strip() or e}")

    stamps = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            stamps.append(dt.datetime.strptime(line, "%Y-%m-%d %H:%M"))
        except ValueError:
            continue
    return stamps


def is_late(ts: dt.datetime, cutoff: tuple[int, int]) -> bool:
    ch, cm = cutoff
    after_cutoff = (ts.hour, ts.minute) >= (ch, cm)
    before_morning = ts.hour < MORNING_END_H
    return after_cutoff or before_morning


def attributed_night(ts: dt.datetime) -> dt.date:
    """A 01:00 commit belongs to the previous evening, not its own date."""
    return (ts - dt.timedelta(days=1)).date() if ts.hour < MORNING_END_H else ts.date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks", type=int, default=8, help="weeks of history (default 8)")
    ap.add_argument("--cutoff", default="22:30", help="late starts at HH:MM (default 22:30)")
    ap.add_argument("--weeknights", action="store_true",
                    help="only nights followed by a work morning (Sun-Thu evenings)")
    args = ap.parse_args()

    cutoff = parse_cutoff(args.cutoff)
    stamps = git_commits(args.weeks)
    if args.weeknights:
        # Filter on the attributed night, not the raw date: a 01:00 Friday commit is
        # Thursday-night work (counts), while 23:00 Friday precedes a weekend (does not).
        # Filtering by raw weekday would get both backwards.
        stamps = [t for t in stamps if attributed_night(t).weekday() in WORK_EVE_WEEKDAYS]
    if not stamps:
        print("no commits in range")
        return 0

    per_week: dict[tuple[int, int], list[dt.datetime]] = collections.defaultdict(list)
    for t in stamps:
        iso = t.isocalendar()
        per_week[(iso[0], iso[1])].append(t)

    scope = "weeknights (Sun-Thu eve)" if args.weeknights else "all nights"
    print(f"\nLate work after {args.cutoff} (through {MORNING_END_H:02d}:00) · {scope} · {args.weeks}w\n")
    print(f"  {'week':<10}{'nights':>8}{'late':>7}{'late %':>9}{'commits':>9}")
    print("  " + "-" * 43)

    rows = []
    for (yr, wk) in sorted(per_week):
        items = per_week[(yr, wk)]
        nights = {attributed_night(t) for t in items}
        late_nights = {attributed_night(t) for t in items if is_late(t, cutoff)}
        pct = 100.0 * len(late_nights) / len(nights)
        thin = len(nights) < MIN_WEEK_NIGHTS
        if not thin:
            rows.append(pct)
        mark = " ·thin" if thin else ""
        bar = "#" * int(pct / 5)
        print(f"  {yr}-W{wk:<5}{len(nights):>8}{len(late_nights):>7}{pct:>8.0f}%"
              f"{len(items):>9}  {bar}{mark}")

    all_nights = {attributed_night(t) for t in stamps}
    late_nights_all = {attributed_night(t) for t in stamps if is_late(t, cutoff)}

    print("  " + "-" * 43)
    print(f"  {'TOTAL':<10}{len(all_nights):>8}{len(late_nights_all):>7}"
          f"{100.0*len(late_nights_all)/len(all_nights):>8.0f}%{len(stamps):>9}")
    print(f"\n  {len(late_nights_all)} of {len(all_nights)} active nights ran past {args.cutoff}.")

    if len(rows) >= 4:
        recent, prior = rows[-2:], rows[:-2]
        r, p = sum(recent) / len(recent), sum(prior) / len(prior)
        delta = r - p
        arrow = "down" if delta < -2 else ("up" if delta > 2 else "flat")
        print(f"  Last 2 weeks {r:.0f}% vs {p:.0f}% before — {arrow}.")

    print("\n  Baseline measured 2026-08-16 (90d, 22:00-04:00): 76 of 87 active days")
    print("  included late work; 40% of all commits fell in that window.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
