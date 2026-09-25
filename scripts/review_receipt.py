#!/usr/bin/env python3
"""Write / inspect adversarial-review receipts, keyed to the diff being committed.

A receipt attests that the *specific bytes* about to be committed were put
through the hostile-review brief in `.claude/skills/eos-adversarial-review/`,
and that every finding ended either fixed or waived-with-a-reason. It is NOT a
record that a review once happened — change the diff and the receipt no longer
matches, which is the whole point.

The key is a SHA-256 over `git diff --cached` plus the sorted list of staged
paths. Paths are included so that staging an additional file invalidates the
receipt even when that file's content happens to produce no diff hunks (a mode
change, a rename). Untracked files are NOT part of the hash: they are invisible
to `git diff --cached` until added, and once added they show up as staged paths.

Commands
--------
  write   Record a receipt for the current staged diff.
  show    Print the current staged-diff key and whether a receipt matches.

Exit codes: 0 on success; 1 when `show` finds no matching receipt (so the
check is usable as an exit-code signal, per .claude/rules/agent-cli.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# One receipt store for every repo the gate covers, always anchored to the
# parent. Nested repos (apps/personal) key into the same directory; collisions
# between them are prevented by mixing the repo identity into the hash below,
# not by giving each repo its own store.
RECEIPT_DIR = REPO / "data" / "review" / "receipts"


def _git(*args: str, repo: Path | str | None = None) -> str:
    """Run a read-only git command in `repo` (default: this repo); '' on error."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo or REPO),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return ""
    # Decode defensively: a diff can carry any bytes, and on Windows the
    # console codepage is not utf-8 (CLAUDE.md / .claude/rules/environment.md).
    return out.stdout.decode("utf-8", errors="replace")


def toplevel(start: Path | str | None = None) -> Path:
    """Resolve the work-tree root containing `start`, falling back to REPO.

    Canonicalising to the top level is what keeps `git -C <subdir> commit` and a
    bare `git commit` on the same key: both name one repo, so both must hash
    identically. Only a genuinely DIFFERENT work tree — the nested
    apps/personal repo — should move the key.
    """
    out = _git("rev-parse", "--show-toplevel", repo=start or REPO).strip()
    if not out:
        return REPO
    try:
        p = Path(out).resolve()
    except Exception:
        return REPO
    # Only a real directory may move the key. `git rev-parse` prints nothing on
    # stdout for a non-repo, but a path that survives parsing yet does not exist
    # (a mangled `-C`, git printing something unexpected) must degrade to the
    # previous behaviour rather than mint a key nobody can reproduce.
    return p if p.is_dir() else REPO


def _is_default_repo(repo: Path) -> bool:
    """True when `repo` is this repo, comparing the way the filesystem does."""
    import os as _os

    return _os.path.normcase(str(repo)) == _os.path.normcase(str(REPO))


def staged_key(
    paths_only: list[str] | None = None,
    repo: Path | str | None = None,
) -> tuple[str, list[str]]:
    """Return (sha256_hex_12, paths) for the change about to be committed.

    With ``paths_only``, keys on the WORKTREE diff of those paths instead of the
    index. That is not a convenience: CLAUDE.md's parallel-session rule mandates
    `git commit <paths> -F-` with no `git add`, which commits worktree state and
    ignores the index entirely — so an index-keyed gate could be satisfied by a
    receipt for somebody else's staged work while committing something nobody
    reviewed. Found the first time the gate was used for real, 2026-08-28.

    With ``repo``, keys on THAT work tree's diff. `apps/personal` is a nested
    repo the parent gitignores, so a `git -C apps/personal commit` used to be
    gated on whatever happened to be staged in the PARENT index — unrelated
    bytes. That made the nested repo uncommittable whenever a parallel session
    held an unreviewed index, and the two documented escapes were both wrong:
    `--waive-all` would have written a receipt for the parent's key, opening
    somebody else's gate on work nobody reviewed. Found 2026-09-01.

    The repo identity enters the hash ONLY for a non-default work tree, so every
    existing parent-repo receipt keeps its key. Without that mixin two repos
    with byte-identical diffs over identical path lists collide — not
    hypothetical, since the same one-line `.gitignore` addition in both repos
    produces the same diff text.
    """
    repo_dir = toplevel(repo) if repo is not None else REPO
    if paths_only:
        diff = _git("diff", "HEAD", "--", *paths_only, repo=repo_dir)
        # An empty diff means there is nothing to attest to, exactly as an empty
        # index does — report no paths so callers take the same "let git speak"
        # branch. Without this, pathspec mode never reaches that branch (the
        # caller always names at least one path) and a no-op commit is denied
        # with a message about a review, when the real answer is "nothing to
        # commit". Found by live-verify, not by the tests.
        paths = sorted(paths_only) if diff.strip() else []
    else:
        diff = _git("diff", "--cached", repo=repo_dir)
        paths = sorted(
            p
            for p in _git("diff", "--cached", "--name-only", repo=repo_dir).splitlines()
            if p
        )
    h = hashlib.sha256()
    h.update(diff.encode("utf-8", errors="replace"))
    h.update(b"\x00--paths--\x00")
    h.update("\n".join(paths).encode("utf-8", errors="replace"))
    if not _is_default_repo(repo_dir):
        import os as _os

        h.update(b"\x00--repo--\x00")
        h.update(_os.path.normcase(str(repo_dir)).encode("utf-8", errors="replace"))
    return h.hexdigest()[:12], paths


