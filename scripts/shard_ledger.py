#!/usr/bin/env python3
"""Append-only ledger for parallel shard runs.

Every status write is ONE ``open(..., "a")`` + one JSON line, so N concurrent
agents can never clobber each other's entry. This is the same idiom as
``emptyos/sdk/fix_queue.py::append_ledger`` (``done/_ledger.jsonl``), and it is
deliberately NOT ``scripts/insights_ledger.py``: that one persists through
``miner_state.save_state``, a whole-file ``write_text`` rewrite, which is a
read-modify-write and loses entries under concurrency.

Reconciling these rows into the gap registry / insights gap lens is the
ARBITER's job, done serially after the run -- never by a shard agent.

Usage:
    python scripts/shard_ledger.py append <run_id> <shard> <status> [--note TEXT] [--gap ID]
    python scripts/shard_ledger.py status <run_id> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DIR = _REPO / "data" / "shard-runs"

STATUSES = (
    "dispatched", "working", "tests-green", "ready",
    "rebasing", "merged", "sent-back", "blocked", "abandoned",
)


def _path(run_id: str) -> Path:
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
    if not safe:
        raise SystemExit("run_id must contain alphanumerics")
    return _DIR / f"{safe}.jsonl"


def append(run_id: str, shard: str, status: str, note: str = "", gap: str = "") -> dict:
    if status not in STATUSES:
        raise SystemExit(f"status must be one of: {', '.join(STATUSES)}")
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "shard": shard,
        "status": status,
        "gap": gap,
        "note": note,
    }
    p = _path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    # One open-append + one line: atomic enough that concurrent shards interleave
    # rows instead of overwriting them.
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read(run_id: str) -> list[dict]:
    p = _path(run_id)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn line never breaks a read
    return out


def fold(rows: list[dict]) -> dict[str, dict]:
    """Last row wins per shard -- append-only, so later rows supersede."""
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r.get("shard", "?")] = r
    return latest


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("run_id"); a.add_argument("shard"); a.add_argument("status")
    a.add_argument("--note", default=""); a.add_argument("--gap", default="")
    s = sub.add_parser("status")
    s.add_argument("run_id"); s.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "append":
        row = append(args.run_id, args.shard, args.status, args.note, args.gap)
        print(json.dumps(row, ensure_ascii=False))
        return 0

    rows = read(args.run_id)
    latest = fold(rows)
    if args.json:
        print(json.dumps({"rows": len(rows), "shards": latest}, ensure_ascii=False, indent=2))
        return 0
    if not latest:
        print(f"(no entries for run {args.run_id})")
        return 0
    print(f"{'SHARD':<22} {'STATUS':<12} NOTE")
    for shard in sorted(latest):
        r = latest[shard]
        print(f"{shard:<22} {r['status']:<12} {r.get('note','')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
