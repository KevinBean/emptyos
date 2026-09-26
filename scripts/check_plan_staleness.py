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
                    Withheld while any row is unreadable: its advice is the only
                    destructive one here ("move to _plans/done/"), and a row the
                    parser could not read is not evidence that the work is done.
  stale_claim       `active_task` non-empty and its claim date older than
                    --max-claim-days. Means a session died mid-task: the marker
                    is a mutual-exclusion claim, so a stale one BLOCKS the plan
                    for every future session until a human clears it.
  claim_mismatch    `active_task` and the row's `status: active` disagree. A
                    half-written claim — the resume protocol writes both, so one
                    without the other is an interrupted write.
  unresolved_dep    `depends_on` names an id that is not in the table.
  dep_cycle         tasks that can never become claimable.
  table_truncated   a row's content wrapped onto a line with no leading `|`, so
                    the walk stopped early and the rows below were never seen.
                    Every count for that plan is then a floor, not a total —
                    which is why it also withholds `unclosed_plan`.
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

# The row parser moved to plan_table.py when session_board.py became its second
# reader. `_status_cell` is re-exported for tests that address it here.
from plan_table import (  # noqa: E402, F401
    DISPOSITIONS,
    OPEN_STATUSES,
    STATUSES,
    _parse_tasks,
    _status_cell,
)
from reconcile_tracks import _is_conflict  # noqa: E402
from scanner_lib import emit_json  # noqa: E402
from vault_paths import vault_root  # noqa: E402

DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


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
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        slug = fm.get("plan") or f.stem
        # Line numbers must address the file on disk, not the post-frontmatter
        # body — see `_parse_tasks`.
        offset = len(text.splitlines()) - len(body.splitlines())
        rows, notes, has_disposition, left = _parse_tasks(body, offset)
        ids = {r["id"] for r in rows}
        active = (fm.get("active_task") or "").strip()

        def add(code: str, detail: str) -> None:
            findings.append({"plan": slug, "file": f.name, "code": code, "detail": detail})

        for n in notes:
            add("unparsable_row", n)

        if left:
            add(
                "table_truncated",
                f"walk stopped with {left} `|` row(s) below — a row's own content "
                f"wraps onto a line with no leading `|`, so those rows are unseen "
                f"(reported counts cover only the {len(rows)} walked)",
            )

        open_rows = [r for r in rows if r["status"] in OPEN_STATUSES]
        # `[...]` not `.get(...)`: every row `_parse_tasks` builds sets the key,
        # and a future branch that forgot it would read as False — i.e. READABLE
        # — silently re-arming the destructive finding below. Fail loud instead.
        unreadable = [r for r in rows if r["unreadable"]]
        # An unreadable row carries `status: ""`, which is not in OPEN_STATUSES —
        # so without this guard a row the parser could not read counts as CLOSED,
        # and a plan whose only remaining work sits in that row is reported
        # "all N tasks closed — move to _plans/done/". That is the one finding
        # here whose advice is destructive: it tells a human to archive a live
        # plan on the strength of a row nobody read. Measured 2026-09-12 on
        # `conversation-ingest-backlog` (T2 truncated mid-row; its prose says
        # 179 records remain). `unparsable_row` already fires and is the
        # actionable finding — stay silent on closure until the table parses.
        if rows and not open_rows and not unreadable and not left:
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
            # Reported beside `open` because a consumer reading `open: 0` alone
            # concludes the plan is finished — which is exactly the wrong call
            # when a row was unreadable rather than closed.
            "unreadable": len(unreadable),
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
        unread = f"  ({p['unreadable']} unreadable)" if p["unreadable"] else ""
        print(
            f"  {p['plan']:<32} {p['open']:>2} open / {p['tasks']:>2} tasks"
            f"{unread}{held}"
        )
    if findings:
        print()
        for fd in findings:
            print(f"  [{fd['code']}] {fd['plan']}: {fd['detail']}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
