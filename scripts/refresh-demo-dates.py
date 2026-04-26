#!/usr/bin/env python3
"""Shift all 📅/✅ dates in demo/vault/ to be relative to today.

The seed task files were authored with concrete dates around the
SEED_BASELINE. Without rewriting, the demo's "today" tile drains and
"overdue" balloons as real time moves forward — by week 3 the pulse
stats look wrong again.

This script keeps the relative spread (2 days overdue, 1 due today,
3 due this week, etc.) but anchors it to whatever today is when the
script runs. Same shape, fresh dates.

Usage (run on the VPS, ideally inside the daily reset cron):
    python3 scripts/refresh-demo-dates.py

Combine with `git checkout demo/vault/` to also reset visitor edits:
    cd /opt/emptyos
    git checkout demo/vault/
    python3 scripts/refresh-demo-dates.py
    docker compose -f docker-compose.demo.yml --env-file .env.demo restart emptyos-demo

Idempotent: rerunning shifts dates again (the seed files always start
from the committed baseline because git checkout reverts them first).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

# Baseline = the date the seed task files were authored with. All dates
# in the seed are relative to this. When this script runs, every date is
# shifted by (today - SEED_BASELINE) so the relative spread is preserved.
SEED_BASELINE = date(2026, 4, 27)

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "demo" / "vault"

# Match either 📅 or ✅ followed by ISO date. The emoji bytes are explicit
# so we don't depend on the source encoding being right.
DATE_RE = re.compile(r"(\U0001F4C5|✅)\s*(\d{4})-(\d{2})-(\d{2})")


def shift_dates(content: str, delta_days: int) -> tuple[str, int]:
    """Shift every 📅/✅ ISO date in content by delta_days. Returns new content + count."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        emoji = m.group(1)
        try:
            old = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            return m.group(0)
        new = old + timedelta(days=delta_days)
        count += 1
        return f"{emoji} {new.isoformat()}"

    return DATE_RE.sub(repl, content), count


def main() -> None:
    if not VAULT.exists():
        raise SystemExit(f"vault not found: {VAULT}")

    today = date.today()
    delta = (today - SEED_BASELINE).days

    if delta == 0:
        print(f"  today ({today}) == baseline ({SEED_BASELINE}) — nothing to shift")
        return

    print(f"  shifting dates by {delta} days  (baseline {SEED_BASELINE} -> today {today})")

    total_files = 0
    total_dates = 0
    for md in VAULT.rglob("*.md"):
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue
        if "\U0001F4C5" not in content and "✅" not in content:
            continue
        new_content, n = shift_dates(content, delta)
        if n == 0 or new_content == content:
            continue
        md.write_text(new_content, encoding="utf-8")
        rel = md.relative_to(ROOT)
        print(f"    {rel}  ({n} dates)")
        total_files += 1
        total_dates += n

    print(f"  done — {total_dates} dates across {total_files} files")


if __name__ == "__main__":
    main()
