#!/usr/bin/env python3
"""sync_user_skills.py — mirror user-global skills into the bundled repo tree, safely.

The product skills (`creative-*`, `life-*`, `tool-*`, `vault-*`, `dev-*`) are
authored in the per-machine store at `~/.claude/skills/` and mirrored into the
git-tracked `skills/` tree so a fresh clone ships them. That copy has to strip
personal absolute paths (`D:/Main Vault`, `C:/Users/Kevin`) — CLAUDE.md rule 13.

Doing it as two steps (copy, then scrub) is what leaked personal paths into
commit 97364cc6: a later copy re-introduced paths a prior scrub had removed, and
nothing re-checked before the commit. So this script fuses them — a file is
scrubbed **in memory** and then **verified against `.eos-personal`** (via the same
`emptyos.sdk.personal_patterns` loader `check-personal.py` uses, so the two can
never disagree) before it is allowed to touch the disk. A file that still matches
a pattern is refused, not written, and reported. Order can no longer be gotten wrong.

Never copied: `*-memory.md` (living per-machine state — see the living-memory
sidecar convention in `~/.claude/skills/_skills-index.md`) and runtime caches.
Only skills that already exist in BOTH trees are synced; this never creates a new
bundled skill (that's a deliberate act — the repo decides what it ships).

Usage:
    python scripts/sync_user_skills.py             # sync every shared skill
    python scripts/sync_user_skills.py <id> [<id>] # sync named skills only
    python scripts/sync_user_skills.py --check     # report drift, write nothing (exit 1 on drift)

Not registered in `scripts/preflight.py`: the user-global store is machine-specific,
so a drift check would fail on CI and on any other machine. Run it by hand after
editing a product skill, then `python scripts/check-personal.py` as the backstop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from emptyos.sdk.personal_patterns import load as load_personal_patterns  # noqa: E402

GLOBAL = Path.home() / ".claude" / "skills"
BUNDLED = ROOT / "skills"

# Personal absolute paths → portable placeholders. The bundled copy is read by
# people whose vault/home live elsewhere, so a literal path is wrong for them
# even setting the leak aside.
SUBS: list[tuple[str, str]] = [
    ("D:\\Main Vault", "{vault}"),
    ("D:/Main Vault", "{vault}"),
    # The macOS vault moved out of ~/Documents (iCloud-managed) in 2026-07; the
    # legacy forms stay so older skill text still scrubs.
    ("/Users/kb/Documents/Main Vault", "{vault}"),
    ("~/Documents/Main Vault", "{vault}"),
    ("/Users/kb/Main Vault", "{vault}"),
    ("~/Main Vault", "{vault}"),
    ("C:\\Users\\Kevin", "{home}"),
    ("C:/Users/Kevin", "{home}"),
]

SKIP_SUFFIXES = ("-memory.md",)


def scrub(text: str) -> str:
    for old, new in SUBS:
        text = text.replace(old, new)
    return text


def residue(text: str, patterns) -> list[str]:
    """Personal patterns still present after scrubbing. Empty means safe to write."""
    return [rx.pattern for rx in patterns if rx.search(text)]


def _is_gitignored(d: Path) -> bool:
    """True for skills the repo deliberately never ships (`.gitignore` entries).

    Some product skills are personal by design — they name the user, their employer,
    their CV. Those live only in the user-global store and are gitignored in the
    bundled tree, so `check-personal.py` (tracked files only) rightly ignores them.
    Syncing them would be pointless and would trip the residue guard on content
    that is perfectly legal where it lives.
    """
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", str(d)], cwd=ROOT, capture_output=True
        ).returncode
        == 0
    )


def _shared_slugs() -> list[str]:
    if not BUNDLED.is_dir() or not GLOBAL.is_dir():
        return []
    return sorted(
        p.name
        for p in BUNDLED.iterdir()
        if p.is_dir() and (GLOBAL / p.name).is_dir() and not _is_gitignored(p)
    )


def sync(slug: str, patterns, *, check_only: bool) -> tuple[int, int]:
    """Returns (changed, refused) counts for one skill."""
    src, dst = GLOBAL / slug, BUNDLED / slug
    changed = refused = 0
    for f in sorted(src.glob("*.md")):
        if f.name.endswith(SKIP_SUFFIXES):
            continue
        text = scrub(f.read_text(encoding="utf-8"))
        if leaks := residue(text, patterns):
            print(f"  REFUSED {slug}/{f.name} — personal pattern survives scrub: {', '.join(leaks)}")
            refused += 1
            continue
        out = dst / f.name
        if out.exists() and out.read_text(encoding="utf-8") == text:
            continue
        changed += 1
        verb = "would update" if check_only else ("update" if out.exists() else "create")
        print(f"  {verb} {slug}/{f.name}")
        if not check_only:
            out.write_text(text, encoding="utf-8")
    return changed, refused


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    check_only = "--check" in sys.argv

    if not GLOBAL.is_dir():
        print(f"no user-global skill store at {GLOBAL} — nothing to sync")
        return 0

    slugs = args or _shared_slugs()
    if not slugs:
        print("no skills present in both trees")
        return 0

    # Explicit path, not find_and_load(ROOT) — that walks `start.parents`, which
    # excludes ROOT itself, and would silently return zero patterns. A guard with
    # no patterns is worse than no guard, so an empty set is a hard failure.
    patterns = load_personal_patterns(ROOT / ".eos-personal")
    if not patterns:
        print("ERROR: no patterns loaded from .eos-personal — refusing to sync unguarded.")
        return 2

    changed = refused = 0
    for slug in slugs:
        if not (GLOBAL / slug).is_dir():
            print(f"  skip {slug} — not in the user-global store")
            continue
        if not (BUNDLED / slug).is_dir():
            print(f"  skip {slug} — not a bundled skill (the repo decides what it ships)")
            continue
        if _is_gitignored(BUNDLED / slug):
            print(f"  skip {slug} — gitignored (personal by design, never shipped)")
            continue
        c, r = sync(slug, patterns, check_only=check_only)
        changed, refused = changed + c, refused + r

    print(
        f"\n{len(slugs)} skills · {changed} file(s) "
        f"{'would change' if check_only else 'synced'} · {refused} refused"
    )
    if refused:
        print("Refused files were NOT written. Add a substitution to SUBS, or fix the source.")
        return 2
    if check_only and changed:
        print("Bundled tree is out of date — run without --check.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
