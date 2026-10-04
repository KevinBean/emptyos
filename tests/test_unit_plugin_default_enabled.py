"""A plugin whose manifest says `default_enabled = false` arrives installed but off.

Pins the fresh-install contract behind the open editions ("nothing arrives
unasked" — the edition guide in the vault project `emptyos-editions`, §1.2
rule 6; contract text in `.claude/rules/store.md` § First-boot seed): such a
plugin is seeded into the Store's
disabled list, so the kernel never loads it and it starts no process until the
user turns it on. Existing installs are never re-seeded, an explicit
`[plugins.<id>] enabled = true` opts back in, and an essential stays on.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from emptyos.kernel.plugin_loader import ESSENTIAL_PLUGINS, PluginLoader, PluginManifest
from emptyos.runtime import store_state

ROOT = Path(__file__).resolve().parent.parent

# Kevin, 2026-09-29: each starts a process or hooks the OS at boot.
DEFAULT_OFF = ("pronounce", "voice-api", "dogfood-demo", "global-hotkey", "system-tray")


def _manifest(tmp: Path, pid: str, extra: str = "") -> Path:
    d = tmp / "plugins" / pid
    d.mkdir(parents=True)
    (d / "manifest.toml").write_text(
        f'[plugin]\nid = "{pid}"\nversion = "1.0.0"\n{extra}\n', encoding="utf-8"
    )
    return d / "manifest.toml"


def _loader(tmp: Path, manifests: dict[str, str], sections: dict | None = None,
            demo: bool = False) -> PluginLoader:
    sections = sections or {}
    config = SimpleNamespace(
        data_dir=tmp / "data",
        demo_enabled=demo,
        get_section=lambda name: sections.get(name, {}),
    )
    loader = PluginLoader(SimpleNamespace(config=config))
    for pid, extra in manifests.items():
        loader.manifests[pid] = PluginManifest.from_toml(_manifest(tmp, pid, extra))
    return loader


def test_manifest_parses_default_enabled(tmp_path):
    assert PluginManifest.from_toml(_manifest(tmp_path, "a")).default_enabled is True
    off = PluginManifest.from_toml(_manifest(tmp_path, "b", "default_enabled = false"))
    assert off.default_enabled is False


def test_a_quoted_false_is_refused_not_read_as_true(tmp_path):
    with pytest.raises(ValueError, match="default_enabled"):
        PluginManifest.from_toml(_manifest(tmp_path, "c", 'default_enabled = "false"'))


def test_fresh_install_seeds_default_off_plugin_installed_but_not_enabled(tmp_path):
    loader = _loader(tmp_path, {"quiet": "", "noisy": "default_enabled = false"})
    enabled = loader.enabled_ids()
    assert "quiet" in enabled
    assert "noisy" not in enabled
    # Installed, so the Store can switch it on with one click.
    assert store_state.installed_ids(tmp_path / "data", "plugins") >= {"quiet", "noisy"}
    assert store_state.disabled_ids(tmp_path / "data", "plugins") == {"noisy"}
    assert "noisy" not in loader.enabled_manifests()


def test_turning_it_on_in_the_store_loads_it(tmp_path):
    loader = _loader(tmp_path, {"noisy": "default_enabled = false"})
    loader.enabled_ids()
    assert store_state.mark_enabled(tmp_path / "data", "plugins", "noisy")
    assert "noisy" in loader.enabled_ids()


def test_existing_install_is_never_reseeded(tmp_path):
    data = tmp_path / "data"
    store_state.seed_if_missing(data, "plugins", [("noisy", "1.0.0")])
    loader = _loader(tmp_path, {"noisy": "default_enabled = false"})
    assert "noisy" in loader.enabled_ids()


def test_explicit_toml_opt_in_wins_at_first_seed(tmp_path):
    loader = _loader(
        tmp_path,
        {"noisy": "default_enabled = false"},
        sections={"plugins.noisy": {"enabled": True}},
    )
    assert "noisy" in loader.enabled_ids()


@pytest.mark.parametrize("section", [
    {"enabled": "true"},            # a quoted string is not an opt-in
    {"host": "10.0.0.5"},           # configuring it is not switching it on
    {"enabled": False},
])
def test_only_a_literal_true_opts_in(tmp_path, section):
    loader = _loader(
        tmp_path, {"noisy": "default_enabled = false"}, sections={"plugins.noisy": section}
    )
    assert "noisy" not in loader.enabled_ids()


def test_demo_mode_loads_default_off_plugins_like_every_other(tmp_path):
    loader = _loader(tmp_path, {"noisy": "default_enabled = false"}, demo=True)
    assert "noisy" in loader.enabled_ids()


def test_an_essential_stays_on_even_if_its_manifest_says_off(tmp_path):
    essential = next(iter(ESSENTIAL_PLUGINS))
    loader = _loader(tmp_path, {essential: "default_enabled = false"})
    assert essential in loader.enabled_ids()
    assert essential not in store_state.disabled_ids(tmp_path / "data", "plugins")


def test_seed_ignores_disabled_ids_it_does_not_install(tmp_path):
    data = tmp_path / "data"
    store_state.seed_if_missing(data, "plugins", [("a", "1")], disabled=["a", "ghost"])
    assert store_state.disabled_ids(data, "plugins") == {"a"}


def test_exactly_the_decided_plugins_ship_default_off():
    """Kevin chose these five; any other plugin going quietly off (edge-tts, the
    default voice, say) is a product decision, not a refactor."""
    off = set()
    for path in sorted((ROOT / "plugins").glob("*/manifest.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data.get("plugin", {}).get("default_enabled", True) is not True:
            off.add(data["plugin"]["id"])
    assert off == set(DEFAULT_OFF)
