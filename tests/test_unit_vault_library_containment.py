"""VaultLibrary.find_file must not resolve outside its own library directory.

Apps call `detail(f"{id}.md")` with an id that came from a route, so the
filename is caller-supplied. Both fallback branches used to escape: the direct
join, and — less obviously — `rglob`, which returns the outside file for a
pattern containing `..` rather than raising.
"""

from __future__ import annotations

import types

import pytest

from emptyos.sdk.utils import contained_path as _contained
from emptyos.sdk.vault_library import VaultLibrary


@pytest.fixture
def library(tmp_path):
    """A VaultLibrary whose fallback dir is `<vault>/lib`, with no VaultIndex."""
    vault = tmp_path / "vault"
    lib = vault / "lib"
    (lib / "nested").mkdir(parents=True)
    (lib / "real.md").write_text("inside", encoding="utf-8")
    (lib / "nested" / "deep.md").write_text("also inside", encoding="utf-8")
    (vault / "SECRET.md").write_text("outside", encoding="utf-8")

    app = types.SimpleNamespace(
        kernel=types.SimpleNamespace(config=types.SimpleNamespace(notes_path=vault)),
    )
    vl = VaultLibrary.__new__(VaultLibrary)
    vl.app = app
    vl.fallback_folder = "lib"
    vl._has_vault_index = lambda: False          # force the fallback path
    return vl, vault, lib


def test_legitimate_lookups_still_resolve(library):
    vl, _vault, lib = library
    assert vl.find_file("real.md") == (lib / "real.md").resolve()
    # rglob branch: a file one level down is still found
    assert vl.find_file("deep.md") == (lib / "nested" / "deep.md").resolve()
    assert vl.find_file("no-such-note.md") is None


@pytest.mark.parametrize("escape", [
    "../SECRET.md",
    "..\\SECRET.md",        # Windows separator — Starlette's {param} allows it
    "../../SECRET.md",
])
def test_traversal_never_escapes_the_library_dir(library, escape):
    vl, vault, _lib = library
    assert (vault / "SECRET.md").exists()        # the target is really there
    assert vl.find_file(escape) is None
    # ...and via the public read path apps actually call.
    assert vl.detail(escape) is None


def test_contained_helper(tmp_path):
    base = tmp_path / "base"
    (base / "sub").mkdir(parents=True)
    (tmp_path / "out.md").write_text("x", encoding="utf-8")

    assert _contained(base, base / "sub") == (base / "sub").resolve()
    assert _contained(base, base / "../out.md") is None
    # base itself is "inside" base — is_relative_to is reflexive, which is the
    # right answer for a directory-containment question.
    assert _contained(base, base) == base.resolve()
