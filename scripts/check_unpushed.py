#!/usr/bin/env python3
"""check_unpushed.py — commits sitting unpushed past a session boundary.

WHAT THIS CATCHES
-----------------
Work that is committed but never left the machine. Measured 2026-09-01: four
`apps/personal` commits from 2026-08-31 — including the one that introduced the
whole System Icon Library — were still unpushed the next day, and only surfaced
because a wrapup happened to inspect that repo. Nothing watched it, because the
nested `apps/personal` repo is gitignored by the parent and so is invisible to
every other check here.

WHY AN AGE THRESHOLD, NOT A COUNT
---------------------------------
"Has unpushed commits" is the normal state of an active session — you commit,
you keep working, you push at wrapup. Reporting that would fire on nearly every
run and be ignored within a week (.claude/rules/audits.md). The signal is a
commit that survived a *session boundary*: the four above sat overnight.

The 12h default is a first estimate anchored on that single measured case, NOT
a calibrated threshold — there is no push-time history to calibrate against.
Tune `--max-age-hours` if it proves noisy; it is advisory and never gates.

WHICH REPOS
-----------
Discovered, then filtered to the ones this account owns, by comparing each
`origin` URL's owner against the *parent repo's own* owner. That is deliberate:
`.tmp/repo-review/` holds clones of other people's projects (the eos-repo-extract
workflow) and a plain "find every .git" would report their unpushed state as
ours — five foreign repos on this machine right now. Deriving the owner at
runtime also keeps a real account name out of a tracked file (CLAUDE.md rule 13).

FRESHNESS
---------
Answers against the remote-tracking ref, which goes stale. A stale ref
over-reports: on 2026-09-01 a parallel session pushed this session's commits and
the unfetched ref still showed them as unpushed. So this fetches when the last
fetch is older than `--fetch-after-min` (cheap on repeated runs, accurate when
it matters).

Offline it does NOT give up — it answers against the last-known ref and marks
the row stale. That direction is safe: a stale ref over-reports and never
under-reports, so degrading this way cannot hide a strand, whereas returning
"unknown" would throw away a usable answer every time the network is down.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Bounds the walk. Not a security boundary — the owner match below is what
# decides ownership; this decides where it is worth looking at all.
SEARCH_DEPTH = 4

# Never descend. Two distinct reasons, both measured on the first run:
#
#  - SPEED. An unpruned rglob over this tree took 95s, which no preflight check
#    may cost.
#  - CORRECTNESS. `data/` is machine state by CLAUDE.md § Storage, and it holds
#    a real git repo: the publish app's generated `gh-pages` site checkout,
#    which had 22 deliberately-local commits going back 143 days. Those are a
#    build artifact awaiting `eos publish deploy`, not stranded work — reporting
#    them is precisely the every-run false positive that gets a check ignored.
#  - `.claude/scratch/` holds clones of the user's OTHER projects. They are
#    owner-matched, so they would pass the filter, but their push state is that
#    project's business and not EmptyOS hygiene.
PRUNE_DIRS = {
    ".git", "data", ".tmp", "dist", "build", "output", "node_modules",
    ".venv", "venv", "__pycache__", "_retired", ".cache", "scratch",
}
PRUNE_PREFIXES = ("sandbox-",)


def git(args: list[str], cwd: Path, timeout: int = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f"<{type(exc).__name__}>"


def owner_of(url: str) -> str:
    """github.com/Owner/repo.git -> 'owner' (lowercased). '' when unparseable."""
    if not url:
        return ""
    body = url.split("://", 1)[-1].split("@", 1)[-1]
    parts = [p for p in body.replace(":", "/").split("/") if p]
    # host / owner / repo
    return parts[1].lower() if len(parts) >= 3 else ""


def discover(root: Path) -> list[Path]:
    """The root repo plus nested checkouts, pruning per PRUNE_DIRS."""
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        depth = len(here.relative_to(root).parts)
        if depth >= SEARCH_DEPTH:
            dirnames[:] = []
        else:
            dirnames[:] = [
                d for d in dirnames
                if d not in PRUNE_DIRS and not d.startswith(PRUNE_PREFIXES)
            ]
        if here != root and (here / ".git").exists():
            found.append(here)
            dirnames[:] = []  # a repo's insides are its own business
    return [root, *sorted(found)]


NEVER_FETCHED = 10.0**6


def last_fetch_age_min(repo: Path) -> float:
    """Minutes since the last fetch; NEVER_FETCHED when it has not.

    Asks git for the path rather than joining `repo/.git/FETCH_HEAD`, which is
    simply wrong whenever `.git` is a FILE — worktrees and submodules point
    elsewhere.

    Deliberately no fallback to HEAD's mtime. HEAD is the last COMMIT time, so
    a repo that had never fetched but had just committed would report "fetched
    seconds ago", skip the fetch, and answer from a ref that may not exist —
    the exact opposite of what this returns it for. Erring toward fetching is
    the safe direction; the cost is one network call.
    """
    rc, raw = git(["rev-parse", "--git-path", "FETCH_HEAD"], repo)
    if rc != 0 or not raw:
        return NEVER_FETCHED
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    try:
        return (time.time() - path.stat().st_mtime) / 60.0
    except OSError:
        return NEVER_FETCHED


def inspect(repo: Path, *, fetch_after_min: float, do_fetch: bool) -> dict | None:
    """Return a report row, or None when the repo is out of scope."""
    rc, url = git(["remote", "get-url", "origin"], repo)
    if rc != 0 or not url:
        return None
    row = {"repo": str(repo), "owner": owner_of(url)}

    rc, branch = git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    if rc != 0 or branch == "HEAD":
        return {**row, "state": "detached", "unpushed": 0}
    row["branch"] = branch

    row["stale"] = False
    if do_fetch and last_fetch_age_min(repo) > fetch_after_min:
        rc, _ = git(["fetch", "--quiet", "origin"], repo, timeout=30)
        # Offline is not "clean", but it is also not "unknown". Keep going
        # against the last-known ref — which still catches a strand — and mark
        # the answer stale. A stale ref over-reports (a parallel session pushing
        # our commits leaves them looking unpushed); it never under-reports, so
        # degrading this way cannot hide the defect the check exists for.
        # Returning early here instead would discard a usable answer.
        row["stale"] = rc != 0

    rc, upstream = git(["rev-parse", "--abbrev-ref", "@{u}"], repo)
    if rc != 0 or not upstream:
        return {**row, "state": "no-upstream", "unpushed": 0}
    row["upstream"] = upstream

    rc, out = git(["log", "--format=%H %ct %s", "@{u}..HEAD"], repo)
    if rc != 0:
        return {**row, "state": "error", "unpushed": 0}
    commits = [ln for ln in out.split("\n") if ln.strip()]
    row["unpushed"] = len(commits)

    _, behind = git(["rev-list", "--count", "HEAD..@{u}"], repo)
    row["behind"] = int(behind) if behind.isdigit() else 0

    if commits:
        oldest_ts = min(int(ln.split(" ", 2)[1]) for ln in commits)
        row["oldest_age_h"] = round((time.time() - oldest_ts) / 3600.0, 1)
        row["oldest_subject"] = commits[-1].split(" ", 2)[2][:72]
    else:
        row["oldest_age_h"] = 0.0
    row["state"] = "ok"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age-hours", type=float, default=12.0,
                    help="report when the oldest unpushed commit is older than this (default 12)")
    ap.add_argument("--fetch-after-min", type=float, default=30.0,
                    help="re-fetch when the last fetch is older than this (default 30)")
    ap.add_argument("--no-fetch", action="store_true", help="never touch the network")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    parent_rc, parent_url = git(["remote", "get-url", "origin"], REPO)
    ours = owner_of(parent_url) if parent_rc == 0 else ""

    rows, skipped_foreign = [], 0
    for repo in discover(REPO):
        row = inspect(repo, fetch_after_min=args.fetch_after_min, do_fetch=not args.no_fetch)
        if row is None:
            continue
        if ours and row["owner"] != ours:
            skipped_foreign += 1
            continue
        rows.append(row)

    findings = [r for r in rows
                if r["state"] == "ok" and r["oldest_age_h"] > args.max_age_hours]
    unknown = [r for r in rows if r["state"] in ("no-upstream", "error")]
    stale = [r for r in rows if r.get("stale")]

    if args.json:
        return emit_json(
            not findings,
            "unpushed",
            f"{len(findings)} repo(s) with commits older than {args.max_age_hours}h unpushed",
            {"findings": findings, "repos": rows, "unknown": unknown,
             "stale": [r["repo"] for r in stale], "skipped_foreign": skipped_foreign},
        )

    for r in findings:
        rel = os.path.relpath(r["repo"], REPO).replace("\\", "/")
        caveat = "  (remote unreachable — measured against a stale ref)" if r.get("stale") else ""
        print(f"  {rel or '.'} [{r['branch']}] — {r['unpushed']} unpushed, "
              f"oldest {r['oldest_age_h']}h: {r['oldest_subject']}{caveat}")
    for r in unknown:
        rel = os.path.relpath(r["repo"], REPO).replace("\\", "/")
        print(f"  {rel or '.'} — {r['state']} (unpushed state UNKNOWN, not clean)")

    scope = f"{len(rows)} own repo(s)" + (
        f", {skipped_foreign} foreign skipped" if skipped_foreign else "")
    if findings:
        print(f"check-unpushed: {len(findings)} repo(s) holding work past "
              f"{args.max_age_hours}h — push or say why. Scanned {scope}.")
        return 1
    carried = sum(r["unpushed"] for r in rows if r["state"] == "ok")
    note = f" ({carried} unpushed but under {args.max_age_hours}h)" if carried else ""
    print(f"check-unpushed: OK — nothing stranded{note}. Scanned {scope}.")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
