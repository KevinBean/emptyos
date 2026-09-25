#!/usr/bin/env python3
"""check_skill_refs.py — repo paths named in a skill body that no longer resolve.

A skill is instructions an agent follows. When it names `apps/kb/shared.py` and
the file moved to `apps/public/standard/kb/shared.py`, the agent either wastes a
search or — worse — answers confidently about a file it never opened. Nothing
watched this: `check_skills.py` validates frontmatter, `check_skill_script_sync`
and `check_skill_vault_sync` compare copies against each other. None of the
three reads a skill's *body* for claims about the repo.

Graduates the 2026-09-12 skills audit's § 4 (16 skills, 45 dead references —
most of them flat `apps/<id>/app.py` paths left behind by the move to the
track tree) per `.claude/rules/audits.md` + `.claude/rules/self-audit-loops.md`.

Measured before the narrowing, which is the part that matters (audits.md § 1 —
a heuristic that fires on healthy input is noise, not a bug list). The naive
variant — this same REF regex with NO placeholder filter and NO marker check —
reported 11 rows across 8 skills on 2026-09-12, and **8 of the 11 were correct
behaviour**, in three distinct shapes. Re-derive by commenting out the
PLACEHOLDER and MARKER branches in ``scan_file``:

  * a path inside an example (`TARGET = "apps/.../verification.py"`) — already
    caught by the placeholder filter, but its paired `TESTS = ...` line was not;
  * a path relative to something that is not this repo — the YouTube skill's
    `scripts/generate_cover.py` lives under a *vault* project directory;
  * a path the skill's whole job is to CREATE — dev-claude-md-optimizer
    describes the `.claude/rules/*.md` layout it writes into a target repo.

Only the third class needs a human to say "this is fine", so it gets an inline
marker rather than a central allowlist (audits.md: an allowlist turns every new
legitimate case into a build break, and sits far from the thing it excuses):

    <!-- skill-refs: ignore — why this file's paths are not repo paths -->

ADVISORY, never a gate. Two of the three shapes above are judgment calls about
what a path is *relative to*, which no regex settles; per audits.md an ambiguous
signal must not gate, and a gate that fires on a correct skill gets disabled.

Exit is 0/1, mirroring `scanner_lib.emit_json`, NOT the finding count: a count
exit is one `sys.exit(256)` away from reporting the worst run as success, which
is the wrap-around `.claude/rules/audits.md` records. The count is in the
payload and on stdout for anyone who needs it.

Pure file I/O — does NOT import emptyos.kernel (no syslog handle; safe while the
daemon is up, `.claude/rules/daemon-handling.md`).

Usage::

    python scripts/check_skill_refs.py              # human table
    python scripts/check_skill_refs.py --json       # agent-cli envelope
    python scripts/check_skill_refs.py --user       # include ~/.claude/skills
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scanner_lib import emit_json  # noqa: E402 — sys.path is set just above

# Roots that ship or are read by a harness. The two mirrors — `.agents/skills`
# and `.agent-bus/skills` — are omitted so a finding is reported once instead of
# three times.
#
# That is a real (if latent) coverage gap, not a free lunch, and the sync
# checkers do NOT close it: `check_skill_script_sync.py` gates byte-equality
# only for skills present in BOTH trees and reports a one-tree-only skill as
# *advisory* (its own docstring says so). So a Codex-only skill's dead
# references would be invisible here and ungated there. Today the only
# asymmetric entry is `_retired`, which `scan()` skips anyway — add the mirrors
# here if a genuinely Codex-only skill ever lands.
TREES = (".claude/skills", "skills")
USER_TREE = Path.home() / ".claude" / "skills"

# A repo-shaped path: one of our top-level directories, then a file extension we
# actually keep. Deliberately NOT `[\w-]+/` — matching any slash-separated token
# sweeps in vault paths, URLs and prose.
REF = re.compile(
    # No backtick in the lookbehind: a skill writes `apps/kb/shared.py` in
    # backticks far more often than bare, so excluding that form blinds the
    # scanner to its main case. Caught by test_fires_on_a_path_that_does_not_exist.
    r"(?<![\w/])"
    r"((?:apps|scripts|emptyos|docs|engines|plugins|tests|skills|services|profiles"
    r"|\.claude|\.agents|\.agent-bus)/[\w./\-]+"
    r"\.(?:py|md|toml|html|json|js|mjs|css|sh|bat|yml|yaml))"
)

# Tokens that mark a MATCHED PATH as illustrative rather than real.
#
# Note what is NOT here, and why. `<x>` and `{x}` are the other two placeholder
# spellings this repo uses, but REF's body is `[\w./\-]+`, which cannot contain
# `<` or `{` — so `apps/<id>/app.py` never reaches this regex at all; it simply
# fails to match. Listing them here was dead weight, and it also made a test
# pass for a reason other than the one it named. `..` CAN appear in the char
# class, so it is excluded explicitly rather than left to resolve against the
# repo root (`apps/../secrets.py` would otherwise test as "exists").
PLACEHOLDER = re.compile(
    r"(\.\.\.|\.\./|\bfoo\b|\bbar\b|\bbaz\b|\bNAME\b|\bmyapp\b|\bslug\b"
    r"|\bexample\b|/X\.|/Y\.|\btest_x\b)"
)

# Deliberately the full two-token string, never a bare "ignore": 36 of 162 skill
# .md files quote some other scanner's opt-out marker in passing, so a widened
# token would silence 22% of the corpus in a single edit.
MARKER = "skill-refs: ignore"


def scan_file(path: Path) -> list[str]:
    """Dead repo-relative references in one markdown file, sorted."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # Never swallow this into a clean result — a scanner that cannot read a
        # file has nothing to say about it, and "no findings" would be a lie
        # (`.claude/rules/audits.md`, the defensive-except failure).
        #
        # On STDOUT, not stderr: preflight's runner tails `stdout or stderr`,
        # and this scanner's stdout is never empty, so a stderr-only warning is
        # discarded unconditionally in the one path that actually runs it.
        print(f"WARNING: could not read {path} ({exc}) — NOT scanned")
        return []
    if MARKER in text:
        return []
    dead = set()
    for ref in REF.findall(text):
        if PLACEHOLDER.search(ref):
            continue
        if (ROOT / ref).exists():
            continue
        dead.add(ref)
    return sorted(dead)


