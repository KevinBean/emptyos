#!/usr/bin/env python3
"""Prediction ledger for the insights skills — make the self-audit loop close.

Each /eos-insights or /eos-life-insights run RECORDs the proposals it made;
the next run reads the SCORECARD first ("last time I proposed X — still open /
recurred N times / how old") so reporting becomes forecast-and-check instead of
a one-shot. Reuses emptyos/sdk/miner_state.py (sig_hash + load/save_state), so a
lightly-reworded proposal still matches its prior entry.

Pure stdlib + the pure miner_state module — no kernel boot, safe to shell.
State lives under data/ (telemetry, gitignored), never the vault.

Usage:
    python scripts/insights_ledger.py scorecard <lens>
    python scripts/insights_ledger.py record <lens> [--date YYYY-MM-DD] \
        (--from <report.md> | "proposal one" "proposal two" ...)

<lens> is "eos" or "life".
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from emptyos.sdk.miner_state import load_state, save_state, sig_hash  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_DATA = _REPO / "data" / "apps" / "insights"


def _ledger_path(lens: str) -> Path:
    return _DATA / f"ledger-{lens}.json"


def _normalize(text: str) -> str:
    """Stable matching key: drop list numbering/bullets + markdown emphasis,
    lowercase, collapse whitespace, keep the leading ~90 chars so a reworded
    tail still groups with its prior entry."""
    t = text.strip()
    t = re.sub(r"^\s*(?:\d+\.|[-*])\s+", "", t)          # leading bullet/number
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)             # bold
    t = re.sub(r"\*([^*]+)\*", r"\1", t)                 # italic
    t = re.sub(r"`([^`]+)`", r"\1", t)                   # code
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)       # links
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t[:90]


def _proposals_from_md(md_path: Path) -> list[str]:
    """Pull list items from the report's 'Suggested next steps' / proposals
    section. Returns the human text of each proposal (first sentence-ish)."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        if ln.startswith("## "):
            title = ln[3:].lower()
            in_section = any(k in title for k in ("suggested", "next step", "proposal", "recommend"))
            continue
        if in_section and re.match(r"^\s*(?:\d+\.|[-*])\s+", ln):
            item = re.sub(r"^\s*(?:\d+\.|[-*])\s+", "", ln).strip()
            # prefer the bolded lead ("**Quick fix — drop …**") if present
            m = re.match(r"\*\*([^*]+)\*\*", item)
            out.append(m.group(1).strip() if m else item)
    return out


def record(lens: str, proposals: list[str], when: str) -> int:
    path = _ledger_path(lens)
    state = load_state(path)
    n_new = 0
    for p in proposals:
        norm = _normalize(p)
        if not norm:
            continue
        key = sig_hash(norm)
        entry = state.get(key)
        if entry is None:
            state[key] = {
                "text": p.strip(),
                "first_seen": when,
                "last_seen": when,
                "count": 1,
                "report_dates": [when],
                "status": "open",
            }
            n_new += 1
        else:
            if when not in entry.get("report_dates", []):
                entry.setdefault("report_dates", []).append(when)
                entry["count"] = int(entry.get("count", 1)) + 1
            entry["last_seen"] = when
            entry["text"] = p.strip()  # keep the latest phrasing
    save_state(path, state)
    return n_new


def _days_between(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except Exception:
        return 0


def scorecard(lens: str, today: str) -> str:
    path = _ledger_path(lens)
    state = load_state(path)
    if not state:
        return f"(no prior {lens} insights ledger — this is the first run)"
    rows = sorted(state.values(), key=lambda e: e.get("last_seen", ""), reverse=True)
    out = [f"## Prior proposals scorecard ({lens}) — {len(rows)} tracked\n"]
    for e in rows:
        age = _days_between(e.get("last_seen", today), today)
        recur = int(e.get("count", 1))
        recur_note = f"recurred {recur}× across {', '.join(e.get('report_dates', []))}" if recur > 1 else f"first proposed {e.get('first_seen','?')}"
        status = e.get("status", "open")
        out.append(f"- [{status}] {e.get('text','')[:120]}  ·  {recur_note}  ·  last surfaced {age}d ago")
    out.append(
        "\nRead before the new run: a proposal **recurring** across reports without being acted on is a stronger signal than a fresh one. "
        "Open the report by reconciling these (acted-on → note it; still-relevant → carry forward; stale → drop)."
    )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    today = date.today().isoformat()
    if cmd == "scorecard":
        if len(argv) < 2:
            print("usage: insights_ledger.py scorecard <lens>", file=sys.stderr)
            return 2
        print(scorecard(argv[1], today))
        return 0
    if cmd == "record":
        if len(argv) < 2:
            print("usage: insights_ledger.py record <lens> [--date D] (--from <md> | <proposal>...)", file=sys.stderr)
            return 2
        lens = argv[1]
        rest = argv[2:]
        when = today
        if "--date" in rest:
            i = rest.index("--date")
            when = rest[i + 1]
            del rest[i : i + 2]
        proposals: list[str] = []
        if "--from" in rest:
            i = rest.index("--from")
            md = Path(rest[i + 1]).resolve()
            del rest[i : i + 2]
            proposals = _proposals_from_md(md)
        proposals += [r for r in rest if not r.startswith("--")]
        if not proposals:
            print("no proposals found to record", file=sys.stderr)
            return 1
        n_new = record(lens, proposals, when)
        print(f"recorded {len(proposals)} proposal(s) ({n_new} new) to {_ledger_path(lens)}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
