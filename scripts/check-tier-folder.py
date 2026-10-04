#!/usr/bin/env python3
"""Lint: app folder placement agrees with `release.toml` tiers.

The apps tree mirrors the release tiers:

    apps/public/core/<id>/            ← [tiers.core]
    apps/public/standard/<id>/        ← [tiers.standard] (direct members)
    apps/public/labs/<id>/            ← [tiers.labs]
    apps/public/englishos/<id>/       ← [tiers.englishos] (open EnglishOS edition)
    apps/extension/plus/<id>/         ← [tiers.plus] (premium; never public)
    apps/extension/engineering/<id>/  ← [tiers.engineering]
    apps/extension/english-learning/  ← [tiers.english-learning]
    apps/extension/portfolio/         ← [tiers.portfolio]
    apps/extension/plekto/            ← [tiers.plekto]
    apps/extension/dev/               ← [tiers.dev]
    apps/extension/business/          ← [tiers.business] (closed add-ons; never public)
    apps/extension/others/            ← no tier (uncategorised; not checked)
    apps/{public,extension,personal}/labs/  ← WIP staging (extension/personal: not checked)

For every app under a group that maps to a tier, the app's **id** must appear in
that tier's *direct* `apps` list (the membership the folder represents), with
two tolerations: composite tiers (plekto/portfolio) reuse public apps that live
in the public track, and `others/` + non-public `labs/` staging are skipped.

Second, INDEPENDENT check — every id a tier names must resolve on disk. The
folder check above walks folder -> tier over apps only; this one walks
tier -> disk over the other four arrays (`plugins`, `skills`, `engines`,
`services`), which have no folder-group mirror and were therefore unvalidated.
That gap was not theoretical: `[tiers.standard].skills` carried `dev-new-app`
after the skill was renamed to `eos-new-app`, and `package-release.py`
warns-and-continues on a missing skill, so the standard bundle silently shipped
18 of its 19 declared skills.

Resolution follows the packagers (`scripts/package-release.py` "Tier skills" /
"Tier plugins" / "Tier services", and `emptyos/sdk/release_filter.py`'s engine
pruning), but is deliberately STRICTER in two places rather than identical:
a plugin needs `manifest.toml` (the packager copies any directory) and a skill
must be a directory (the packager accepts any path). Both differences fail a
release EARLIER than the build would, which is the safe direction — but it is a
near-mirror, not an equivalence, and `tests/test_unit_release_prune.py` pins the
agreement that matters rather than trusting this paragraph.

Exit non-zero on any mismatch. Standalone — no kernel boot. Pure-CI-safe (loads
app_layout + release_tiers by path).
"""

from __future__ import annotations

import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from check_common import load_by_path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"

# Folder group -> release.toml tier. None = no membership rule (skip).
GROUP_TIER: dict[str, str | None] = {
    "core": "core",
    "standard": "standard",
    "labs": "labs",
    "plus": "plus",
    "engineering": "engineering",
    "english-learning": "english-learning",
    "english_learning": "english-learning",  # filesystem-safe alias
    "portfolio": "portfolio",
    "plekto": "plekto",
    "dev": "dev",
    "business": "business",
    "englishos": "englishos",
    "others": None,
}


def _resolves(kind: str, ident: str) -> bool:
    """Does a tier-declared non-app id exist on disk?

    One function per kind rather than a single glob, because each packager
    looks in a specific place and a laxer rule here would pass ids the build
    then drops:

      plugins  — `plugins/<id>/manifest.toml`. STRICTER than the packager, which
                 copies any directory; a plugin without a manifest cannot load,
                 so failing here beats shipping it.
      skills   — `skills/<id>/` OR `.agents/skills/<id>/`. NOT `.claude/skills/`:
                 that path is in release.toml's [exclude], so a skill living only
                 there would never ship. Same two locations as the packager;
                 stricter only in requiring a directory rather than any path.
      engines  — `engines/<id>/` (release_filter prunes by directory name)
      services — `services/<id>/` (package-release.py copies the directory)
    """
    if kind == "plugins":
        return (ROOT / "plugins" / ident / "manifest.toml").is_file()
    if kind == "skills":
        return (ROOT / "skills" / ident).is_dir() or (
            ROOT / ".agents" / "skills" / ident
        ).is_dir()
    if kind == "engines":
        return (ROOT / "engines" / ident).is_dir()
    if kind == "services":
        return (ROOT / "services" / ident).is_dir()
    raise ValueError(f"unknown tier resource kind: {kind!r}")


