"""Canonical vault-root resolution for `scripts/`.

One resolver so a script cannot disagree with the daemon about where the vault
is. Reads `[notes] path` from `emptyos.toml` and honours an `EOS_VAULT` override
(which is what lets a script run against a sandbox or a fixture vault without
editing machine config).

Two entry points because the two failure modes are genuinely different:
`vault_root()` returns None when there is no vault to find, for callers that
degrade; `require_vault_root()` exits with an actionable message, for callers
that cannot proceed without one.

Neither ever returns `Path("")`. That is the shape worth naming: `Path("")` is
`Path(".")`, so a script that resolves an absent config to it silently walks the
repo root instead of the vault — scanning, and potentially rewriting, the wrong
tree entirely.

~35 scripts hand-roll this today. They are NOT migrated wholesale: they differ in
what they do when config is absent, and a blind sweep would convert those quiet
differences into quiet breakage. This is the destination that new and
already-touched scripts import from; the sweep is a deferred row of its own.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def vault_root() -> Path | None:
    """The configured vault root, or None when there isn't one.

    `EOS_VAULT` wins over config so a run can be pointed elsewhere without
    touching `emptyos.toml` (which is gitignored per-machine config).
    """
    env = (os.environ.get("EOS_VAULT") or "").strip()
    if env:
        return Path(env)
    toml_path = REPO_ROOT / "emptyos.toml"
    if not toml_path.exists():
        return None
    try:
        with toml_path.open("rb") as f:
            configured = (tomllib.load(f).get("notes") or {}).get("path") or ""
    except (OSError, tomllib.TOMLDecodeError):
        return None
    configured = str(configured).strip()
    return Path(configured) if configured else None


def require_vault_root() -> Path:
    """`vault_root()` or exit 2 saying how to fix it."""
    root = vault_root()
    if root is None:
        print(
            "No vault configured. Set EOS_VAULT=<path>, or set [notes] path in "
            f"{REPO_ROOT / 'emptyos.toml'}.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return root
