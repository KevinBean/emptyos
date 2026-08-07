"""Shared vault/KB root resolution for the kb_* audit scripts.

Co-located scripts/ sibling (like md_frontmatter.py) — imported by
kb_link_audit.py and kb_claim_audit.py so their --root semantics can't drift.
"""
from __future__ import annotations
from pathlib import Path

from vault_paths import require_vault_root

REPO = Path(__file__).resolve().parents[1]


def vault_root() -> Path:
    """Delegates to the canonical resolver in vault_paths.

    Was a local tomllib read returning `Path(cfg[...].get("path", ""))` — and
    `Path("")` is `Path(".")`, so an absent or unreadable config silently pointed
    the KB audits at the repo root instead of failing. Now it exits with an
    actionable message, and picks up the `EOS_VAULT` override for free.
    """
    return require_vault_root()


def resolve_kb_root(arg: str | None) -> Path:
    """Default: the EmptyOS KB. Accepts an absolute path or one relative to the vault."""
    if not arg:
        return vault_root() / "30_Resources" / "EmptyOS" / "kb"
    p = Path(arg)
    return p if p.is_absolute() else vault_root() / p
