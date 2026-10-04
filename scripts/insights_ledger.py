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
    python scripts/insights_ledger.py scorecard <lens> [--all]
    python scripts/insights_ledger.py record <lens> [--date YYYY-MM-DD] \
        (--from <report.md> | "proposal one" "proposal two" ...)
    python scripts/insights_ledger.py close <lens> <match> [--status shipped]
    python scripts/insights_ledger.py reconcile <lens> [--apply]

<lens> is "eos", "life", or "gap".

A note on `status`, added 2026-08-05: `record` has always written
``status: "open"`` and until now **nothing could ever change it** — there was no
close verb. So every entry read `[open]` forever, including the many whose own
text began "SHIPPED <sha>". The `gap` ledger had 57 entries in that state, which
makes the scorecard actively misleading: it is the input to prioritisation, and
a ledger where nothing ever closes ranks finished work alongside real work.

`close` is the explicit fix. `reconcile` is the bulk one, and it **proposes by
default** — it only reads self-declared markers in an entry's own text, which is
a heuristic about prose, so `--apply` is a deliberate second step rather than
something that happens on a read.
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


# Markers a proposal's own text uses to announce its outcome. Matched at the
# START of the text (after the lead-in), so a passing mention of the word
# "shipped" mid-sentence doesn't close an open item.
_OUTCOME_MARKERS: list[tuple[str, re.Pattern]] = [
    ("shipped", re.compile(r"\b(shipped|resolved|implemented|done)\b", re.I)),
    ("declined", re.compile(r"\b(declined|rejected|not building|build.nothing)\b", re.I)),
    ("corrected", re.compile(r"\b(corrected|reclassified|superseded|withdrawn)\b", re.I)),
]
_VALID_STATUS = ("open", "shipped", "declined", "corrected", "dropped")


def _detect_outcome(text: str) -> str:
    """Outcome a proposal declares about ITSELF, or "" if it declares none.

    Deliberately positional: many gap entries are written
    ``"<app>: <gap> — SHIPPED <sha> (...)"``, so the marker sits in the tail
    after an em-dash. Anything before the first dash is the proposal's own
    subject and must not be scanned, or a gap literally named "no-scheduled-
    refresh — SHIPPED" would be indistinguishable from one merely discussing
    shipping.
    """
    tail = re.split(r"—|--|—", text, maxsplit=1)
    if len(tail) < 2:
        return ""
    head = tail[1].strip()[:60]
    for status, pat in _OUTCOME_MARKERS:
        if pat.search(head):
            return status
    return ""


def close(lens: str, match: str, status: str) -> list[str]:
    """Set status on every entry whose hash or text contains `match`."""
    path = _ledger_path(lens)
    state = load_state(path)
    hit = []
    needle = match.strip().lower()
    for key, entry in state.items():
        if needle == key or needle in (entry.get("text") or "").lower():
            entry["status"] = status
            entry["closed_on"] = date.today().isoformat()
            hit.append(entry.get("text", "")[:100])
    if hit:
        save_state(path, state)
    return hit


def reconcile(lens: str, apply: bool) -> list[tuple[str, str]]:
    """Find open entries whose own text declares an outcome. Read-only unless
    `apply` — the detection is a prose heuristic, so the write is a second step."""
    path = _ledger_path(lens)
    state = load_state(path)
    found: list[tuple[str, str]] = []
    for entry in state.values():
        if entry.get("status", "open") != "open":
            continue
        outcome = _detect_outcome(entry.get("text") or "")
        if not outcome:
            continue
        found.append((outcome, entry.get("text", "")[:110]))
        if apply:
            entry["status"] = outcome
            entry["closed_on"] = date.today().isoformat()
    if apply and found:
        save_state(path, state)
    return found


def _days_between(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except Exception:
        return 0


def scorecard(lens: str, today: str, show_all: bool = False) -> str:
    path = _ledger_path(lens)
    state = load_state(path)
    if not state:
        return f"(no prior {lens} insights ledger — this is the first run)"
    every = sorted(state.values(), key=lambda e: e.get("last_seen", ""), reverse=True)
    # Closed entries are history, not agenda. Listing them by default is what
    # made the gap scorecard unreadable — 57 rows of mostly-finished work with
    # nothing distinguishing it from what still needs doing.
    rows = every if show_all else [e for e in every if e.get("status", "open") == "open"]
    n_closed = len(every) - len(rows)
    head = f"## Prior proposals scorecard ({lens}) — {len(rows)} open"
    if n_closed:
        head += f", {n_closed} closed (--all to list)"
    out = [head + "\n"]
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
            print("usage: insights_ledger.py scorecard <lens> [--all]", file=sys.stderr)
            return 2
        print(scorecard(argv[1], today, show_all="--all" in argv[2:]))
        return 0
    if cmd == "close":
        if len(argv) < 3:
            print("usage: insights_ledger.py close <lens> <match> [--status shipped]",
                  file=sys.stderr)
            return 2
        rest = argv[3:]
        status = "shipped"
        if "--status" in rest:
            i = rest.index("--status")
            status = rest[i + 1]
        if status not in _VALID_STATUS:
            print(f"status must be one of {', '.join(_VALID_STATUS)}", file=sys.stderr)
            return 2
        hit = close(argv[1], argv[2], status)
        if not hit:
            print(f"no {argv[1]} entry matched {argv[2]!r}", file=sys.stderr)
            return 1
        for t in hit:
            print(f"  {status}: {t}")
        print(f"closed {len(hit)} entr{'y' if len(hit) == 1 else 'ies'}")
        return 0
    if cmd == "reconcile":
        if len(argv) < 2:
            print("usage: insights_ledger.py reconcile <lens> [--apply]", file=sys.stderr)
            return 2
        apply = "--apply" in argv[2:]
        found = reconcile(argv[1], apply)
        if not found:
            print(f"no open {argv[1]} entries declare an outcome in their own text")
            return 0
        for status, text in found:
            print(f"  {status:<9} {text}")
        verb = "closed" if apply else "would close"
        print(f"\n{verb} {len(found)} entr{'y' if len(found) == 1 else 'ies'}"
              + ("" if apply else " — re-run with --apply"))
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
