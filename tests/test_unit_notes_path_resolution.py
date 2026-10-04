"""A relative `notes.path` must never reach a vault-rooted resolver.

Seven CI-only failures sat parked and undiagnosed for a week behind exactly one
condition: CI configures `notes.path = "./ci-vault"` while homepc and every
sandbox member use an absolute path, so nothing reproduced anywhere. Measured
2026-09-13 from the CI daemon log and run 34621715409.

The chain, for `note.append()`:

    _notes_dir()  -> "./ci-vault"              (RAW config.get — the bug)
    find()        -> search(root="./ci-vault")
    grep returns  -> "./ci-vault/x.md"         (cwd-relative, vault prefix in it)
    read(path)    -> resolve_under_base(rel, ABSOLUTE base)
                  -> "<abs vault>/ci-vault/x.md"   -> FileNotFoundError -> HTTP 500

`Config.notes_path` already existed to prevent this and says so in its own
docstring ("Always absolute so callers that build paths off it don't accidentally
double-prefix"). The defence was simply bypassed: eight call sites read the raw
key instead. The AST test below is the part that stops a ninth appearing —
the per-app assertions only cover the apps that exist today.

Daemon-free on purpose: these are the conditions CI itself runs under, so a
daemon-backed test could not gate them (`.claude/rules/testing.md`).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))

from emptyos.basepath import resolve_under_base  # noqa: E402
from emptyos.kernel.config import Config  # noqa: E402


def _raw_get_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `<x>.get("notes.path", ...)` call in a parsed module."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "get") or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "notes.path":
            out.append(node)
    return out


def _raw_notes_path_reads() -> list[str]:
    """Raw reads of the key outside the ONE place allowed to make one.

    Matched on the AST, never on the source text: a comment explaining the bug
    quotes `get("notes.path")` verbatim, and a substring check flags that as an
    offender — the first version of this test did exactly that and reported the
    fix as the defect.
    """
    offenders: list[str] = []
    for base in ("apps", "plugins", "emptyos", "services", "scripts"):
        d = ROOT / base
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in py.parts or "_retired" in py.parts:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            # `Config.notes_path` is the property every other caller must use,
            # so it is the one function entitled to read the raw key. Keyed on
            # the enclosing function name, not the file, so another raw read
            # elsewhere in config.py is still reported.
            allowed = {
                id(c)
                for fn in ast.walk(tree)
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.name == "notes_path"
                for c in _raw_get_calls(fn)
            }
            for call in _raw_get_calls(tree):
                if id(call) not in allowed:
                    offenders.append(f"{py.relative_to(ROOT)}:{call.lineno}")
    return offenders


def _config(tmp_path: Path, notes_value: str) -> Config:
    cfg = tmp_path / "emptyos.toml"
    cfg.write_text(
        '[os]\nname = "t"\n\n[notes]\npath = "%s"\n' % notes_value,
        encoding="utf-8",
    )
    return Config(str(cfg))


class TestNotesPathIsAlwaysAbsolute:
    def test_a_relative_value_resolves_absolute(self, tmp_path):
        c = _config(tmp_path, "./ci-vault")
        assert c.notes_path is not None
        assert c.notes_path.is_absolute(), (
            f"a relative notes.path must resolve; got {c.notes_path}"
        )

    def test_an_absolute_value_is_left_alone(self, tmp_path):
        target = (tmp_path / "vault").resolve()
        c = _config(tmp_path, str(target).replace("\\", "/"))
        assert c.notes_path == target

    def test_absent_notes_path_is_none_not_cwd(self, tmp_path):
        cfg = tmp_path / "emptyos.toml"
        cfg.write_text('[os]\nname = "t"\n', encoding="utf-8")
        assert Config(str(cfg)).notes_path is None


class TestTheDoublePrefixItself:
    """The mechanism, pinned directly — not inferred from a passing app."""

    def test_a_relative_base_doubles_a_vault_prefixed_path(self):
        # This is the CI shape, and it is why the defence must stay upstream:
        # given a relative base there is nothing `resolve_under_base` can do.
        doubled = resolve_under_base("./ci-vault/00_Inbox/n.md", "./ci-vault")
        assert str(doubled).count("ci-vault") == 2, (
            "the regression this whole fix exists for stopped reproducing — "
            "if resolve_under_base changed, re-derive the guarantee"
        )

    def test_an_absolute_base_with_an_absolute_path_never_doubles(self, tmp_path):
        vault = (tmp_path / "vault").resolve()
        got = resolve_under_base(str(vault / "00_Inbox" / "n.md"), str(vault))
        assert got == vault / "00_Inbox" / "n.md"
        assert str(got).count("vault") == 1


class TestNoAppReadsTheRawKey:
    """The guard that keeps a ninth bypasser from appearing.

    `Config.notes_path` is the only correct reader. A raw
    `config.get("notes.path")` re-opens the double-prefix, and it is invisible on
    any machine configured with an absolute path — i.e. every developer machine.
    """

    def test_no_raw_notes_path_reads_remain(self):
        offenders = _raw_notes_path_reads()
        assert not offenders, (
            "read the resolved `config.notes_path` property, never the raw key — "
            "a relative value double-prefixes through read/write. Offenders: "
            + ", ".join(sorted(offenders))
        )


@pytest.mark.parametrize(
    "rel, attr",
    [
        ("apps/public/core/note/app.py", "_notes_dir"),
        ("apps/public/core/link/app.py", "_notes_dir"),
        ("apps/extension/dev/rag-eval/app.py", "_vault_root"),  # release-filter: optional
        ("apps/public/core/search/app.py", "_vault_path"),
    ],
)
def test_the_known_vault_root_helpers_use_the_property(rel, attr):
    """One row per helper, so trimming the set produces one red each."""
    from helpers import public_snapshot

    if not (ROOT / rel).exists() and public_snapshot():
        pytest.skip(f"{rel} absent (public snapshot)")
    src = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == attr),
        None,
    )
    assert fn is not None, f"{rel} no longer defines {attr}()"
    assert not _raw_get_calls(fn), f"{rel}:{attr} still reads the raw key"
    uses_property = any(
        isinstance(n, ast.Attribute) and n.attr == "notes_path" for n in ast.walk(fn)
    )
    assert uses_property, f"{rel}:{attr} does not use config.notes_path"