def receipt_path(key: str) -> Path:
    return RECEIPT_DIR / f"{key}.json"


def load_receipt(key: str) -> dict | None:
    p = receipt_path(key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # A corrupt receipt is not a valid receipt. Treat as absent rather than
        # crashing the gate that calls us.
        return None


def cmd_write(args: argparse.Namespace) -> int:
    key, paths = staged_key(args.path, repo=args.repo)
    if not paths:
        where = f" in {toplevel(args.repo)}" if args.repo else ""
        print(
            f"Nothing is staged{where} — a receipt would attest to an empty diff.\n"
            "Stage the files you intend to commit first, then write the receipt.",
            file=sys.stderr,
        )
        return 1

    waivers = list(args.waive or [])
    if args.waive_all:
        waivers.append(f"ALL — {args.waive_all}")

    summary = args.summary or (args.waive_all and f"waived wholesale: {args.waive_all}")
    if not summary:
        print(
            "--summary is required (or --waive-all with a reason).\n"
            "A receipt with no written disposition is the silence the gate exists "
            "to prevent.",
            file=sys.stderr,
        )
        return 1

    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": str(toplevel(args.repo)),
        "staged_paths": paths,
        "summary": summary,
        "waivers": waivers,
        "waived_wholesale": bool(args.waive_all),
    }
    receipt_path(key).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Receipt written: {key}  ({len(paths)} staged path(s))")
    print(f"  summary: {summary}")
    for w in waivers:
        print(f"  waived:  {w}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    repo_dir = toplevel(args.repo)
    key, paths = staged_key(repo=args.repo)
    if not paths:
        print(f"Nothing staged in {repo_dir}.")
        return 1
    rec = load_receipt(key)
    print(f"repo:            {repo_dir}")
    print(f"staged-diff key: {key}")
    print(f"staged paths:    {len(paths)}")
    for p in paths:
        print(f"  - {p}")
    if rec is None:
        print("\nNO RECEIPT for this diff — the commit gate is CLOSED.")
        return 1
    print(f"\nreceipt: {rec.get('written_at')}")
    print(f"summary: {rec.get('summary')}")
    for w in rec.get("waivers") or []:
        print(f"waived:  {w}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="record a receipt for the staged diff")
    w.add_argument("--summary", help="one line: how many findings, how disposed")
    w.add_argument(
        "--waive",
        action="append",
        metavar="TEXT",
        help="a waived finding, as 'file:line - written reason'. Repeatable.",
    )
    w.add_argument(
        "--path",
        action="append",
        metavar="PATH",
        help="key on the worktree diff of these paths instead of the index, for "
             "a `git commit <paths>` that never touches the index. Repeatable.",
    )
    w.add_argument(
        "--waive-all",
        metavar="REASON",
        help="waive the whole review with a reason (typo fix, revert, generated artifacts)",
    )
    w.add_argument(
        "--repo",
        metavar="DIR",
        help="key on the work tree containing DIR instead of this repo, for a "
             "`git -C <dir> commit` into a nested repo such as apps/personal.",
    )
    w.set_defaults(func=cmd_write)

    s = sub.add_parser("show", help="print the staged key and any matching receipt")
    s.add_argument(
        "--repo",
        metavar="DIR",
        help="inspect the work tree containing DIR instead of this repo.",
    )
    s.set_defaults(func=cmd_show)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