# Every array a tier can declare besides `apps` (which the folder check above
# covers). `services` is here because package-release.py resolves
# ("apps", "plugins", "skills", "services") and packages service dirs with the
# same warn-and-continue that let `dev-new-app` ship — leaving it out would have
# closed the defect class in three arrays of four while the docstring claimed
# otherwise.
RESOURCE_KINDS = ("plugins", "skills", "engines", "services")


def check_tier_resources(tiers: dict) -> list[str]:
    """Every plugin/skill/engine/service id named by a tier must exist on disk."""
    problems: list[str] = []
    for tier_name in sorted(tiers):
        for kind in RESOURCE_KINDS:
            declared = tiers[tier_name].get(kind, []) or []
            if isinstance(declared, str):
                # `skills = "eos-new-app"` (quotes instead of brackets) would
                # otherwise iterate per character and bury the real defect under
                # one finding per letter. Name the shape instead.
                problems.append(
                    f"[tiers.{tier_name}].{kind}: expected an array, got a string "
                    f"({declared!r}) — wrap it in brackets"
                )
                continue
            for ident in declared:
                if not _resolves(kind, ident):
                    problems.append(
                        f"[tiers.{tier_name}].{kind}: '{ident}' resolves to nothing on disk"
                    )
    return problems


def main() -> int:
    al = load_by_path("app_layout_lint", "emptyos/sdk/app_layout.py")
    rt = load_by_path("release_tiers_lint", "emptyos/sdk/release_tiers.py")

    with open(ROOT / "release.toml", "rb") as f:
        tiers = tomllib.load(f).get("tiers", {}) or {}
    public = rt.tier_union(tiers, rt.PUBLIC_TIERS, "apps")

    problems: list[str] = []
    discovered = list(al.iter_app_dirs(APPS, include_personal=False))
    by_id: dict[str, list[Path]] = defaultdict(list)
    for app_id, app_dir in discovered:
        by_id[app_id].append(app_dir)
    for app_id, app_dirs in sorted(by_id.items()):
        if len(app_dirs) > 1:
            rels = ", ".join(str(path.relative_to(APPS)).replace("\\", "/") for path in app_dirs)
            problems.append(f"duplicate app id '{app_id}': {rels}")

    for app_id, app_dir in discovered:
        track = al.track_of(app_dir, APPS)
        group = al.group_of(app_dir, APPS)
        if track == "personal" or not group:
            continue
        if group not in GROUP_TIER:
            problems.append(f"{track}/{group}/{app_id}: group '{group}' has no tier mapping")
            continue
        tier = GROUP_TIER[group]
        if tier is None:
            continue  # others/ — uncategorised, no rule
        # extension/labs is WIP staging, not a tier membership claim.
        if group == "labs" and track == "extension":
            continue
        if tier not in tiers:
            problems.append(f"{track}/{group}/: no matching [tiers.{tier}]")
            continue
        direct = set(tiers[tier].get("apps", []) or [])
        if track == "public":
            # Public-track groups (core/standard/labs) must hold DIRECT members
            # of their own tier — a core-tier app doesn't belong in public/standard.
            if app_id not in direct:
                problems.append(
                    f"{track}/{group}/{app_id}: not a direct member of [tiers.{tier}].apps "
                    f"(move it to the group matching its tier)"
                )
        else:
            # Extension groups may reuse public apps (composite tiers like
            # plekto/portfolio), so a public-union member is allowed here.
            if app_id not in direct and app_id not in public:
                problems.append(f"{track}/{group}/{app_id}: id not in [tiers.{tier}].apps")

    problems.extend(check_tier_resources(tiers))

    if problems:
        print("DRIFT — release.toml tiers vs the tree:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK: app folder placement + tier plugin/skill/engine/service ids match release.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