def scan(include_user: bool = False) -> list[tuple[str, str, list[str]]]:
    """Returns (tree, skill-relative file, dead refs) for every offending file."""
    roots = [(t, ROOT / t) for t in TREES]
    if include_user and USER_TREE.is_dir():
        roots.append(("~/.claude/skills", USER_TREE))

    findings: list[tuple[str, str, list[str]]] = []
    for label, root in roots:
        if not root.is_dir():
            continue
        for skill in sorted(p for p in root.iterdir() if p.is_dir()):
            # `_`-prefixed dirs are skipped everywhere in EmptyOS skill
            # discovery (`docs/SKILLS.md` header). Load-bearing here rather
            # than cosmetic: `_retired/` holds superseded skills whose dead
            # references are expected — reporting them would be noise nobody
            # can action, and they are the only asymmetric tree entry.
            if skill.name.startswith("_"):
                continue
            for dirpath, dirnames, filenames in os.walk(skill):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for fn in sorted(filenames):
                    if not fn.endswith(".md"):
                        continue
                    fp = Path(dirpath) / fn
                    if dead := scan_file(fp):
                        rel = fp.relative_to(root).as_posix()
                        findings.append((label, rel, dead))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="agent-cli envelope")
    ap.add_argument(
        "--user", action="store_true", help="also scan ~/.claude/skills (per-machine)"
    )
    args = ap.parse_args()

    findings = scan(include_user=args.user)
    n = sum(len(d) for _, _, d in findings)

    if args.json:
        payload = {
            "files": [
                {"tree": t, "file": f, "dead": d} for t, f, d in findings
            ],
            "dead_refs": n,
        }
        distinct = len({r for _, _, d in findings for r in d})
        msg = (
            f"{n} dead reference(s) ({distinct} distinct path(s)) in "
            f"{len(findings)} file(s)"
        ) if n else "no dead references"
        return emit_json(not n, "ok" if not n else "dead_refs", msg, payload)

    if not findings:
        trees = ", ".join(TREES) + (" + user store" if args.user else "")
        print(f"OK: every repo path named in a skill body resolves ({trees})")
        return 0

    for tree, rel, dead in findings:
        print(f"  {tree}/{rel}")
        for ref in dead:
            print(f"      {ref}")
    distinct = len({r for _, _, d in findings for r in d})
    print(
        f"\n{n} dead reference(s) ({distinct} distinct path(s)) "
        f"in {len(findings)} file(s). "
        f"Advisory — a path relative to a vault project, or one the skill "
        f"creates, is not a defect. Mark such a file with "
        f"`<!-- {MARKER} — why -->`."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
