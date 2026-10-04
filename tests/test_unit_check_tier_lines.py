"""Both directions for `scripts/check_tier_lines.py`.

Green on the live tree proves nothing until each rule has been watched to fire
(`.claude/rules/audits.md` § Failure mode 3), so every rule gets a fixture that
breaks it on purpose next to one that must stay silent. The live-tree cases
mutate the parsed `release.toml` in memory, so the named regression — a hosted
allowlist gaining an app its line never shipped — is tested against the real
tiers, not a toy.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "check_tier_lines", REPO / "scripts" / "check_tier_lines.py")
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)


def _live_data() -> dict:
    with open(REPO / "release.toml", "rb") as f:
        return tomllib.load(f)


def _live() -> dict:
    return _live_data()["tiers"]


def _live_scan(tiers: dict) -> list[dict]:
    return chk.scan_tiers(tiers, _live_data()["product_lines"])


def _scan(tiers: dict, product_lines: dict | None = None) -> list[dict]:
    """Scan with every line id the fixture uses registered as a public line,
    unless a test passes its own registry — so each test exercises only the
    rule it names."""
    if product_lines is None:
        product_lines = {
            t["product_line"]: {} for t in tiers.values()
            if isinstance(t.get("product_line"), str)
        }
    return chk.scan_tiers(tiers, product_lines)


def _codes(findings: list[dict]) -> list[tuple[str, str]]:
    return [(f["tier"], f["code"]) for f in findings]


def _base() -> dict:
    """A trunk root, a trunk tier, a branch tier on the trunk, a hosted branch allowlist."""
    return {
        "core": {"product_line": "emptyos", "apps": ["hub"], "plugins": ["health"]},
        "standard": {"product_line": "emptyos", "extends": "core", "apps": ["task"]},
        "learn": {"product_line": "branch", "extends": "standard",
                  "apps": ["dictionary", "academy"], "plugins": ["tts"]},
        "learn-cloud": {"product_line": "branch",
                        "apps": ["hub", "dictionary"], "plugins": ["health", "tts"]},
    }


# ── the live tree ──────────────────────────────────────────────────────────

def test_live_tree_is_clean():
    assert chk.scan() == []


def test_every_live_tier_declares_a_line():
    tiers = _live()
    # The operand exists (the public snapshot keeps the 6 non-private tiers);
    # an empty parse would pass vacuously.
    assert len(tiers) >= 3
    assert all(chk._line(t) for t in tiers.values())


def test_live_hosted_tier_gaining_an_off_line_app_goes_red():
    tiers = copy.deepcopy(_live())
    hosted = sorted(n for n, t in tiers.items() if n != chk.ROOT and "extends" not in t)
    if not hosted:
        # The public snapshot drops private tiers, and the hosted one with them.
        pytest.skip("no hosted allowlist tier in this tree")
    name = hosted[0]
    tiers[name]["apps"] = [*tiers[name]["apps"], "not-an-app-of-this-line"]
    assert (name, "hosted_not_subset") in _codes(_live_scan(tiers))


def test_live_tier_losing_its_line_goes_red():
    tiers = copy.deepcopy(_live())
    del tiers["standard"]["product_line"]
    assert ("standard", "missing_line") in _codes(_live_scan(tiers))


# ── missing_line ───────────────────────────────────────────────────────────

def test_healthy_fixture_is_clean():
    assert _scan(_base()) == []


@pytest.mark.parametrize("value", [None, "", "EnglishOS", "english os", ["emptyos"], 1, "-x", "x-", "a--b"])
def test_missing_or_malformed_line_fires(value):
    tiers = _base()
    if value is None:
        del tiers["standard"]["product_line"]
    else:
        tiers["standard"]["product_line"] = value
    assert ("standard", "missing_line") in _codes(_scan(tiers))


def test_empty_tiers_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        _scan({})


def test_live_registry_is_used_by_scan():
    # `scan()` must read [product_lines]; without it every live tier is unknown.
    data = _live_data()
    assert data.get("product_lines"), "release.toml has no [product_lines] registry"
    assert chk.scan_tiers(data["tiers"], {}) != []


def test_scan_reads_the_registry_from_release_toml(tmp_path, monkeypatch):
    # scan() must hand scan_tiers the file's [product_lines], not a registry
    # rebuilt from the tiers themselves (which would register every typo).
    (tmp_path / "release.toml").write_text(
        '[product_lines.emptyos]\n'
        '[tiers.core]\nproduct_line = "emptyos"\napps = []\n'
        '[tiers.std]\nproduct_line = "emptyos"\nextends = "core"\napps = []\n'
        '[tiers.typo]\nproduct_line = "emptyso"\nextends = "core"\napps = []\n',
        encoding="utf-8")
    monkeypatch.setattr(chk, "REPO", tmp_path)
    assert _codes(chk.scan()) == [("typo", "unknown_line")]


# ── unknown_line / private_line ────────────────────────────────────────────

def test_a_mistyped_line_id_fires_unknown_line():
    tiers = _base()
    registry = {"emptyos": {}, "branch": {}}
    tiers["learn"]["product_line"] = "brnach"
    assert ("learn", "unknown_line") in _codes(_scan(tiers, registry))


def test_a_registry_entry_that_is_not_a_table_is_unknown():
    tiers = _base()
    assert ("learn", "unknown_line") in _codes(
        _scan(tiers, {"emptyos": {}, "branch": "EnglishOS"}))


def test_a_public_tier_in_a_private_line_fires():
    tiers = _base()
    registry = {"emptyos": {}, "branch": {"private": True}}
    tiers["learn-cloud"]["private"] = True
    got = _codes(_scan(tiers, registry))
    assert got == [("learn", "private_line")]


def test_private_tiers_in_a_private_line_are_fine():
    tiers = _base()
    registry = {"emptyos": {}, "branch": {"private": True}}
    tiers["learn"]["private"] = True
    tiers["learn-cloud"]["private"] = True
    assert _scan(tiers, registry) == []


def test_a_private_tier_in_a_public_line_is_fine():
    tiers = _base()
    tiers["standard"]["private"] = True
    assert _scan(tiers, {"emptyos": {}, "branch": {}}) == []


# ── cross_line_extends ─────────────────────────────────────────────────────

def test_branch_extending_trunk_is_fine():
    assert not [f for f in _scan(_base()) if f["code"] == "cross_line_extends"]


def test_branch_extending_its_own_line_is_fine():
    tiers = _base()
    tiers["learn-plus"] = {"product_line": "branch", "extends": "learn", "apps": ["x"]}
    assert _scan(tiers) == []


def test_branch_extending_another_branch_fires():
    tiers = _base()
    tiers["other"] = {"product_line": "other", "extends": "learn", "apps": []}
    assert _codes(_scan(tiers)) == [("other", "cross_line_extends")]


def test_trunk_extending_a_branch_fires():
    tiers = _base()
    tiers["trunk-top"] = {"product_line": "emptyos", "extends": "learn", "apps": []}
    assert _codes(_scan(tiers)) == [("trunk-top", "cross_line_extends")]


# ── hosted_not_subset ──────────────────────────────────────────────────────

def test_hosted_with_an_extra_app_fires_and_names_it():
    tiers = _base()
    tiers["learn-cloud"]["apps"].append("radio")
    got = _scan(tiers)
    assert _codes(got) == [("learn-cloud", "hosted_not_subset")]
    assert "radio" in got[0]["detail"]


def test_hosted_with_an_extra_plugin_fires():
    tiers = _base()
    tiers["learn-cloud"]["plugins"].append("comfyui")
    assert _codes(_scan(tiers)) == [("learn-cloud", "hosted_not_subset")]


def test_subset_must_hold_against_ONE_tier_not_a_union():
    # apps covered only by `learn`, plugins only by `learn-b`: no single tier
    # covers both, so the allowlist is not a slice of anything that ships.
    tiers = _base()
    tiers["learn"]["plugins"] = []
    tiers["learn-b"] = {"product_line": "branch", "extends": "standard",
                        "apps": [], "plugins": ["tts"]}
    assert _codes(_scan(tiers)) == [("learn-cloud", "hosted_not_subset")]


def test_hosted_may_not_borrow_from_another_line():
    # Every item exists in the trunk's resolved set, but the line is different.
    tiers = _base()
    tiers["learn-cloud"]["product_line"] = "lonely"
    got = _scan(tiers)
    assert _codes(got) == [("learn-cloud", "hosted_not_subset")]
    assert "no extending tier" in got[0]["detail"]


def test_root_is_exempt_from_the_subset_rule():
    tiers = _base()
    tiers["core"]["apps"].append("only-in-core")
    assert _scan(tiers) == []


def test_the_root_is_exempt_even_with_nothing_to_measure_against():
    tiers = {
        "core": {"product_line": "emptyos", "apps": ["hub"]},
        "learn": {"product_line": "branch", "extends": "core", "apps": ["dictionary"]},
    }
    assert _scan(tiers) == []


def test_a_descendant_cannot_vouch_for_its_ancestor():
    # A tier extending the allowlist contains it by construction, so measuring
    # against it would pass anything the allowlist lists.
    tiers = _base()
    tiers["learn-cloud"]["apps"].append("radio")
    tiers["learn-cloud-pro"] = {"product_line": "branch", "extends": "learn-cloud"}
    assert ("learn-cloud", "hosted_not_subset") in _codes(_scan(tiers))


def test_two_bare_allowlists_cannot_vouch_for_each_other():
    tiers = _base()
    tiers["zz-a"] = {"product_line": "zz", "apps": ["anything"]}
    tiers["zz-b"] = {"product_line": "zz", "apps": ["anything"]}
    got = _codes(_scan(tiers))
    assert ("zz-a", "hosted_not_subset") in got and ("zz-b", "hosted_not_subset") in got


@pytest.mark.parametrize("order", ["fitting-first", "fitting-last"])
def test_one_fitting_sibling_is_enough_in_either_order(order):
    # `learn` lacks the plugin; `learn-b` covers everything. Names sort so the
    # fitting sibling comes first or last.
    tiers = _base()
    tiers["learn"]["plugins"] = []
    fit = "a-fit" if order == "fitting-first" else "zz-fit"
    tiers[fit] = {"product_line": "branch", "extends": "standard",
                  "apps": ["dictionary"], "plugins": ["tts"]}
    assert _scan(tiers) == []


# ── bad_extends ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["standrad", "", ["standard"]])
def test_bad_extends_fires_and_escapes_nothing(value):
    tiers = _base()
    tiers["x"] = {"product_line": "branch", "extends": value,
                  "apps": ["anything"], "plugins": ["comfyui"]}
    assert _codes(_scan(tiers)) == [("x", "bad_extends")]


def test_extends_cycle_is_reported_on_each_member_once():
    tiers = _base()
    tiers["a"] = {"product_line": "branch", "extends": "b"}
    tiers["b"] = {"product_line": "branch", "extends": "a"}
    tiers["c"] = {"product_line": "branch", "extends": "a"}
    got = _codes(_scan(tiers))
    assert sorted(got) == [("a", "bad_extends"), ("b", "bad_extends")]


def test_a_broken_parent_is_reported_once_not_on_every_child():
    tiers = _base()
    tiers["x"] = {"product_line": "branch", "extends": "gone"}
    tiers["y"] = {"product_line": "branch", "extends": "x"}
    assert _codes(_scan(tiers)) == [("x", "bad_extends")]


# ── exit code ──────────────────────────────────────────────────────────────

def test_exit_code_is_the_finding_count(tmp_path, monkeypatch, capsys):
    (tmp_path / "release.toml").write_text(
        '[product_lines.emptyos]\ndisplay_name = "EmptyOS"\n'
        '[tiers.core]\nproduct_line = "emptyos"\napps = []\n'
        '[tiers.std]\nproduct_line = "emptyos"\nextends = "core"\napps = []\n'
        '[tiers.a]\nextends = "core"\napps = []\n'
        '[tiers.b]\nextends = "core"\napps = []\n',
        encoding="utf-8")
    monkeypatch.setattr(chk, "REPO", tmp_path)
    assert chk.main([]) == 2
    assert "missing_line" in capsys.readouterr().out
