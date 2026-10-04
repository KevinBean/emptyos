"""Read a value out of a module's source without importing the module.

For a test that pins one module's literal to another's — a control-plane copy
of the kernel's ESSENTIAL_APPS, a review-all SOURCES list, the KB's KINDS —
importing the owning module is the wrong tool: it may boot the kernel
(daemon-handling rule 4), pull in the whole ``emptyos.sdk`` package or a
dependency the test process does not have, or run module-level code. AST is
enough, and it reads the literal the author wrote rather than whatever a later
import might have rebound.

Top-level and stdlib-only (``.claude/rules/top-level-modules.md``) so a
daemon-free unit test can use it without paying the SDK import it exists to
avoid. Three hand-rolled copies preceded it (2026-10-02).
"""

from __future__ import annotations

import ast
from pathlib import Path

_WRAPPERS = {"frozenset": frozenset, "set": set, "tuple": tuple, "list": list}


def module_constant(path: Path, name: str):
    """The literal bound to module-level ``name`` in the source at ``path``.

    Accepts ``NAME = <literal>``, ``NAME: T = <literal>`` and the one-argument
    wrappers ``NAME = frozenset(<literal>)`` / ``set`` / ``tuple`` / ``list``,
    returning the wrapped type. Only module-level statements count — a
    same-named local inside a function is not the constant. Raises
    ``LookupError`` unless ``name`` is bound exactly once there, and
    ``ValueError`` (from ``ast.literal_eval``) when the value is not a literal.
    """
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            bound.append(value)
    if len(bound) != 1:
        raise LookupError(f"{name} is bound {len(bound)} times at module level in {path}")
    value = bound[0]
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _WRAPPERS
        and len(value.args) == 1
        and not value.keywords
    ):
        return _WRAPPERS[value.func.id](ast.literal_eval(value.args[0]))
    return ast.literal_eval(value)
