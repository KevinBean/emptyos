#!/usr/bin/env python3
"""Scan committed files for personal data AND credential-shaped secrets.

Two independent pattern classes, one gate (this script is what the installed
pre-commit hook runs, so anything not checked here is not checked at commit
time at all):

  personal — regexes from `.eos-personal`. Whole-file exemption via ALLOWLIST.
  secrets  — `SECRET_PATTERNS` from `emptyos/capabilities/outbound_scan.py`,
             the same vocabulary the pre-cloud outbound scanner uses. No
             file-level allowlist; exempt a specific line with an inline
             `check-secrets: ignore` marker on it or the line above.

The secret class was added 2026-08-14. Until then this script loaded
`.eos-personal` only — which carries identity/path/coordinate patterns and
**zero credential shapes** — so an `sk-ant-...` in a tracked file passed the
commit gate, while `docs/`+skills already advertised "API keys, tokens in
tracked files" as covered. Calibrated before landing: 4 hits across 4,100
tracked files, all in two test-fixture files, zero on real code.

Secret findings print a redacted preview, never the matched line — a leak
report that echoes the key is a second leak.

Exit code 0 = clean, 1 = personal data or a secret found.

Usage:
    python scripts/check-personal.py              # scan all tracked files
    python scripts/check-personal.py --staged     # scan only staged files (for pre-commit)
    python scripts/check-personal.py --install-hook   # write .git/hooks/pre-commit
    python scripts/check-personal.py --patterns P --no-allowlist  # release snapshot scan
"""

import sys
from pathlib import Path

# Make `check_base` (sibling script) importable, and load both pattern sources
# DIRECTLY from their files rather than via `from emptyos.sdk...` /
# `from emptyos.capabilities...`. The package-style import triggers
# `emptyos/sdk/__init__.py`, which transitively imports `starlette` — fine when
# the project is `pip install -e .`-ed, fatal in a bare CI checkout that runs
# this script without project deps (GHA Release Safe). See v0.4.1 CI failure.
# `outbound_scan` is loadable this way only because it keeps its module scope
# stdlib-only and imports `emptyos.sdk` lazily inside `_personal_patterns()`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_common import load_by_path

_load_personal_patterns = load_by_path(
    "personal_patterns", "emptyos/sdk/personal_patterns.py"
).load

_outbound = load_by_path(
    "outbound_scan_patterns", "emptyos/capabilities/outbound_scan.py"
)
SECRET_PATTERNS = _outbound.SECRET_PATTERNS
_redact_preview = _outbound.redact_preview

# Same by-path trick: `release_filter` keeps its module scope stdlib-only for
# exactly this reason (see its header), so a bare CI checkout can load it.
_is_never_published = load_by_path(
    "release_filter_paths", "emptyos/sdk/release_filter.py"
).is_never_published

from check_base import (
    REPO_ROOT,
    git_staged,
    git_tracked,
    git_untracked,
    install_pre_commit_hook,
)

PATTERNS_FILE = ".eos-personal"

# Line-level opt-out for the SECRET class, on the matching line or the one
# above it. Deliberately not a file allowlist (`.claude/rules/audits.md`): a
# whole-file exemption would silently cover a real key added to that file
# later, and several ALLOWLIST entries below — `.claude/settings.local.json`,
# `data/personal-defaults.json` — are exactly the files most likely to hold a
# genuine token.
SECRET_IGNORE_MARKER = "check-secrets: ignore"

# Whole subtrees that are git-tracked in this private repo but never reach a
# public snapshot (`apps/personal/`, `engines/personal/`, `tests/personal/` —
# the list lives in `emptyos/sdk/release_filter.py` next to the prune that
# enforces it, and `release-public.py` asserts they are absent from every built
# snapshot). They were gitignored until 2026-08-16, which is the only reason
# this scanner's "tracked ⇒ published" premise held.
#
# Mutes the PERSONAL class only. A personal app is *supposed* to contain the
# owner's name, employer and vault paths — that is what makes it personal — so
# flagging it is the same false positive `check_no_private_apps` already
# documents. SECRETS are still scanned here, deliberately: a leaked credential
# is compromised the moment it enters git history, public snapshot or not.


