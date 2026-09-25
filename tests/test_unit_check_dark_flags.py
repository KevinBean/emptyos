"""check_dark_flags — the settings-store read path.

Daemon-free. This scanner had **no tests** until 2026-08-28, and the gap cost a
real wrong conclusion: its settings-store scan only inspected *top-level scalar*
keys, but the settings service stores an app's toggles NESTED under its id
(``{"agent_fleet": {"feature": {"fleet": {"enabled": true}}}}``). The top-level
value is a dict, so the scalar filter skipped it and four live, enabled features
were reported as dark — including ``operate.feature.executor.enabled``, the
desktop actuation gate.

Both directions are pinned: a nested truthy toggle must be FOUND, and the
"unset" values that `.claude/rules/app-ui-patterns.md` says must fall through to
TOML (``null``, ``""``, ``false``) must NOT be credited as ON.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mod():
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "check_dark_flags", REPO / "scripts" / "check_dark_flags.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- the flattener ----------------------------------------------------------

def test_flatten_normalises_nested_store_to_the_key_setting_or_config_reads(mod):
    flat = mod._flatten_settings(
        {"agent_fleet": {"feature": {"fleet": {"enabled": True}}, "ttl_minutes": 30}})
    assert flat["agent_fleet.feature.fleet.enabled"] is True
    assert flat["agent_fleet.ttl_minutes"] == 30


def test_flatten_passes_through_an_already_dotted_flat_key(mod):
    """The settings page writes some keys verbatim as one dotted string."""
    flat = mod._flatten_settings({"feature.projects-offline-writes.enabled": True})
    assert flat["feature.projects-offline-writes.enabled"] is True


def test_flatten_tolerates_a_non_dict(mod):
    assert mod._flatten_settings(None) == {}
    assert mod._flatten_settings([1, 2]) == {}


# --- machine_state, against a real temp settings.json ------------------------

def _run(mod, tmp_path, monkeypatch, settings: dict, flag: str = "feature.fleet.enabled"):
    """Point the scanner's REPO_ROOT at a temp dir carrying only settings.json."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8")
    (tmp_path / "emptyos.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    return mod.machine_state({flag: {}}).get(flag, [])


def test_nested_enabled_toggle_is_credited_as_on(mod, tmp_path, monkeypatch):
    hits = _run(mod, tmp_path, monkeypatch,
                {"agent_fleet": {"feature": {"fleet": {"enabled": True}}}})
    assert any("settings.json" in h and "agent_fleet.feature.fleet.enabled" in h
               for h in hits), hits


@pytest.mark.parametrize("value", [False, None, "", 0])
def test_unset_or_false_is_not_credited_as_on(mod, tmp_path, monkeypatch, value):
    """null/"" mean 'fall through to TOML'; false means genuinely off."""
    hits = _run(mod, tmp_path, monkeypatch,
                {"agent_fleet": {"feature": {"fleet": {"enabled": value}}}})
    assert hits == [], f"{value!r} must not read as ON, got {hits}"


def test_a_different_slug_is_not_credited(mod, tmp_path, monkeypatch):
    hits = _run(mod, tmp_path, monkeypatch,
                {"agent_fleet": {"feature": {"something-else": {"enabled": True}}}})
    assert hits == [], hits


def test_bare_dotted_key_without_an_app_prefix_is_credited(mod, tmp_path, monkeypatch):
    """`feature.<slug>.enabled` with no namespace — the shape app-ui-patterns.md
    warns about, and one really is stored that way on this machine."""
    hits = _run(mod, tmp_path, monkeypatch,
                {"feature": {"fleet": {"enabled": True}}})
    assert any("settings.json" in h for h in hits), hits


def test_legacy_top_level_scalar_heuristic_still_fires(mod, tmp_path, monkeypatch):
    """Deliberately preserved — widening it over the flattened map would trade
    the nested fix for a new false-positive class."""
    hits = _run(mod, tmp_path, monkeypatch, {"voice-assistant.fleet": True})
    assert any(h.startswith("setting ") for h in hits), hits
