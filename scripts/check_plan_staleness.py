"""Session-plan hygiene — the closure signal `_plans/` has no other source for.

`.claude/rules/session-plans.md` promises a plan is *bounded*: it finishes, moves
to `_plans/done/`, and leaves on its own. Nothing enforced that promise. This
scanner is the advisory that notices when it slips.

Deliberately advisory (`gate=False` in preflight). A plan is a human artifact
written mid-session under interruption; every finding here is "a human should
look", never "the tree is broken". It reports and never rewrites — same posture
as `check_memory_rot.py`, and the same reason: the fix is a judgment call.

What it reports (the four the plan task named, plus three contract checks that
are free once the table is parsed):

  unclosed_plan     no queued/active/blocked task left, yet still in `_plans/`.
                    This is the 87-of-121 pile forming again, one plan at a time.
  stale_claim       `active_task` non-empty and its claim date older than
                    --max-claim-days. Means a session died mid-task: the marker
                    is a mutual-exclusion claim, so a stale one BLOCKS the plan
                    for every future session until a human clears it.
  claim_mismatch    `active_task` and the row's `status: active` disagree. A
                    half-written claim — the resume protocol writes both, so one
                    without the other is an interrupted write.
  unresolved_dep    `depends_on` names an id that is not in the table.
  dep_cycle         tasks that can never become claimable.
  done_no_disposition / bad_status / bad_disposition
                    vocabulary drift against the rule's own contract.

Why it does not run `reconcile_tracks.py`: that scanner is the evidence half of
the same job, but it makes hundreds of git calls and its own docstring rules it
out of preflight. Frontmatter parsing goes through the scanner family's canonical
`md_frontmatter.parse_frontmatter`; `_is_conflict` is reused from reconcile_tracks
so "what is a sync-conflict copy" has exactly one definition across both. That
second one matters more than it looks: a conflict copy carries a STALE
`active_task`, and a consumer that reads it as a separate plan sees a phantom
claim blocking real work.

Run: python scripts/check_plan_staleness.py [--json] [--max-claim-days N]
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md_frontmatter import parse_frontmatter  # noqa: E402
from reconcile_tracks import _is_conflict  # noqa: E402
from scanner_lib import emit_json  # noqa: E402
from vault_paths import vault_root  # noqa: E402

# `.claude/rules/session-plans.md` § File format. Kept as literals rather than
# imported from fix_queue.DISPOSITIONS because the rule subtracts one value
# (`planned`, meaningless for a task that *is* the plan) — importing the superset
# would silently accept it.
STATUSES = frozenset({"queued", "active", "done", "blocked"})
DISPOSITIONS = frozenset({"shipped", "deferred", "declined", "dismissed", "done"})
OPEN_STATUSES = frozenset({"queued", "active", "blocked"})

# Variants seen in the wild that mean a real status but are not the contract's
# word. Recognised so the row still PARSES — dropping it cascades: a dropped row
# is an id nothing resolves to, so every task depending on it reports a phantom
# `unresolved_dep`. Measured 2026-08-07: one `in-progress` cell produced two
# findings, and the second one pointed at an innocent task.
STATUS_ALIASES = {
    "in-progress": "active", "in progress": "active", "wip": "active",
    "open": "queued", "todo": "queued", "pending": "queued",
    "cancelled": "done", "canceled": "done",
}

DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
NO_DEPS = {"", "—", "-", "–", "none", "n/a"}


def _parse_tasks(body: str) -> tuple[list[dict], list[str], bool]:
    """Parse the `## Tasks` table. Returns (rows, unparsable_notes, has_disposition).

    Anchors each row on the cell holding a known *status*, never on column
    index. Task cells are long prose carrying `|` in code spans and tables of
    their own; `dev_tracks.parse_track_index` already learned this the same way
    ("col-3 prose embeds pipes, so a naive pipe-split is only trusted for the
    first two cells"). Anchoring on the vocabulary means a pipe anywhere in the
    prose shifts nothing.

    Fail-soft per row, per the devboard `_collect_tracks_sync` model: one
    malformed row must never hide the others.
    """
    rows: list[dict] = []
    notes: list[str] = []
    in_table = False
    has_disposition = False
    for i, ln in enumerate(body.splitlines(), start=1):
        stripped = ln.strip()
        if not in_table:
            low = stripped.lower()
            if low.startswith("| id") and "task" in low:
                in_table = True
                has_disposition = "disposition" in low
            continue
        if not stripped.startswith("|"):
            if rows:
                break  # table ended
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":", " "}:
            continue  # separator row
        tid = cells[0].strip("*` ")
        if not tid or tid.lower() == "id":
            continue
        idx = next(
            (n for n, c in enumerate(cells) if c.strip().lower() in STATUSES), None
        )
        raw = ""
        if idx is None:
            idx = next(
                (n for n, c in enumerate(cells) if c.strip().lower() in STATUS_ALIASES),
                None,
            )
            if idx is not None:
                raw = cells[idx].strip().lower()
        if idx is None or idx == 0:
            notes.append(f"line {i}: row {tid!r} has no recognisable status cell")
            rows.append({"id": tid, "deps": [], "status": "", "disposition": "",
                         "raw_status": "", "line": i})
            continue
        rows.append({
            "id": tid,
            "deps": [
                d.strip().strip("*` ")
                for d in cells[idx - 1].split(",")
                if d.strip().lower() not in NO_DEPS
            ],
            "status": STATUS_ALIASES.get(raw, cells[idx].strip().lower()) if raw
                      else cells[idx].strip().lower(),
            "disposition": (cells[idx + 2].strip().lower() if len(cells) > idx + 2 else ""),
            "raw_status": raw,
            "line": i,
        })
    return rows, notes, has_disposition


def _cycles(rows: list[dict]) -> list[list[str]]:
    """Every dependency cycle, each reported once by its rotation-minimal form.

    Three-colour DFS (unvisited / on-stack / done) rather than enumerating every
    path. The path-walking version was exponential on a *cycle-free* dense DAG —
    measured 0.001s at 12 tasks, 0.041s at 18, 0.715s at 22, so a 30-task plan
    where tasks depend on most of their predecessors would stall preflight, and
    this check runs in the `always` scope at every session start. Marking a node
    done once explored makes it O(V+E).

    Every cycle in a directed graph contains at least one back edge in any DFS,
    so each one is still reported; cycles sharing a back edge collapse into a
    single finding, which is what a reader wants anyway.
    """
    ids = {r["id"] for r in rows}
    graph = {r["id"]: [d for d in r["deps"] if d in ids] for r in rows}
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []

    def walk(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for nxt in graph[node]:
            if color[nxt] == GREY:
                cyc = stack[stack.index(nxt):]
                rot = min(range(len(cyc)), key=lambda k: cyc[k:] + cyc[:k])
                key = tuple(cyc[rot:] + cyc[:rot])
                if key not in seen:
                    seen.add(key)
                    out.append(list(key))
            elif color[nxt] == WHITE:
                walk(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for r in rows:
        if color[r["id"]] == WHITE:
            walk(r["id"], [])
    return out


def _claim_age(active: str, path: Path, today: datetime.date) -> tuple[int | None, str]:
    """Days since the claim was written, and the date it was read from.

    Prefers a date inside the `active_task` value (the resume protocol writes
    one) and falls back to file mtime. Not the reverse: the vault syncs, so
    mtime moves for reasons that have nothing to do with the claim.
    """
    m = DATE.search(active)
    if m:
        try:
            d = datetime.date.fromisoformat(m.group(1))
            return (today - d).days, m.group(1)
        except ValueError:
            pass
    try:
        d = datetime.date.fromtimestamp(path.stat().st_mtime)
        return (today - d).days, f"{d.isoformat()} (mtime)"
    except OSError:
        return None, ""


def scan_plans(
    plans_dir: Path, *, max_claim_days: int = 2, today: datetime.date | None = None
) -> dict:
    """Pure scan. Returns {plans, findings, skipped}."""
    today = today or datetime.date.today()
    findings: list[dict] = []
    plans: list[dict] = []
    skipped: list[str] = []

    for f in sorted(plans_dir.glob("*.md")):
        if f.name.startswith("_"):
            continue
        if _is_conflict(f):
            skipped.append(f.name)
            continue
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        slug = fm.get("plan") or f.stem
        rows, notes, has_disposition = _parse_tasks(body)
        ids = {r["id"] for r in rows}
        active = (fm.get("active_task") or "").strip()

        def add(code: str, detail: str) -> None:
            findings.append({"plan": slug, "file": f.name, "code": code, "detail": detail})

        for n in notes:
            add("unparsable_row", n)

        open_rows = [r for r in rows if r["status"] in OPEN_STATUSES]
        if rows and not open_rows:
            add(
                "unclosed_plan",
                f"all {len(rows)} tasks closed — move to _plans/done/{f.name}",
            )
        if not rows:
            add("no_tasks", "no parsable `## Tasks` table")

        if active:
            age, src = _claim_age(active, f, today)
            # Exact leading token, never `startswith`: an `active_task` of
            # "T12 (claimed …)" starts with "T1", so a prefix match reports the
            # WRONG task's row as mismatched — and T1 is typically long done, so
            # it reads as a confident, specific, false finding.
            head = re.match(r"[\w.-]+", active)
            named = next((r for r in rows if head and r["id"] == head.group(0)), None)
            if age is not None and age > max_claim_days:
                add(
                    "stale_claim",
                    f"active_task {active!r} claimed {src} ({age}d ago) — a live "
                    f"session holds a plan, so this blocks every other session "
                    f"until cleared",
                )
            if named is None:
                add("claim_mismatch", f"active_task {active!r} names no task id")
            elif named["status"] != "active":
                add(
                    "claim_mismatch",
                    f"active_task names {named['id']} but its row is "
                    f"{named['status']!r}, not 'active'",
                )
        else:
            for r in rows:
                if r["status"] == "active":
                    add(
                        "claim_mismatch",
                        f"{r['id']} is 'active' but active_task is empty "
                        f"(interrupted claim or close)",
                    )

        # Reported once per plan, not once per done task: a 4-column table is a
        # single authoring choice, and fanning it out over every closed row
        # buries the findings that need a human.
        if rows and not has_disposition:
            add("no_disposition_column",
                "task table has no `disposition` column (contract: "
                "id | task | depends_on | status | session | disposition)")

        for r in rows:
            for d in r["deps"]:
                if d not in ids:
                    add("unresolved_dep", f"{r['id']} depends_on {d!r}, not in the table")
            if r["raw_status"]:
                add("bad_status",
                    f"{r['id']} status {r['raw_status']!r} — read as "
                    f"{r['status']!r}; the claim protocol only writes "
                    f"{sorted(STATUSES)}")
            if has_disposition and r["status"] == "done" and not r["disposition"]:
                add("done_no_disposition", f"{r['id']} is done with no disposition")
            if r["disposition"] and r["disposition"] not in DISPOSITIONS:
                add("bad_disposition", f"{r['id']} disposition {r['disposition']!r}")

        for cyc in _cycles(rows):
            add("dep_cycle", " → ".join(cyc + [cyc[0]]))

        plans.append({
            "plan": slug,
            "file": f.name,
            "track": fm.get("track") or "",
            "active_task": active,
            "tasks": len(rows),
            "open": len(open_rows),
        })

    return {"plans": plans, "findings": findings, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--max-claim-days", type=int, default=2,
        help="a claim older than this is reported as stale (default 2)",
    )
    ap.add_argument("--plans-dir", default="", help="override (testing)")
    ap.add_argument("--today", default="", help="ISO date override (testing)")
    args = ap.parse_args()

    # `vault_root()` not `require_vault_root()`: this check runs in the `always`
    # preflight scope, and a fresh clone with no vault configured is HEALTHY —
    # exiting 2 there would print an actionable-looking failure at every
    # session start on a tree that has nothing wrong with it.
    if args.plans_dir:
        plans_dir = Path(args.plans_dir)
    else:
        root = vault_root()
        plans_dir = root / "10_Projects/emptyos/log/_plans" if root else None
    if plans_dir is None or not plans_dir.is_dir():
        msg = f"no _plans dir at {plans_dir}" if plans_dir else "no vault configured"
        if args.json:
            return emit_json(True, "ok", msg, {"plans": [], "findings": []})
        print(msg)
        return 0

    today = datetime.date.fromisoformat(args.today) if args.today else None
    res = scan_plans(plans_dir, max_claim_days=args.max_claim_days, today=today)
    findings = res["findings"]
    msg = f"{len(res['plans'])} plans · {len(findings)} findings"

    if args.json:
        return emit_json(not findings, "plan_staleness", msg, res)

    print(f"{msg}  (stale after {args.max_claim_days}d)")
    if res["skipped"]:
        print(f"  sync-conflict copies (skipped): {res['skipped']}")
    for p in res["plans"]:
        held = f"  HELD by {p['active_task']}" if p["active_task"] else ""
        print(f"  {p['plan']:<32} {p['open']:>2} open / {p['tasks']:>2} tasks{held}")
    if findings:
        print()
        for fd in findings:
            print(f"  [{fd['code']}] {fd['plan']}: {fd['detail']}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
