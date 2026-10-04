"""Release gate: KB notes stay aligned with the shipped app/engine tree.

Re-runs scripts/audit_kb_app_alignment.py and fails on integrity-breaking
findings — dangling implemented_in paths, app KB references that point at a
missing note or section, apps using KB without declaring the dependency,
duplicate slugs, invalid kinds, and broken verification targets.

Incomplete-but-not-broken findings (a formula note without a verification
case or implementation yet) are reported as warnings and do NOT fail the gate
— a formula may legitimately be authored before its case lands.

Exit codes (mirrors scripts/check-clickable.py so release-public.py can treat
them the same way):
  0  clean (warnings allowed)
  1  one or more blocking findings — release should abort
  2  vault not configured / not present — gate skipped

Run standalone any time:  python scripts/check-kb-alignment.py
"""

from __future__ import annotations

import sys

# Sibling import: when run as a script, scripts/ is sys.path[0], so the
# reusable scanner resolves without packaging. audit_kb_app_alignment has no
# leading underscore, so it is tracked (scripts/_*.py is gitignored).
from audit_kb_app_alignment import audit

# Integrity breaks — a link points nowhere, an app references a missing note,
# a slug collides, a kind is invalid, a verification target doesn't resolve.
BLOCKING = {
    "duplicate_kb_slug",
    "invalid_kind",
    "unresolved_verified_against",
    "verification_target_not_case",
    "broken_implemented_in",
    "missing_implemented_symbol",
    "broken_app_kb_slug",
    "broken_app_kb_section",
    "app_uses_kb_without_manifest_dependency",
}
# Incomplete, not broken — reported, never blocks.
WARN = {
    "formula_missing_verification",
    "formula_missing_implementation",
}


def main() -> int:
    try:
        result = audit()
    except SystemExit as exc:
        print(f"check-kb-alignment: SKIP — {exc}")
        return 2

    issues = result.get("issues", {})
    summary = result.get("summary", {})
    print(
        "KB/app alignment gate: "
        f"{summary.get('kb_notes', 0)} notes, "
        f"{summary.get('active_apps', 0)} apps, "
        f"{summary.get('implemented_in_links', 0)} implemented_in links"
    )

    for key in sorted(WARN):
        rows = issues.get(key) or []
        if rows:
            print(f"  WARN  {key}: {len(rows)}")

    blocking = {key: issues.get(key) or [] for key in BLOCKING if issues.get(key)}
    if not blocking:
        print("  OK: no blocking KB/app alignment findings")
        return 0

    total = 0
    for key, rows in sorted(blocking.items()):
        total += len(rows)
        print(f"  FAIL  {key}: {len(rows)}")
        for row in rows[:10]:
            print(f"        - {row}")
        if len(rows) > 10:
            print(f"        ... and {len(rows) - 10} more")
    print(
        f"\ncheck-kb-alignment: {total} blocking finding(s). "
        "Run `python scripts/audit_kb_app_alignment.py` for full detail."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
