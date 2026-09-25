"""viz/figures.py + kb/figures.py — every `self.X` they use must be bound.

Both are bound-helper modules (`.claude/rules/multi-module-apps.md`): functions
are defined at module level and re-bound in the app class body. A helper added
here and called via `self` but never given its `<name> = _figures.<name>` line
raises AttributeError only when that branch runs — and `export_figure`'s embed
call sits inside `except Exception`, so a missing binding there would degrade
*silently* to "asset written, never embedded" rather than failing loudly.

Static (ast), so it needs no kernel and no daemon. A behavioural test cannot
catch this class: passing a module-level function a fake app never touches the
real class, so a missing binding stays invisible.

Mirrors tests/test_unit_publish_media_bindings.py, which exists because exactly
this shipped once in publish.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import load_app_module  # noqa: E402

# (helper module, spine module, names the spine must expose)
CASES = [
    pytest.param(
        "apps/public/standard/viz/figures.py",
        "apps/public/standard/viz/app.py",
        id="viz",
    ),
    pytest.param(
        "apps/public/standard/kb/figures.py",
        "apps/public/standard/kb/app.py",
        id="kb",
    ),
]


def _module_level_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("__")
    }


def _self_attrs_used(path: Path) -> set[str]:
    """Attribute names read off `self` anywhere in the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            out.add(node.attr)
    return out


def _class_bound_names(path: Path) -> set[str]:
    """Names assigned in a class body, e.g. `export_figure = _figures.export_figure`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        out.add(t.id)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(stmt.name)
    return out


@pytest.mark.parametrize("helper_rel,spine_rel", CASES)
def test_every_helper_function_is_bound_on_the_app_class(helper_rel, spine_rel):
    helper, spine = ROOT / helper_rel, ROOT / spine_rel
    defined = _module_level_defs(helper)
    bound = _class_bound_names(spine)
    missing = sorted(defined - bound)
    assert not missing, (
        f"{helper_rel} defines {missing} but {spine_rel} never binds them — "
        f"add a `<name> = _figures.<name>` line in the class body."
    )


@pytest.mark.parametrize("helper_rel,spine_rel", CASES)
def test_every_self_attr_the_helper_calls_exists_on_the_spine(helper_rel, spine_rel):
    """Catches the reverse slip: calling `self._helper()` that nothing provides."""
    helper, spine = ROOT / helper_rel, ROOT / spine_rel
    used = _self_attrs_used(helper)
    available = _class_bound_names(spine) | _module_level_defs(helper)

    # Provided by BaseApp, not by the app class — not this test's business.
    from_base_app = {
        "app_config", "setting_or_config", "call_app", "emit", "log",
        "vault_root", "vault_get_properties", "vault_update", "vault_write_at",
        "note_lock", "vault_set_section", "manifest", "kernel",
    }
    missing = sorted(used - available - from_base_app)
    assert not missing, (
        f"{helper_rel} calls self.{{{', '.join(missing)}}} which {spine_rel} does not "
        f"define or bind (and which is not a known BaseApp member)."
    )


# ── kb figure_asset_path: the gate that prevents orphan assets ───────────

class _StubKB:
    """Enough of KBApp for the pure path helpers."""

    def __init__(self, vault_root, existing=()):
        self.vault_root = vault_root
        self._existing = set(existing)

    def _notes_dir(self):
        return "30_Resources/EmptyOS/kb/notes"

    def _note_path(self, slug):
        return f"{self._notes_dir()}/{slug}.md"


def test_kb_asset_path_requires_the_note_to_exist(tmp_path):
    """A typo'd slug must not yield a path.

    Otherwise the producer writes an orphan SVG+PNG into `_assets/` for a note
    nobody will open, and stamps `used_in: kb/<typo>` — provenance asserting a
    relationship that does not exist. The embed failing afterwards is too late;
    both files are already on disk.
    """
    kbf = load_app_module("kb", "figures")
    notes = tmp_path / "30_Resources/EmptyOS/kb/notes"
    notes.mkdir(parents=True)
    (notes / "cse-thrust.md").write_text("# x", encoding="utf-8")
    app = _StubKB(tmp_path)

    assert kbf.figure_asset_path(app, "cse-thrust") == (
        "30_Resources/EmptyOS/kb/notes/_assets/cse-thrust.svg"
    )
    assert kbf.figure_asset_path(app, "no-such-note") == ""
    assert kbf.figure_asset_path(app, "") == ""


def test_kb_asset_path_refuses_a_traversal_or_device_slug(tmp_path):
    kbf = load_app_module("kb", "figures")
    (tmp_path / "30_Resources/EmptyOS/kb/notes").mkdir(parents=True)
    app = _StubKB(tmp_path)
    for bad in ("../../etc/passwd", "..", "nul", "con"):
        assert kbf.figure_asset_path(app, bad) == "", bad


# ── shared cross-app note writers must use the kernel-wide note lock ─────

def test_embed_viz_into_note_uses_note_lock_not_write_lock():
    """`embed_viz_into_note` is BaseApp's shared writer — kb, note and journal
    all call it — so its lock must exclude ACROSS app instances.

    `write_lock` is per-app-instance (`self._write_locks`): two apps embedding
    into the same note take two different lock objects and do not exclude each
    other, which is the exact read-modify-write race the lock is there to
    prevent. `note_lock` is the kernel-wide registry keyed by normalized
    vault-relative path. It also normalizes the key, which matters on Windows
    where a backslashed relative path and an absolute path to the same note
    would otherwise hash to different `write_lock` entries.

    Regression pin: this shipped as `write_lock` and was found by reading, not
    by running — the path is dark, so nothing exercised it.
    """
    src = (ROOT / "emptyos/sdk/base_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "embed_viz_into_note"
    )
    locks = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"write_lock", "note_lock"}
    }
    assert locks == {"note_lock"}, (
        f"embed_viz_into_note serialises with {locks or 'no lock'}; a shared "
        f"cross-app note writer must use self.note_lock(note_rel)."
    )


def test_kb_attach_figure_uses_note_lock():
    """Same contract for the figure-embed sibling — viz writes the asset, kb
    rewrites the note, and both touch the same file."""
    src = (ROOT / "apps/public/standard/kb/figures.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "attach_figure"
    )
    locks = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"write_lock", "note_lock"}
    }
    assert locks == {"note_lock"}, f"attach_figure serialises with {locks or 'no lock'}"
