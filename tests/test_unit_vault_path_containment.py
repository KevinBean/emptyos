"""BaseApp.vault_path must stay inside the app's own vault directory.

Apps pass ids that arrived from a route straight in (`nest` builds
`f"{rid}/floorplan.json"`), so `filename` is caller-supplied AND legitimately
nested — the guard has to allow nesting and refuse only escape.
"""

from __future__ import annotations

import types

import pytest

from emptyos.sdk import base_app_vault as v


@pytest.fixture
def app(tmp_path):
    """Minimal stub carrying just what the vault_* helpers touch."""
    vault = tmp_path / "vault"
    appdir = vault / "30_Resources" / "EmptyOS" / "demo"
    appdir.mkdir(parents=True)
    (appdir / "own.md").write_text("mine", encoding="utf-8")
    (vault / "elsewhere.md").write_text("not mine", encoding="utf-8")
    (tmp_path / "OUTSIDE.md").write_text("outside the vault", encoding="utf-8")

    stub = types.SimpleNamespace(vault_dir=appdir, vault_root=vault)
    for name in ("vault_path", "vault_read", "vault_write", "vault_read_at", "vault_write_at"):
        setattr(stub, name, types.MethodType(getattr(v, name), stub))
    return stub, vault, appdir, tmp_path


def test_ordinary_and_nested_paths_still_resolve(app):
    stub, _vault, appdir, _tmp = app
    assert stub.vault_path("own.md") == appdir / "own.md"
    # Nesting is legitimate — nest stores `<room-id>/floorplan.json`.
    assert stub.vault_path("room-1/floorplan.json") == appdir / "room-1" / "floorplan.json"
    assert stub.vault_read("own.md") == "mine"
    stub.vault_write("sub/new.md", "written")
    assert (appdir / "sub" / "new.md").read_text(encoding="utf-8") == "written"


@pytest.mark.parametrize("escape", [
    "../elsewhere.md",
    "..\\elsewhere.md",              # Windows separator — route params allow it
    "../../../OUTSIDE.md",
    "room-1/../../elsewhere.md",     # escapes only after a legitimate-looking prefix
])
def test_escape_is_refused_loudly_on_write_and_softly_on_read(app, escape):
    stub, _vault, _appdir, _tmp = app
    # The path builder and every write fail loudly...
    with pytest.raises(ValueError, match="escapes"):
        stub.vault_path(escape)
    with pytest.raises(ValueError, match="escapes"):
        stub.vault_write(escape, "should not land")
    # ...while the read keeps its documented "returns default" contract.
    assert stub.vault_read(escape, "fallback") == "fallback"


def test_read_default_contract_is_unchanged_for_a_plain_miss(app):
    stub, _vault, _appdir, _tmp = app
    assert stub.vault_read("no-such-file.md", "fallback") == "fallback"


def test_at_helpers_are_bounded_by_the_vault_root_not_the_app_dir(app):
    stub, vault, _appdir, tmp = app
    # Reaching elsewhere IN the vault is the whole point of the _at helpers.
    assert stub.vault_read_at("elsewhere.md") == "not mine"
    stub.vault_write_at("other-app/note.md", "ok")
    assert (vault / "other-app" / "note.md").exists()
    # Leaving the vault entirely is not.
    assert stub.vault_read_at("../OUTSIDE.md", "fallback") == "fallback"
    with pytest.raises(ValueError, match="escapes"):
        stub.vault_write_at("../OUTSIDE.md", "should not land")
    assert (tmp / "OUTSIDE.md").read_text(encoding="utf-8") == "outside the vault"
