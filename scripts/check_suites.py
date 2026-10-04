"""check_suites.py — validate suites.toml (the EmptyOS suite catalog) against the app tree.

The suite catalog (`suites.toml`, repo root) is descriptive product taxonomy:
suites → member atom-app ids. This scanner keeps it honest:

GATES (exit 1):
  - a suite/chrome member id that resolves to NO tracked app manifest
    (typo or retired app — the catalog would silently lie);
  - a duplicate suite id, or an app listed twice within one suite;
  - a `surface` id that is neither "" nor a resolvable app id.

ADVISORY (reported, never gates):
  - tracked FEATURE apps in no suite and not in `chrome` — new apps should be
    assigned or consciously chromed;
  - an app listed in both `chrome` and a suite (pick one);
  - a NON-private suite naming apps the public snapshot drops. `filter_suites_toml`
    keys on `private`, which is a per-SUITE flag over per-APP membership, so a
    mixed suite ships its held members' ids. Same defect
    `prune_release_toml_to_snapshot` fixes for release.toml, in the file filtered
    four lines later — the criterion has to be *does this ship*, not *is this
    flagged*. Advisory because there are pre-existing violations (7 ids across
    `studio` and `automation` as of 2026-08-31); a gate here would fire on a
    healthy tree, which `.claude/rules/audits.md` forbids.

"Feature apps" = the groups in SUITE_GROUPS (public core/standard/labs plus
extension engineering/dev/english-learning/business). This used to read `public/` only,
which meant 47 extension apps could never be *reported* as unassigned however
long they sat there — audit finding F4 in
`docs/APPS-MATRIX-AUDIT-2026-08-28.md`, and the reason suite coverage sat at
87/157 unnoticed. The id-resolution GATE always covered every tracked app; it
was only the coverage advisory that was narrow.

Groups genuinely outside the taxonomy (single-distribution bundles, staging)
are in DISTRIBUTION_GROUPS and printed as `note:` lines on every run rather
than silently skipped — an unstated exemption is the defect this scanner had.
Anything in NEITHER set is reported as UNCLASSIFIED rather than dropped, so
counted + noted + unclassified always sums to the tracked total; without that
bucket a flat `apps/<id>/` app vanished from all three, which was the original
blind spot one level down.

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

from emptyos.sdk.app_layout import group_of, iter_app_dirs  # noqa: E402
from emptyos.sdk.release_tiers import PUBLIC_TIERS, tier_union  # noqa: E402
from scanner_lib import emit_json  # noqa: E402

CATALOG = ROOT / "suites.toml"

# What the public snapshot actually ships: PUBLIC_TIERS, imported above from
# emptyos/sdk/release_tiers.py — the same definition release-public.py filters
# by, so this scanner cannot disagree with the release about what ships.


def load_catalog() -> dict:
    with open(CATALOG, "rb") as f:
        return tomllib.load(f)


def collect_apps() -> dict[str, Path]:
    """id -> app dir for every tracked (non-personal) app."""
    return {aid: p for aid, p in iter_app_dirs(ROOT / "apps", include_personal=False)}


# Folder groups whose apps are product features, and so are expected to appear
# in some suite. Mirrors check-tier-folder.py's GROUP_TIER in spirit: the suite
# catalog is a product taxonomy, and every feature-bearing group needs a chapter.
SUITE_GROUPS = {
    "core",
    "standard",
    "labs",
    "engineering",
    "dev",
    "english-learning",
    "english_learning",  # filesystem-safe alias
    "business",          # closed consulting tools (editions M9)
    "englishos",         # open EnglishOS edition (editions M10)
}

# Groups deliberately outside the chapter taxonomy, with the reason. These are
# single-distribution bundles, not product chapters: a branded landing page or a
# one-app premium bundle is not something a user "digests" as a chapter, and
# every one of them is held from the public snapshot, so a public suite naming
# them would leak the ids (see `private` in suites.toml's header).
# NOT silent — group_report() prints their contents on every run, because an
# unstated exemption is exactly the blind spot this scanner had (audit F4).
DISTRIBUTION_GROUPS = {
    "portfolio": "branded portfolio distribution",
    "plekto": "branded Plekto distribution",
    "plus": "premium bundle",
    "others": "uncategorised staging",
}


def _group_of(app_dir: Path) -> str:
    """Group folder of an app dir, or '' for a flat/depth-2 app.

    Delegates to app_layout rather than re-splitting the path: the shared helper
    resolves both sides and returns () on ValueError/OSError, where a hand-rolled
    `relative_to` on an unresolved path raises — a traceback instead of a finding,
    in a gating scanner.
    """
    return group_of(app_dir, ROOT / "apps")


def catalog_ids(apps: dict[str, Path]) -> set[str]:
    """Ids expected to carry a suite (or chrome) assignment."""
    return {aid for aid, p in apps.items() if _group_of(p) in SUITE_GROUPS}


def group_report(apps: dict[str, Path]) -> list[str]:
    """One line per app outside the chapter taxonomy, so none is silently dropped.

    Every app lands in exactly one of three buckets — counted, noted-with-reason,
    or UNCLASSIFIED — and the three sum to the tracked total. The unclassified
    bucket exists because the first version of this function only reported groups
    it already knew about: a flat app (`apps/<id>/`, a layout `iter_app_dirs`
    explicitly supports) matched neither set and vanished from the coverage
    figure, the advisory and the notes alike. That is the same silent exemption
    the `public/`-prefix filter was, one level down.
    """
    buckets: dict[str, list[str]] = {}
    unclassified: list[str] = []
    for aid, p in apps.items():
        group = _group_of(p)
        if group in SUITE_GROUPS:
            continue
        if group in DISTRIBUTION_GROUPS:
            buckets.setdefault(group, []).append(aid)
        else:
            rel = p.relative_to(ROOT / "apps").as_posix()
            unclassified.append(f"{aid} ({rel})")
    out = [
        f"{g} ({DISTRIBUTION_GROUPS[g]}), not in the chapter taxonomy: "
        + ", ".join(sorted(ids))
        for g, ids in sorted(buckets.items())
    ]
    if unclassified:
        out.append(
            "UNCLASSIFIED — in no known group, so counted by nothing above: "
            + ", ".join(sorted(unclassified))
        )
    return out


def held_ids_in_public_suites(catalog: dict, apps: dict[str, Path]) -> list[str]:
    """Non-private suites that name apps the public snapshot will not contain.

    `filter_suites_toml` drops a suite marked `private = true` — but privacy is
    per-SUITE while membership is per-APP, so a mixed suite ships its held
    members' ids. That is the same defect `prune_release_toml_to_snapshot` fixes
    for release.toml, in the file filtered four lines later: the criterion has to
    be *does this ship*, not *is this flagged*.

    Advisory, never a gate — there are pre-existing violations, and per
    `.claude/rules/audits.md` a signal that fires on a healthy tree must not
    block. Reported so the count cannot grow unnoticed.
    """
    try:
        with open(ROOT / "release.toml", "rb") as f:
            tiers = tomllib.load(f).get("tiers") or {}
    except OSError:
        return []

    # Same resolution the release itself uses (PUBLIC_TIERS),
    # via the shared helper rather than a private `extends` walk — that walk
    # already exists three times and this scanner's sibling,
    # check-tier-folder.py, imports exactly this function.
    public = tier_union(tiers, PUBLIC_TIERS, "apps")
    out = []
    for s in catalog.get("suite", []) or []:
        if s.get("private"):
            continue
        held = sorted(a for a in (s.get("apps") or []) if a in apps and a not in public)
        if held:
            out.append(
                f"suite '{s.get('id')}' is not private but names {len(held)} held "
                "app(s) the public snapshot drops: " + ", ".join(held)
            )
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

    unassigned = sorted(catalog_ids(apps) - assigned - set(chrome))
    if unassigned:
        advisories.append(
            "feature apps in no suite and not chrome: " + ", ".join(unassigned)
        )
    notes = group_report(apps)
    advisories.extend(held_ids_in_public_suites(cat, apps))

    ok = not errors
    n_suites, n_members = len(suites), len(assigned)
    covered = len(catalog_ids(apps) & (assigned | set(chrome)))
    n_catalog = len(catalog_ids(apps))
    msg = (
        f"{n_suites} suites, {n_members} assigned apps; coverage {covered}/{n_catalog} "
        f"feature apps; {len(errors)} errors, {len(advisories)} advisories"
    )
    if args.json:
        return emit_json(
            ok,
            "unresolved_ids",
            msg,
            {"errors": errors, "advisories": advisories, "notes": notes},
        )

    for e in errors:
        print(f"ERROR: {e}")
    for a in advisories:
        print(f"advisory: {a}")
    for n in notes:
        print(f"note: {n}")
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
