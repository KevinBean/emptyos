"""Unit tests for emptyos/basepath.py — the one base-relative resolver.

Five providers carried a verbatim copy of this before extraction, so the tests
pin the exact behaviour all five agreed on, including the two easy-to-lose
properties: an empty-string base means "no base", and nothing touches the
filesystem (the write provider resolves paths that do not exist yet).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from emptyos.basepath import resolve_under_base


def _s(p: Path) -> str:
    return str(p).replace("\\", "/")


# ── the three-branch contract ───────────────────────────────────────────────

def test_absolute_path_passes_through_untouched():
    out = resolve_under_base("C:/somewhere/deck.ppt", "C:/vault")
    assert _s(out) == "C:/somewhere/deck.ppt", "a base must never capture an absolute path"


def test_relative_path_resolves_under_the_base():
    assert _s(resolve_under_base("sub/deck.ppt", "C:/vault")) == "C:/vault/sub/deck.ppt"


def test_relative_path_without_a_base_stays_relative():
    """No base means the caller's cwd decides — do not invent one."""
    out = resolve_under_base("sub/deck.ppt", None)
    assert not out.is_absolute()
    assert _s(out) == "sub/deck.ppt"


# ── the two properties most easily lost in a rewrite ────────────────────────

@pytest.mark.parametrize("empty", ["", None])
def test_empty_base_is_the_same_as_no_base(empty):
    """Providers pass an unset config value straight through; "" must not join."""
    out = resolve_under_base("notes.md", empty)
    assert not out.is_absolute()
    assert _s(out) == "notes.md"


def test_does_not_touch_the_filesystem(tmp_path):
    """Pure joining — the write provider resolves paths that do not exist yet."""
    missing = tmp_path / "no" / "such" / "dir"
    out = resolve_under_base("new-file.md", missing)
    assert out == missing / "new-file.md", "must be an exact join, not a normalisation"
    assert not out.exists()


def test_never_makes_a_relative_result_absolute():
    """A relative base must stay relative — `Path.resolve()` here would silently
    bind the result to the process cwd, which differs between the daemon, the CLI
    and a test run. This is the one branch a mutation can actually observe: an
    absolute-base test cannot see `.resolve()` because the path is already absolute.
    """
    out = resolve_under_base("x.md", Path("relbase"))
    assert not out.is_absolute(), f"resolved against cwd: {out}"
    assert _s(out) == "relbase/x.md"


# ── shape tolerance ─────────────────────────────────────────────────────────

def test_base_accepts_str_or_path():
    a = resolve_under_base("x.md", "C:/vault")
    b = resolve_under_base("x.md", Path("C:/vault"))
    assert a == b


def test_path_accepts_str_or_path():
    a = resolve_under_base("sub/x.md", "C:/vault")
    b = resolve_under_base(Path("sub/x.md"), "C:/vault")
    assert a == b


def test_returns_a_path_not_a_string():
    assert isinstance(resolve_under_base("x.md", "C:/vault"), Path)


# ── parity with the five copies it replaced ─────────────────────────────────

def _original(path: str, base_path: Path | None) -> Path:
    """The body every provider carried, verbatim, before extraction."""
    p = Path(path)
    if p.is_absolute():
        return p
    if base_path:
        return base_path / p
    return p


@pytest.mark.parametrize("path", [
    "notes.md", "sub/deck.ppt", "a/b/c/d.txt", "C:/abs/file.md", "./rel.md",
])
@pytest.mark.parametrize("base", [
    None,
    Path("C:/vault"),
    Path("D:/other root"),
    # A RELATIVE base is the discriminating case: with an absolute base, adding
    # `.resolve()` to the join is invisible (the path is already absolute), so a
    # suite without this row cannot catch that mutation. Found by mutation
    # testing -- the first version of this file passed all three mutants.
    Path("relbase"),
    Path("rel/nested"),
])
def test_matches_the_implementation_it_replaced(path, base):
    assert resolve_under_base(path, base) == _original(path, base)
