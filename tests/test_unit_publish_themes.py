"""Unit tests for publish ↔ design-system theme resolution (no daemon).

Covers the relationship wiring: a `design-system-*` KB note's ```json
`publish_theme` fence resolves to publish's 14-key theme and injects into the
generated site CSS — the same code path `builder.build()` runs at line ~539.

templates.py is pure (json/re/pathlib only) so it loads standalone. Reads the
real vault for the design-system notes; skips gracefully if absent.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_TEMPLATES = _REPO / "apps/public/standard/publish/templates.py"
_THEME_KEYS = (
    "bg", "bg_card", "bg_input", "text", "text_heading", "text_secondary",
    "text_muted", "border", "border_strong", "accent", "accent_bg",
    "success", "warning", "danger",
)


@pytest.fixture(scope="module")
def t():
    spec = importlib.util.spec_from_file_location("pub_templates", _TEMPLATES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vault():
    # emptyos.toml is gitignored — a fresh clone has only emptyos.toml.example,
    # so this raised FileNotFoundError at fixture setup and the tests ERRORED
    # rather than skipping. A machine with no config simply has no vault.
    cfg = _REPO / "emptyos.toml"
    if not cfg.exists():
        pytest.skip("emptyos.toml is absent; vault root unknown")
    with open(cfg, "rb") as f:
        p = (tomllib.load(f).get("notes") or {}).get("path") or ""
    return Path(p) if p else None


def test_builtin_theme_unchanged(t):
    v = t.resolve_theme_vars("eos")
    assert v is t.THEME_VARS["eos"]
    assert all(k in v for k in _THEME_KEYS)


def test_unknown_theme_falls_back(t):
    v = t.resolve_theme_vars("design-system-does-not-exist", _REPO)  # repo has no such note
    assert all(k in v for k in _THEME_KEYS)  # always 14 keys, never breaks a build


def test_design_system_theme_resolves(t, vault):
    if not vault or not (vault / "30_Resources/EmptyOS/kb/notes/design-system-vercel.md").exists():
        pytest.skip("design-system-vercel note not in this vault")
    v = t.resolve_theme_vars("design-system-vercel", vault)
    assert all(k in v for k in _THEME_KEYS)
    assert v["bg"].lower() == "#ffffff"
    assert v["accent"].lower() == "#171717"  # Vercel's monochrome primary


def test_emptyos_theme_matches_builtin_eos(t, vault):
    if not vault or not (vault / "30_Resources/EmptyOS/kb/notes/design-system-emptyos.md").exists():
        pytest.skip("design-system-emptyos note not in this vault")
    v = t.resolve_theme_vars("design-system-emptyos", vault)
    # The emptyos note mirrors THEME_VARS["eos"].
    assert v["accent"].lower() == t.THEME_VARS["eos"]["accent"].lower()
    assert v["bg"].lower() == t.THEME_VARS["eos"]["bg"].lower()


def test_get_site_css_injects_vars(t, vault):
    if not vault or not (vault / "30_Resources/EmptyOS/kb/notes/design-system-stripe.md").exists():
        pytest.skip("design-system-stripe note not in this vault")
    vars = t.resolve_theme_vars("design-system-stripe", vault)
    css = t.get_site_css("design-system-stripe", vars=vars)
    assert "--accent: #533afd" in css  # Stripe indigo lands in :root
    assert "--bg: #ffffff" in css


def test_load_missing_note_returns_none(t):
    assert t.load_design_system_theme(_REPO, "design-system-nope") is None
