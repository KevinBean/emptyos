"""Shared vault/KB root resolution for the kb_* audit scripts.

Co-located scripts/ sibling (like md_frontmatter.py) — imported by
kb_link_audit.py and kb_claim_audit.py so their --root semantics can't drift.
"""
from __future__ import annotations
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def vault_root() -> Path:
    cfg = tomllib.load(open(REPO / "emptyos.toml", "rb"))
    return Path(cfg.get("notes", {}).get("path", ""))


def resolve_kb_root(arg: str | None) -> Path:
    """Default: the EmptyOS KB. Accepts an absolute path or one relative to the vault."""
    if not arg:
        return vault_root() / "30_Resources" / "EmptyOS" / "kb"
    p = Path(arg)
    return p if p.is_absolute() else vault_root() / p
