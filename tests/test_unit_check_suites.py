"""Both-direction pins for `check_suites.py`.

This scanner gates at `scope: ["always", "release"]`, and the 2026-08-31 change
widened what it looks at (audit finding F4). New gating logic with no pin is
exactly what `.claude/rules/audits.md` § Failure mode 3 is about: it passed on a
healthy tree from the first run, which proves nothing until it has been watched
fail for each shape it claims to cover.

The three functions under test are pure over an `{id: Path}` mapping, so most of
this needs no fixture tree — the paths only have to be shaped like real ones.

Daemon-free and kernel-free.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_common import load_by_path  # noqa: E402

cs = load_by_path("check_suites_under_test", "scripts/check_suites.py")

APPS = ROOT / "apps"


def _apps(**by_id: str) -> dict[str, Path]:
    """{'kb': 'public/standard'} -> {'kb': <ROOT>/apps/public/standard/kb}."""
    return {aid: APPS / rel / aid for aid, rel in by_id.items()}


# ── catalog_ids: the F4 fix ──────────────────────────────────────────────────

def test_extension_apps_are_in_the_catalog():
    """The whole of F4: `public/`-only meant 47 extension apps could never be
    reported as unassigned, however long they sat there."""
    ids = cs.catalog_ids(_apps(
        grill="extension/dev",
        earthing="extension/engineering",
        academy="extension/english-learning",
    ))
    assert ids == {"grill", "earthing", "academy"}


def test_public_apps_are_still_in_the_catalog():
    """The inverse — a fix that dropped the public track would also pass above."""
    ids = cs.catalog_ids(_apps(task="public/core", kb="public/standard", canvas="public/labs"))
    assert ids == {"task", "kb", "canvas"}


def test_distribution_groups_are_not_in_the_catalog():
    assert cs.catalog_ids(_apps(
        code="extension/plekto", explore="extension/plus", welcome="extension/portfolio",
    )) == set()


# ── group_report: no app may be silently dropped ─────────────────────────────

def test_a_flat_app_is_reported_unclassified_not_dropped():
    """A flat `apps/<id>/` app matched neither set and vanished from the coverage
    figure, the advisory AND the notes — a silent exemption inside the fix for
    silent exemptions."""
    notes = cs.group_report({"test-app-wiring": APPS / "test-app"})
    assert any("UNCLASSIFIED" in n and "test-app-wiring" in n for n in notes)


def test_distribution_group_is_noted_with_its_reason():
    notes = cs.group_report(_apps(explore="extension/plus"))
    assert any("plus" in n and "premium bundle" in n and "explore" in n for n in notes)


def test_a_catalogued_app_produces_no_note():
    """Otherwise every app would be 'reported' and the notes would mean nothing."""
    assert cs.group_report(_apps(kb="public/standard")) == []


def test_the_three_buckets_sum_to_the_total_on_the_real_tree():
    """The invariant group_report's docstring claims: counted + noted +
    unclassified == tracked. If any bucket silently drops an app this breaks."""
    apps = cs.collect_apps()
    counted = len(cs.catalog_ids(apps))
    named = set()
    for note in cs.group_report(apps):
        named |= {p.strip().split(" ")[0] for p in note.split(":", 1)[1].split(",")}
    assert counted + len(named) == len(apps), (
        f"counted={counted} noted/unclassified={len(named)} tracked={len(apps)}"
    )


# ── held_ids_in_public_suites: the flag-vs-reality gap ───────────────────────

def test_a_public_suite_naming_a_held_app_is_reported():
    catalog = {"suite": [{"id": "studio", "apps": ["kb", "held-app"]}]}
    apps = _apps(kb="public/standard", **{"held-app": "extension/labs"})
    out = cs.held_ids_in_public_suites(catalog, apps)
    assert len(out) == 1 and "held-app" in out[0] and "studio" in out[0]


def test_a_private_suite_is_not_reported():
    """filter_suites_toml drops it wholesale, so its ids never ship."""
    catalog = {"suite": [{"id": "engineer", "private": True, "apps": ["held-app"]}]}
    apps = _apps(**{"held-app": "extension/labs"})
    assert cs.held_ids_in_public_suites(catalog, apps) == []


def test_a_public_suite_of_public_apps_is_not_reported():
    catalog = {"suite": [{"id": "knowledge", "apps": ["kb"]}]}
    assert cs.held_ids_in_public_suites(catalog, _apps(kb="public/standard")) == []


def test_it_finds_the_known_violations_on_the_real_tree():
    """Anchored to reality, not just to fixtures. Update the expectation when
    the leak is actually closed — do not loosen it to make a change pass."""
    from helpers import public_snapshot

    if public_snapshot():
        pytest.skip("public snapshot: the extension track and its suite entries are dropped")
    out = cs.held_ids_in_public_suites(cs.load_catalog(), cs.collect_apps())
    suites = {line.split("'")[1] for line in out}
    assert suites == {"studio", "automation"}, out


# ── the constant that must not drift ─────────────────────────────────────────

def test_public_tiers_matches_the_release_script():
    """A comment saying 'the two must agree' cannot enforce it. Disagreement
    means this scanner reports a leak the release does not have, or misses one
    it does."""
    rp = load_by_path("relpub_for_suites", "scripts/release-public.py")
    assert tuple(cs.PUBLIC_TIERS) == tuple(rp.PUBLIC_TIERS)


def test_suite_groups_and_distribution_groups_are_disjoint():
    """An overlap would make a group both counted and exempt."""
    assert not (cs.SUITE_GROUPS & set(cs.DISTRIBUTION_GROUPS))


@pytest.mark.parametrize("group", sorted(cs.DISTRIBUTION_GROUPS))
def test_every_exempt_group_states_a_reason(group):
    assert cs.DISTRIBUTION_GROUPS[group].strip(), f"{group} exempt with no stated reason"
