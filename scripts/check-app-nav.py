#!/usr/bin/env python3
"""Scan app pages for the global EmptyOS nav include.

Every page under `apps/*/pages/*.html` (and `apps/personal/*/pages/*.html`) is
expected to load `/static/eos.js` so the global nav bar + speed-dial dock
appear. Apps that ship without it lose the back-to-Home / app-switcher /
keyboard-shortcut chrome that the rest of the system relies on — and the user
discovers it by accident on visit (cf. apps/store + apps/fix-agent, 2026-05-14).

Opt-out is allowed but must be explicit:

    <!-- eos-nav: skip — first-run onboarding has no chrome -->
    <!-- eos-nav: skip — embed page, host controls nav -->

The dash + rationale is required so future audits can survey why a given page
opted out. A bare `<!-- eos-nav: skip -->` is rejected.

Usage:
    python scripts/check-app-nav.py             # scan all tracked app pages
    python scripts/check-app-nav.py --staged    # scan only staged pages
    python scripts/check-app-nav.py --fix       # insert the canonical 3-script
                                                # block after <body> in every
                                                # violation
    python scripts/check-app-nav.py --install-hook   # write .git/hooks/pre-commit

Exit code 0 = clean, 1 = findings.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from _check_base import REPO_ROOT as ROOT, git_staged, git_tracked, install_pre_commit_hook

# Only HTML pages under apps/<id>/pages/ (community + personal) are scanned.
# Match both `apps/<id>/pages/*.html` and `apps/personal/<id>/pages/*.html`.
SCAN_GLOBS = ("apps/*/pages/*.html", "apps/personal/*/pages/*.html")

# Skip these subtrees entirely — they're either retired, template, or
# under-construction surface area not subject to the nav rule.
SKIP_FRAGMENTS = (
    "/_retired/",
    "/_example/",
    "/_catalog/",
)

NAV_INCLUDE_RE = re.compile(r"""/static/eos\.js""")
# Opt-out marker: `<!-- eos-nav: skip — <rationale> -->`. Requires the dash
# (— or -) plus at least one non-space char of rationale before `-->`. A
# bare `<!-- eos-nav: skip -->` is rejected (no rationale to audit later).
OPT_OUT_RE = re.compile(
    r"<!--\s*eos-nav\s*:\s*skip\s*[—-]\s*\S[^>]*-->",
    re.IGNORECASE,
)
# Permissive variant used to surface the "you tried to opt out but forgot the
# rationale" failure mode with a better error message than "missing nav".
OPT_OUT_BARE_RE = re.compile(r"<!--\s*eos-nav\s*:\s*skip[^>]*-->", re.IGNORECASE)

BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


@dataclass
class Finding:
    file: Path
    msg: str

    def format(self) -> str:
        rel = self.file.relative_to(ROOT) if self.file.is_relative_to(ROOT) else self.file
        return f"{rel} — {self.msg}"


def _matches_scan_glob(p: Path) -> bool:
    if not p.is_relative_to(ROOT):
        return False
    rel = p.relative_to(ROOT).as_posix()
    for pattern in SCAN_GLOBS:
        # Glob-style match using fnmatch-via-Path.match
        if Path(rel).match(pattern):
            return True
    return False


def _filter(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if not _matches_scan_glob(p):
            continue
        rel = "/" + p.relative_to(ROOT).as_posix()
        if any(skip in rel for skip in SKIP_FRAGMENTS):
            continue
        if not p.exists():
            continue
        out.append(p)
    return out


def _app_id_from_page(path: Path) -> str:
    """apps/<id>/pages/foo.html → <id>;  apps/personal/<id>/pages/foo.html → <id>."""
    return path.parent.parent.name


def _scan(path: Path) -> Finding | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if NAV_INCLUDE_RE.search(text):
        return None
    if OPT_OUT_RE.search(text):
        return None
    if OPT_OUT_BARE_RE.search(text):
        return Finding(
            path,
            "opt-out marker present but missing rationale — "
            "use `<!-- eos-nav: skip — <reason> -->`",
        )
    return Finding(
        path,
        "missing `<script src=\"/static/eos.js\">` "
        "(global nav). Add it, or opt out with "
        "`<!-- eos-nav: skip — <reason> -->`.",
    )


# ---------- --fix --------------------------------------------------------------

# The canonical 3-script block, inserted immediately after the <body ...> open
# tag. Matches the shape used by apps/billing, apps/agent, etc.
INSERT_BLOCK_TEMPLATE = (
    "\n<script src=\"/static/eos.js\"></script>"
    "\n<script src=\"/static/eos-components.js\"></script>"
    "\n<script>EOS.nav('{app_id}');</script>"
)


def _fix_one(path: Path) -> bool:
    """Insert the canonical nav block. Returns True on success."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    m = BODY_OPEN_RE.search(text)
    if not m:
        return False
    app_id = _app_id_from_page(path)
    block = INSERT_BLOCK_TEMPLATE.format(app_id=app_id)
    new_text = text[: m.end()] + block + text[m.end() :]
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


# ---------- driver -------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--staged", action="store_true", help="scan only staged files")
    p.add_argument(
        "--fix",
        action="store_true",
        help="insert the canonical nav block in every violation",
    )
    p.add_argument(
        "--install-hook",
        action="store_true",
        help="install .git/hooks/pre-commit that runs --staged",
    )
    args = p.parse_args()

    if args.install_hook:
        return install_pre_commit_hook(
            script="check-app-nav.py",
            backup_suffix=".pre-app-nav.bak",
            idempotent_marker="check-app-nav.py",
        )

    candidates = git_staged() if args.staged else git_tracked()
    files = _filter(candidates)

    findings: list[Finding] = []
    for f in files:
        finding = _scan(f)
        if finding is not None:
            findings.append(finding)

    if not findings:
        print(f"clean — scanned {len(files)} app page(s)")
        return 0

    if args.fix:
        fixed: list[Path] = []
        skipped: list[Finding] = []
        for f in findings:
            # Don't auto-fix "rationale missing" — that's a writer-intent
            # problem the human has to resolve.
            if "rationale" in f.msg:
                skipped.append(f)
                continue
            if _fix_one(f.file):
                fixed.append(f.file)
            else:
                skipped.append(Finding(f.file, "fix failed (no <body> tag?)"))
        print(f"fixed {len(fixed)} file(s):")
        for path in fixed:
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            print(f"  {rel}")
        if skipped:
            print(f"\nskipped {len(skipped)} file(s):")
            for f in skipped:
                print(f"  {f.format()}")
            return 1
        return 0

    print(f"{len(findings)} finding(s) (scanned {len(files)} app page(s)):\n")
    for f in sorted(findings, key=lambda x: str(x.file)):
        print(f"  {f.format()}")
    print("\nRun with --fix to auto-insert the canonical nav block.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
