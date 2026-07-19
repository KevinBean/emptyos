"""check_suites.py — validate suites.toml (the EmptyOS suite catalog) against the app tree.

The suite catalog (`suites.toml`, repo root) is descriptive product taxonomy:
suites → member atom-app ids. This scanner keeps it honest:

GATES (exit 1):
  - a suite/chrome member id that resolves to NO tracked app manifest
    (typo or retired app — the catalog would silently lie);
  - a duplicate suite id, or an app listed twice within one suite;
  - a `surface` id that is neither "" nor a resolvable app id.

ADVISORY (reported, never gates):
  - tracked public apps (public/core|standard|labs) in no suite and not in
    `chrome` — new apps should be assigned or consciously chromed;
  - an app listed in both `chrome` and a suite (pick one).

Pure file I/O (tomllib + app_layout), no kernel import, daemon-safe.

Usage:
  python scripts/check_suites.py [--json]
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from emptyos.sdk.app_layout import iter_app_dirs  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

CATALOG = ROOT / "suites.toml"


def load_catalog() -> dict:
    with open(CATALOG, "rb") as f:
        return tomllib.load(f)


def collect_apps() -> dict[str, Path]:
    """id -> app dir for every tracked (non-personal) app."""
    return {aid: p for aid, p in iter_app_dirs(ROOT / "apps", include_personal=False)}


def public_ids(apps: dict[str, Path]) -> set[str]:
    out = set()
    for aid, p in apps.items():
        rel = p.relative_to(ROOT / "apps").as_posix()
        if rel.startswith("public/"):
            out.add(aid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not CATALOG.is_file():
        msg = "suites.toml not found at repo root"
        if args.json:
            return emit_json(False, "missing_catalog", msg)
        print(f"FAIL: {msg}")
        return 1

    cat = load_catalog()
    apps = collect_apps()
    suites = cat.get("suite", []) or []
    chrome = list(cat.get("chrome", []) or [])

    errors: list[str] = []
    advisories: list[str] = []

    seen_suite_ids: set[str] = set()
    assigned: set[str] = set()
    for s in suites:
        sid = s.get("id") or "?"
        if sid in seen_suite_ids:
            errors.append(f"duplicate suite id '{sid}'")
        seen_suite_ids.add(sid)
        members = s.get("apps", []) or []
        seen_members: set[str] = set()
        for aid in members:
            if aid in seen_members:
                errors.append(f"suite '{sid}': app '{aid}' listed twice")
            seen_members.add(aid)
            if aid not in apps:
                errors.append(f"suite '{sid}': app '{aid}' does not resolve to any tracked manifest")
        assigned |= seen_members
        surface = s.get("surface", "")
        if surface:
            if surface not in apps:
                errors.append(f"suite '{sid}': surface '{surface}' does not resolve to any tracked manifest")
            assigned.add(surface)  # a suite's surface app is covered by that suite

    for aid in chrome:
        if aid not in apps:
            errors.append(f"chrome: app '{aid}' does not resolve to any tracked manifest")
        if aid in assigned:
            advisories.append(f"'{aid}' is in chrome AND in a suite — pick one")

    unassigned = sorted(public_ids(apps) - assigned - set(chrome))
    if unassigned:
        advisories.append(
            "public apps in no suite and not chrome: " + ", ".join(unassigned)
        )

    ok = not errors
    n_suites, n_members = len(suites), len(assigned)
    msg = f"{n_suites} suites, {n_members} assigned apps; {len(errors)} errors, {len(advisories)} advisories"
    if args.json:
        return emit_json(ok, "unresolved_ids", msg, {"errors": errors, "advisories": advisories})

    for e in errors:
        print(f"ERROR: {e}")
    for a in advisories:
        print(f"advisory: {a}")
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
