"""publish/media.py — every helper it calls via `self` must be bound on the app class.

`media.py` is a bound-helper module (`.claude/rules/multi-module-apps.md`): its
functions are defined at module level and re-bound in the `PublishApp` class
body. A helper that is added to `media.py` and called via `self` but never given
its `<name> = _media.<name>` line raises `AttributeError` at runtime — and
several of media.py's call sites sit inside `except Exception`, so two of the
three cover write paths would fail *silently* while the third 500s.

That is not hypothetical: it is exactly how `_set_post_frontmatter_field_locked`
and `_rewrite_post_locked` once shipped unbound, breaking every cover path.

Static (ast) rather than an import, so it needs no kernel and no daemon. A
behavioural test cannot catch this class — passing a module-level function a
fake app never touches the real class, so a missing binding stays invisible.

Harvested from an unmerged worktree (recover/wt-831b-20260728); the refactor it
originally accompanied was left behind, this pin was not.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from helpers import app_path  # noqa: E402

PUBLISH_DIR = app_path("publish")


def test_every_media_helper_called_on_self_is_bound_on_the_app_class():
    media_ast = ast.parse((PUBLISH_DIR / "media.py").read_text(encoding="utf-8"))

    defined = {
        node.name
        for node in media_ast.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    called_on_self = {
        node.attr
        for node in ast.walk(media_ast)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    needs_binding = defined & called_on_self
    assert needs_binding, "expected media.py to call at least one of its own helpers via self"

    app_ast = ast.parse((PUBLISH_DIR / "app.py").read_text(encoding="utf-8"))
    bound: set[str] = set()
    for cls in (n for n in ast.walk(app_ast) if isinstance(n, ast.ClassDef)):
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign):
                bound.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(stmt.name)

    missing = sorted(needs_binding - bound)
    assert not missing, (
        f"media.py calls these via self but app.py never binds them: {missing}. "
        "Add a `<name> = _media.<name>` line to the class body — otherwise every "
        "call raises AttributeError at runtime, silently wherever the call site "
        "is wrapped in `except Exception`."
    )
