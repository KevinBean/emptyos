#!/usr/bin/env python3
"""Lint: app folder placement agrees with `release.toml` tiers.

The apps tree mirrors the release tiers:

    apps/public/core/<id>/            ← [tiers.core]
    apps/public/standard/<id>/        ← [tiers.standard] (direct members)
    apps/public/labs/<id>/            ← [tiers.labs]
    apps/extension/plus/<id>/         ← [tiers.plus] (premium; never public)
    apps/extension/engineering/<id>/  ← [tiers.engineering]
    apps/extension/english-learning/  ← [tiers.english-learning]
    apps/extension/portfolio/         ← [tiers.portfolio]
    apps/extension/plekto/            ← [tiers.plekto]
    apps/extension/dev/               ← [tiers.dev]
    apps/extension/others/            ← no tier (uncategorised; not checked)
    apps/{public,extension,personal}/labs/  ← WIP staging (extension/personal: not checked)

For every app under a group that maps to a tier, the app's **id** must appear in
that tier's *direct* `apps` list (the membership the folder represents), with
two tolerations: composite tiers (plekto/portfolio) reuse public apps that live
in the public track, and `others/` + non-public `labs/` staging are skipped.

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
    "others": None,
}


def main() -> int:
    al = load_by_path("app_layout_lint", "emptyos/sdk/app_layout.py")
    rt = load_by_path("release_tiers_lint", "emptyos/sdk/release_tiers.py")

    with open(ROOT / "release.toml", "rb") as f:
        tiers = tomllib.load(f).get("tiers", {}) or {}
    public = rt.tier_union(tiers, ("core", "standard"), "apps")

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

    if problems:
        print("DRIFT — app folder vs release.toml tier mismatch:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK: app folder placement matches release.toml tiers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
