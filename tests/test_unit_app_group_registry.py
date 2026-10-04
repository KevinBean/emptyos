"""Every app group folder on disk is known to both grouping checkers.

A new `apps/<track>/<group>/` needs a row in two places:
`check-tier-folder.py`'s `GROUP_TIER` (which tier the folder belongs to) and
`check_suites.py`'s `SUITE_GROUPS` or `DISTRIBUTION_GROUPS` (whether its apps
are product features that need a suite chapter). Missing the first makes
check-tier-folder fail loudly; missing the second is quieter — its apps turn
into an "UNCLASSIFIED" note and drop out of suite coverage, which is how
`apps/extension/business/` (editions M9, 2026-09-28) would have gone unnoticed.
This pins both registries against the tree, so the next new group goes red
here instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from check_common import load_by_path  # noqa: E402

tier_folder = load_by_path("tier_folder_under_test", "scripts/check-tier-folder.py")
suites = load_by_path("suites_under_test", "scripts/check_suites.py")


def _groups_on_disk() -> set[str]:
    """Group folders that directly contain at least one app (a manifest)."""
    out: set[str] = set()
    for track in ("public", "extension"):
        root = REPO / "apps" / track
        if not root.is_dir():
            continue
        for group in root.iterdir():
            if group.is_dir() and any((a / "manifest.toml").is_file() for a in group.iterdir() if a.is_dir()):
                out.add(group.name)
    return out


def test_the_tree_has_groups_to_check():
    # A walk that found nothing would pass both assertions below vacuously.
    assert {"core", "standard", "labs"} <= _groups_on_disk()


def test_every_group_on_disk_has_a_tier_mapping():
    missing = _groups_on_disk() - set(tier_folder.GROUP_TIER)
    assert not missing, f"add to GROUP_TIER in scripts/check-tier-folder.py: {sorted(missing)}"


def test_every_group_on_disk_is_classified_for_suites():
    known = set(suites.SUITE_GROUPS) | set(suites.DISTRIBUTION_GROUPS)
    missing = _groups_on_disk() - known
    assert not missing, (
        "add to SUITE_GROUPS (feature apps) or DISTRIBUTION_GROUPS (a bundle, "
        f"with its reason) in scripts/check_suites.py: {sorted(missing)}"
    )