# Files that are allowed to contain personal patterns. Applies to the PERSONAL
# class only — secrets are scanned in every file regardless.
ALLOWLIST = {
    ".eos-personal",  # the patterns file itself
    ".eos-personal-subs",  # sync_user_skills.py's literal → placeholder table
    "scripts/check-personal.py",  # this script
    "data/personal-defaults.json",
    ".claude/settings.local.json",  # machine-specific auto-approve rules
    "docs/PRIVACY.md",  # documents the threat model with illustrative examples
}
# The two `.eos-personal*` files are dropped from every public snapshot
# (release-public.py CRUFT_PATHS). The release scans the snapshot with
# `--patterns <private copy> --no-allowlist`, so an entry here can never again
# be how a file carrying personal data reaches the public repo — which is how
# the privacy test's examples and the sync script's table shipped until
# 2026-09-24.
# Binary extensions to skip
BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".wav",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".db",
    ".sqlite",
    ".pyc",
}


def _personal_muted(filepath: str, *, use_allowlist: bool = True) -> bool:
    """True if the PERSONAL class should skip this file. Secrets never skip."""
    return (use_allowlist and filepath in ALLOWLIST) or _is_never_published(filepath)


def _arg_value(flag: str) -> str | None:
    """Value of `flag` in argv (`--x v` or `--x=v`), or None. Missing value is a
    usage error — an unread `--patterns=path` would fall back to the default
    file and report clean on an empty pattern set."""
    for arg in sys.argv:
        if arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
            if not value:
                print(f"ERROR: {flag} needs a value")
                sys.exit(2)
            return value
    if flag not in sys.argv:
        return None
    i = sys.argv.index(flag)
    if i + 1 >= len(sys.argv):
        print(f"ERROR: {flag} needs a value")
        sys.exit(2)
    return sys.argv[i + 1]


def load_patterns(path: str):
    if not Path(path).exists():
        print(f"Warning: {path} not found, no patterns to check")
        return []
    return _load_personal_patterns(
        path,
        on_error=lambda line, err: print(f"Warning: invalid pattern '{line}': {err}"),
    )


def scan_content(
    filepath: str, content: str, patterns, *, allowed: bool
) -> tuple[list[tuple], list[tuple]]:
    """Scan one file's text. Returns `(personal_violations, secret_findings)`.

    Pure — no I/O, no git, no exit. `allowed` mutes the PERSONAL class only
    (an ALLOWLIST file is still scanned for secrets, deliberately).
    """
    violations: list[tuple] = []
    secrets: list[tuple] = []
    lines = content.splitlines()

    for lineno, line in enumerate(lines, 1):
        if not allowed:
            for pattern in patterns:
                if pattern.search(line):
                    violations.append(
                        (filepath, lineno, pattern.pattern, line.strip()[:120])
                    )

        # Secrets: every file, but honour a marker on this line or above it.
        prev = lines[lineno - 2] if lineno >= 2 else ""
        if SECRET_IGNORE_MARKER in line or SECRET_IGNORE_MARKER in prev:
            continue
        for name, pattern in SECRET_PATTERNS:
            m = pattern.search(line)
            if m:
                # Redacted preview only — echoing the match would leak it again.
                secrets.append((filepath, lineno, name, _redact_preview(m.group(0))))

    return violations, secrets


def get_files(staged_only: bool = False, include_untracked: bool = False) -> list[str]:
    """Repo-relative path strings (existing call sites read them as such)."""
    paths = git_staged() if staged_only else git_tracked()
    if include_untracked:
        paths = paths + git_untracked()
    return [p.relative_to(REPO_ROOT).as_posix() for p in paths]


