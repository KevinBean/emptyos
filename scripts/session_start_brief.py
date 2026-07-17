#!/usr/bin/env python3
"""SessionStart(startup|resume|clear) hook — surface resume tracks + deferred work.

Two pull-based registries are easy to forget at the one moment they matter most
— the start of a session. This hook prints a near-zero-token pointer (NOT their
content) so they get noticed without taxing every turn:

  1. The active per-track resume index written by /eos-session-wrapup
     ({vault}/10_Projects/emptyos/log/_next/_index.md) — most-recent few tracks.
  2. The deferred-work registry (docs/DEFERRED-WORK.md) — count of `deferred`
     rows + how many are aging, as a nudge to grep it before building a
     substantive feature (the readiness recheck itself lives in eos-insights).

Stdlib only, every read time-boxed by being tiny, always exits 0. Degrades
silently (prints nothing extra) when a file is missing — e.g. a fresh clone
with no vault mounted. Resolves the project root from CLAUDE_PROJECT_DIR.

The canonical (richer) parsers for these two tables live in
``emptyos/sdk/dev_tracks.py`` (used by apps/extension/dev/devboard/). This
hook keeps its own freestanding copy by design — it must run on a fresh clone
before ``pip install -e .`` — so format changes must be mirrored there.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

from hook_common import project_root, vault_path

_TOP_TRACKS = 8
_AGING_DAYS = 30


def _resume_tracks(root: Path) -> tuple[list[tuple[str, str]], int, str]:
    """Return (top tracks [(name, last_touched)], total count, index path str).

    Parses ONLY the 'Next-session tracks' table (the file embeds many other
    tables in track descriptions), anchored on its '| Track | Last touched'
    header, collecting consecutive pipe rows until the table ends.
    """
    vault = vault_path(root)
    if not vault:
        return [], 0, ""
    idx = vault / "10_Projects" / "emptyos" / "log" / "_next" / "_index.md"
    if not idx.exists():
        return [], 0, ""
    try:
        lines = idx.read_text(encoding="utf-8").splitlines()
    except Exception:
        return [], 0, str(idx).replace("\\", "/")

    rows: list[tuple[str, str]] = []
    in_table = False
    for ln in lines:
        if not in_table:
            low = ln.lower()
            if low.startswith("| track") and "last touched" in low:
                in_table = True
            continue
        if not ln.lstrip().startswith("|"):
            break  # table ended
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name, touched = cells[0], cells[1]
        if set(name) <= {"-", ":", " "}:  # separator row
            continue
        # keep only rows whose 2nd cell parses as a date (real track rows)
        if not _parse_date(touched):
            continue
        rows.append((name, touched))

    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:_TOP_TRACKS], len(rows), str(idx).replace("\\", "/")


def _parse_date(s: str) -> datetime.date | None:
    s = s.strip().lstrip("~").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _deferred_counts(root: Path) -> tuple[int, int]:
    """Return (deferred rows, of which aging > _AGING_DAYS by `Added` date)."""
    doc = root / "docs" / "DEFERRED-WORK.md"
    if not doc.exists():
        return 0, 0
    try:
        lines = doc.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0, 0
    today = datetime.date.today()
    deferred = aging = 0
    for ln in lines:
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 6:
            continue
        status = cells[-1].lower()
        if status != "deferred":
            continue
        deferred += 1
        added = _parse_date(cells[-2])
        if added and (today - added).days > _AGING_DAYS:
            aging += 1
    return deferred, aging


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    root = project_root(payload)
    tracks, total, idx_path = _resume_tracks(root)
    deferred, aging = _deferred_counts(root)

    if not tracks and not deferred:
        return 0  # nothing useful to surface — stay silent

    parts: list[str] = ["[EmptyOS session brief]", ""]

    if tracks:
        parts.append(f"Resume tracks (top {len(tracks)} of {total}, newest first):")
        width = max(len(n) for n, _ in tracks)
        for name, touched in tracks:
            parts.append(f"  {name.ljust(width)}  {touched}")
        if idx_path:
            parts.append(f"  → full index: {idx_path}")
        parts.append("  → /eos-session-resume [track] to pick one up.")
        parts.append("")

    if deferred:
        aging_note = f" ({aging} aging >{_AGING_DAYS}d)" if aging else ""
        parts.append(
            f"Deferred work: {deferred} deferred{aging_note} — grep "
            "docs/DEFERRED-WORK.md before building a substantive feature "
            "(eos-insights rechecks readiness)."
        )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
