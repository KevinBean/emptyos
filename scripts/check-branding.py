#!/usr/bin/env python3
"""Scan user-facing code for third-party product mentions.

Reads .eos-branding for regex patterns, scans app UIs, manifests, prompts,
and error messages. Plugin code is exempt (plugins necessarily name their service).

Usage:
    python scripts/check-branding.py              # scan all tracked files
    python scripts/check-branding.py --staged     # scan only staged files (for pre-commit)
"""

import re
import sys
from pathlib import Path

from check_base import REPO_ROOT, git_staged, git_tracked

PATTERNS_FILE = ".eos-branding"

# Directories/files exempt from branding checks (they integrate with specific services)
EXEMPT_PREFIXES = (
    "plugins/",
    ".eos-branding",
    "scripts/check-branding.py",
    "scripts/",  # migration/utility scripts
    "CLAUDE.md",  # development rules reference product names for documentation
    "DESIGN.md",  # design tokens contract — references brands when stating "don't" rules
    "docs/",  # design docs may reference integrations
    ".claude/",  # claude code config
    "skills/",  # skill definitions
    "data/",  # runtime data
    "tests/",  # test fixtures
    "results/",  # benchmark/test results
    ".gitignore",  # folder names like .obsidian
    "emptyos.example.toml",
    "emptyos.toml.example",
    "release.toml",  # build manifest — plugin IDs are functional identifiers
)

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

# Files where product names are functional identifiers (provider selectors, CSS classes)
FUNCTIONAL_FILES = {
    "apps/assistant/pages/index.html",  # backend selector uses provider names
    "emptyos/web/static/topology.html",  # service registry uses service names
}

# Line-level patterns that are NOT branding violations (technical identifiers)
FALSE_POSITIVE_RE = [
    re.compile(r"\.obsidian"),  # .obsidian folder name
    re.compile(r"obsidian://"),  # URI scheme (technical protocol)
    re.compile(
        r"Obsidian(Search|CLI|Provider|Plugin)"
    ),  # code identifiers (CLI plugin, search provider)
    re.compile(r"obsidian-cli"),  # plugin ID
    re.compile(r"obs-link|obs-icon"),  # CSS class names
    re.compile(r'"vault-obsidian-format"'),  # skill ID
    re.compile(r'"suno[_-]'),  # legacy field alias (backwards compat)
]


def load_patterns(path: str) -> list[re.Pattern]:
    p = Path(path)
    if not p.exists():
        print(f"Warning: {path} not found, no patterns to check")
        return []
    patterns = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as e:
            print(f"Warning: invalid pattern '{line}': {e}")
    return patterns


def get_files(staged_only: bool = False) -> list[str]:
    paths = git_staged() if staged_only else git_tracked()
    return [p.relative_to(REPO_ROOT).as_posix() for p in paths]


def is_exempt(filepath: str) -> bool:
    normalized = filepath.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def is_false_positive(line: str) -> bool:
    return any(fp.search(line) for fp in FALSE_POSITIVE_RE)


def main():
    staged_only = "--staged" in sys.argv
    patterns = load_patterns(PATTERNS_FILE)
    if not patterns:
        sys.exit(0)

    files = get_files(staged_only)
    violations = []

    for filepath in files:
        if is_exempt(filepath):
            continue
        if Path(filepath).suffix.lower() in BINARY_EXT:
            continue
        normalized = filepath.replace("\\", "/")
        if normalized in FUNCTIONAL_FILES:
            continue

        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Skip comments in Python files
            if filepath.endswith(".py") and stripped.startswith("#"):
                continue
            # Skip Python docstrings (triple-quoted) — internal documentation
            if stripped.startswith(('"""', "'''")):
                continue
            # Skip import/class/def lines (code identifiers, not user-facing)
            if stripped.startswith(("import ", "from ", "class ", "def ")):
                continue
            # Skip CSS comments
            if stripped.startswith("/*") or stripped.startswith("*"):
                continue
            # Skip JS comments
            if stripped.startswith("//"):
                continue

            for pattern in patterns:
                if pattern.search(line):
                    # Check false positives (folder names, URI schemes, code identifiers)
                    if is_false_positive(line):
                        continue
                    violations.append((filepath, lineno, pattern.pattern, stripped[:120]))

    if violations:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  THIRD-PARTY BRANDING in {len(violations)} location(s)", file=sys.stderr)
        print(f"{'=' * 60}\n", file=sys.stderr)
        for filepath, lineno, pattern, preview in violations:
            # Sanitize for console output
            safe = preview.encode("ascii", "replace").decode("ascii")
            print(f"  {filepath}:{lineno}", file=sys.stderr)
            print(f"    Pattern: {pattern}", file=sys.stderr)
            print(f"    Content: {safe}", file=sys.stderr)
            print(file=sys.stderr)
        print("Use generic terms instead. See .eos-branding for details.", file=sys.stderr)
        print("Exempt: plugin code, docs, CLAUDE.md, provider selectors\n", file=sys.stderr)
        sys.exit(1)
    else:
        mode = "staged files" if staged_only else "all tracked files"
        print(
            f"OK: No third-party branding found in {mode} ({len(files)} files, {len(patterns)} patterns)"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
