"""The public site (eos.binbian.net) lists only what the public version ships.

`scripts/generate_emptyos_site.py` filtered its per-app pages to PUBLIC_TIERS
but built `apps.md` and `plugins.md` from every manifest in the repo, so the
live catalogue named each held engineering app with a one-line description of
what it calculates, and listed the held plugins. These tests run the real
generator against a temporary site dir and read what it wrote.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("gen_site_under_test", SCRIPTS / "generate_emptyos_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def site(tmp_path, monkeypatch):
    gen = _load()
    site_dir = tmp_path / "30_Resources" / "EmptyOS-Site"
    site_dir.mkdir(parents=True)
    monkeypatch.setattr(gen, "fetch_integrity_audit", lambda: None)  # no daemon
    monkeypatch.setattr(sys, "argv", ["generate_emptyos_site.py", "--vault", str(tmp_path)])
    return gen, site_dir


def _held_app_ids(gen) -> set[str]:
    from emptyos.sdk.app_layout import iter_app_dirs, track_of

    apps_root = ROOT / "apps"
    held = {aid for aid, d in iter_app_dirs(apps_root) if track_of(d, apps_root) != "public"}
    return held - gen.public_app_ids()


def _held_plugin_ids(gen) -> set[str]:
    on_disk = {p.parent.name for p in (ROOT / "plugins").glob("*/manifest.toml")}
    return on_disk - gen.public_plugin_ids() - {"_retired", "_example"}


def test_catalogue_names_no_held_app(site):
    gen, site_dir = site
    held = _held_app_ids(gen)
    assert held, "fixture premise: the repo has non-public apps to leak"
    gen.main()
    text = (site_dir / "apps.md").read_text(encoding="utf-8")
    leaked = sorted(a for a in held if f"**{a}**" in text)
    assert leaked == []
    assert "**task**" in text  # the catalogue is not simply empty


def test_plugin_list_names_no_held_plugin(site):
    gen, site_dir = site
    held = _held_plugin_ids(gen)
    assert held, "fixture premise: the repo has non-public plugins to leak"
    gen.main()
    text = (site_dir / "plugins.md").read_text(encoding="utf-8")
    leaked = sorted(p for p in held if f"**{p}**" in text or f"`{p}`" in text)
    assert leaked == []


def test_unreadable_release_toml_refuses_instead_of_listing_everything(site, monkeypatch):
    gen, site_dir = site
    monkeypatch.setattr(gen, "public_app_ids", lambda: set())
    with pytest.raises(SystemExit):
        gen.main()
    assert not (site_dir / "apps.md").exists()