def main():
    if "--install-hook" in sys.argv:
        sys.exit(
            install_pre_commit_hook(
                script="check-personal.py",
                backup_suffix=".pre-eos.bak",
                idempotent_marker="check-personal.py",
            )
        )
    staged_only = "--staged" in sys.argv
    # A file that has not been `git add`-ed is invisible to both scopes above,
    # so a bare run can print OK while a violation sits in a new file. Opt-in
    # rather than default: the pre-commit hook's `--staged` run is the gate, and
    # widening the default would make the release scanners walk scratch output.
    include_untracked = "--include-untracked" in sys.argv and not staged_only
    # A missing/empty `.eos-personal` disables the PERSONAL class only. It must
    # not take the SECRET class down with it: `SECRET_PATTERNS` is compiled in
    # and always available, and the public snapshot has carried no
    # `.eos-personal` since 2026-09-24 (docs/PRIVACY.md) — which under
    # the old blanket `sys.exit(0)` would have silently turned off credential
    # scanning in exactly the tree about to be published.
    #
    # `--patterns <path>` is the release's snapshot scan: the snapshot has no
    # `.eos-personal` of its own (it is dropped as cruft), so the private copy is
    # passed in. There an empty pattern set is a hard failure, not a warning — a
    # snapshot scanned without patterns reports clean for the wrong reason.
    # `--no-allowlist` stops ALLOWLIST muting files there, since nothing in a
    # public snapshot is entitled to carry personal data.
    explicit = _arg_value("--patterns")
    use_allowlist = "--no-allowlist" not in sys.argv
    patterns = load_patterns(explicit or PATTERNS_FILE)
    if explicit and not patterns:
        print(f"ERROR: no personal patterns loaded from {explicit} — refusing to report clean.")
        sys.exit(2)

    files = get_files(staged_only, include_untracked)
    violations = []
    secrets = []

    for filepath in files:
        if Path(filepath).suffix.lower() in BINARY_EXT:
            continue
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        v, s = scan_content(
            filepath, content, patterns,
            allowed=_personal_muted(filepath, use_allowlist=use_allowlist),
        )
        violations.extend(v)
        secrets.extend(s)

    if secrets:
        print(f"\n{'=' * 60}")
        print(f"  SECRET DETECTED in {len(secrets)} location(s)")
        print(f"{'=' * 60}\n")
        for filepath, lineno, name, preview in secrets:
            print(f"  {filepath}:{lineno}")
            print(f"    Pattern: {name}")
            print(f"    Match:   {preview}")
            print()
        print("A credential must never be committed. Treat any real value here as")
        print("COMPROMISED — rotate it at the provider, don't just delete the line")
        print("(it stays in git history).")
        print("Machine config belongs in emptyos.toml (gitignored); keys in data/secrets/.")
        print(f"If this is a pattern fixture, add `{SECRET_IGNORE_MARKER}` on that line.\n")

    if violations:
        print(f"\n{'=' * 60}")
        print(f"  PERSONAL DATA DETECTED in {len(violations)} location(s)")
        print(f"{'=' * 60}\n")
        for filepath, lineno, pattern, preview in violations:
            print(f"  {filepath}:{lineno}")
            print(f"    Pattern: {pattern}")
            print(f"    Content: {preview}")
            print()
        print(f"Fix these before committing. Patterns defined in {PATTERNS_FILE}")
        print("Move personal values to data/personal-defaults.json (git-ignored)\n")

    if violations or secrets:
        sys.exit(1)
    else:
        if staged_only:
            mode = "staged files"
        elif include_untracked:
            mode = "all tracked + untracked files"
        else:
            mode = "all tracked files"
        print(
            f"OK: No personal data or secrets found in {mode} "
            f"({len(files)} files, {len(patterns)} personal + "
            f"{len(SECRET_PATTERNS)} secret patterns)"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
