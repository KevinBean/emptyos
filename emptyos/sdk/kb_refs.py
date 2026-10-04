"""Shared parsing for KB ``implemented_in:`` references.

A KB note's ``implemented_in:`` frontmatter links a formula/spec to the code
that realises it. Because vault frontmatter is flat-only, nested entries like

    implemented_in:
      - path: engines/thermal/fem/solver.py
      - method: cables.ampacity.fem

serialise as literal strings ``"path: ..."`` / ``"method: ..."``. This module
owns the one canonical parse so every consumer agrees on the grammar.

Consumers: ``apps/public/standard/kb`` (health + detail existence checks) and
``apps/extension/dev/kb-butler`` (broken-path repair resolver). Pure — no kernel,
no I/O — so it imports cleanly into an app module and into a unit test.
"""

from __future__ import annotations


def parse_impl_ref(ref) -> tuple[str, str, str]:
    """Split an ``implemented_in:`` entry into ``(kind, code_path, symbol)``.

    ``path:`` / bare entries return ``kind="path"`` with a repo-relative path
    (callers verify existence). ``method:`` entries are pointer-only
    (``kind="method"``) — there's no file to check, so callers skip the
    existence test. The symbol separator may be the canonical ``::`` or one of
    the dash forms used in free-text refs (``foo.py — sym``).
    """
    sref = str(ref).strip()
    low = sref.lower()
    body = sref
    if low.startswith("path:"):
        body = sref.split(":", 1)[1].strip()
    elif low.startswith("method:"):
        return "method", sref.split(":", 1)[1].strip(), ""
    code_path, sym = body, ""
    for sep in ("::", " — ", " – ", " - "):
        if sep in body:
            code_path, _, sym = body.partition(sep)
            break
    return "path", code_path.strip(), sym.strip()
