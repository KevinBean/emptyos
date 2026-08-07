#!/usr/bin/env python
"""Find .agent-bus/ files that were derived from another session's uncommitted work.

`eos bus import` copies the WHOLE native tree (.claude/rules, .claude/skills)
into the canonical store. It has no idea which native files are mid-edit by a
parallel session, so a plain import→commit publishes a half-written snapshot of
somebody else's work under your name.

That is not hypothetical: on 2026-07-31 an import captured a skill while another
session was actively rewriting it, and a rule pair that had been sitting dirty on
two other tracks all day.

This reports bus files whose native counterpart is unstaged or untracked, so
they can be excluded before you commit. Read-only unless --revert.

**Stage your own native edits first.** Your new rule and another session's new
rule are both just "untracked" to git; the index is the only thing that tells
them apart. Staging says "this is mine, in this commit" — skip it and the guard
will strip your own work back out, correctly and unhelpfully.

Exit code is the number of files needing exclusion, so a caller can gate on it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanner_lib import emit_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# (bus subdir, native subdir) — the pairs `import` copies between.
PAIRS = (("rules", ".claude/rules"), ("skills", ".claude/skills"))


def _git(*args: str) -> str:
    """Run git and REFUSE to continue on failure.

    A safety check that degrades to empty output degrades to "all clear", which
    is the one answer it must never give wrongly — the caller would commit
    another session's work believing this had vetted it. Fail loud instead.
    """
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def _foreign_native() -> set[str]:
    """Repo-relative paths holding work that is NOT part of the commit you are
    about to make.

    The discriminator is the index, because "another session's file" and "my own
    new file" are otherwise identical to git — both are just untracked. Porcelain
    column 1 is index state, column 2 is worktree state. Anything you have
    staged is yours and about to be committed, so it is not foreign; anything
    merely dirty or untracked in the worktree belongs to whoever is still
    working on it.

    Consequence for callers: stage your own native edits BEFORE importing, or
    the guard will correctly-but-unhelpfully strip your own work out of the bus.
    """
    out = set()
    for line in _git("status", "--porcelain").splitlines():
        if len(line) < 3:
            continue
        index_state = line[0]
        p = line[3:].strip().strip('"')
        if " -> " in p:  # rename
            p = p.split(" -> ", 1)[1]
        if p and index_state in (" ", "?"):
            out.add(p.replace("\\", "/"))
    return out


def _changed_bus() -> list[str]:
    """Bus files that this import touched — modified or newly created."""
    out = []
    for line in _git("status", "--porcelain", "--", ".agent-bus").splitlines():
        p = line[3:].strip().strip('"').replace("\\", "/")
        if p:
            out.append(p)
    return out


def _native_for(bus_path: str) -> str | None:
    for sub, native_root in PAIRS:
        prefix = f".agent-bus/{sub}/"
        if bus_path.startswith(prefix):
            return f"{native_root}/{bus_path[len(prefix):]}"
    return None


def scan() -> dict:
    foreign = _foreign_native()
    # A foreign *directory* (untracked skill dir) shows up as "path/" — match by
    # prefix so every file under it counts, not just an exact hit.
    foreign_dirs = tuple(d for d in foreign if d.endswith("/"))

    def is_foreign(nat: str) -> bool:
        return nat in foreign or any(nat.startswith(d) for d in foreign_dirs)

    tracked_files = set(_git("ls-files", "--", ".agent-bus").splitlines())
    revert, remove = [], []
    for bus in _changed_bus():
        nat = _native_for(bus)
        if not nat or not is_foreign(nat):
            continue
        (revert if bus in tracked_files else remove).append({"bus": bus, "native": nat})
    return {"revert": revert, "remove": remove}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit one JSON envelope")
    ap.add_argument("--revert", action="store_true",
                    help="actually exclude them (git checkout / rm), not just report")
    args = ap.parse_args()

    res = scan()
    revert, remove = res["revert"], res["remove"]
    n = len(revert) + len(remove)

    if args.revert:
        if revert:
            _git("checkout", "HEAD", "--", *[r["bus"] for r in revert])
        for r in remove:
            target = ROOT / r["bus"]
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    verb = "excluded" if args.revert else "need exclusion"
    msg = f"{n} bus file(s) {verb}" if n else "bus is clean of parallel-session work"
    if args.json:
        return emit_json(n == 0, "ok" if n == 0 else "parallel_session_work", msg, res)

    print(msg)
    for r in revert:
        print(f"  {'reverted' if args.revert else 'revert  '}: {r['bus']}")
        print(f"      ← native is unstaged: {r['native']}")
    for r in remove:
        print(f"  {'removed ' if args.revert else 'remove  '}: {r['bus']}")
        print(f"      ← native is untracked: {r['native']}")
    return 0 if n == 0 else n


if __name__ == "__main__":
    sys.exit(main())
