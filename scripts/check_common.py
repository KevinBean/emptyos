"""Shared plumbing for the static `check-*.py` scanners in this directory.

Deliberately tiny. `scripts/preflight.py` runs every check as a black-box
subprocess (``python scripts/<check>.py``), so ``sys.path[0]`` is this
directory and a plain ``from check_common import load_by_path`` resolves with
no packaging — while each check stays independently runnable by hand.

Named without a leading underscore on purpose: ``.gitignore`` excludes
``scripts/_*.py`` (scratch scripts), so an ``_common.py`` would be silently
untracked and every scanner would break in a fresh clone.

Anything heavier belongs in ``emptyos/sdk/``. Importing that package is exactly
what these scanners must not do: ``emptyos/sdk/__init__.py`` pulls the daemon's
runtime deps, and a scanner has to stay pure file I/O so it is safe to run while
the daemons are up.

Mirrors ``emptyos/cli/_common.py`` (shared envelope for the ``eos`` commands).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent


def load_by_path(name: str, rel: str) -> ModuleType:
    """Import one module *file* under ``REPO`` without importing its package.

    Used for leaf modules the scanners genuinely need (``emptyos/sdk/app_layout.py``,
    ``emptyos/sdk/release_tiers.py``). ``name`` must be unique per caller — it
    lands in ``sys.modules``.
    """
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    if spec is None or spec.loader is None:  # pragma: no cover — bad path is a bug
        raise ImportError(f"cannot load {rel} as {name!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
