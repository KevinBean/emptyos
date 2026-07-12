#!/usr/bin/env python3
"""One-shot migration: flat apps/<id>/ -> apps/<track>/<group>/<id>/.

Reads release.toml tiers and moves each tracked flat app dir into its track
folder by *manifest id* (precedence: public core>standard>labs, then the
extension groups, else extension/others). Dir names are preserved (e.g.
cable_network/ stays cable_network/, whose id is cable-network).

Usage:
    python scripts/migrate_apps_tree.py            # dry-run (prints plan)
    python scripts/migrate_apps_tree.py --execute  # git mv for real
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS = ROOT / "apps"

# (tier name, destination folder) in precedence order — first match wins, so a
# public app shared by a composite tier (plekto/portfolio) stays public.
PRECEDENCE = [
    ("core", "public/core"),
    ("standard", "public/standard"),
    ("labs", "public/labs"),
    ("engineering", "extension/engineering"),
    ("english-learning", "extension/english-learning"),
    ("portfolio", "extension/portfolio"),
    ("plekto", "extension/plekto"),
    ("dev", "extension/dev"),
]
DEFAULT_DEST = "extension/others"
SKIP_TOP = {"personal", "public", "extension", "_catalog", "_example", "_retired",
            "installed", "private"}
# Empty staging + bucket folders to scaffold.
SCAFFOLD = ["public/labs", "extension/labs", "extension/others", "personal/labs"]


def _manifest_id(d: Path) -> str | None:
    f = d / "manifest.toml"
    if not f.is_file():
        return None
    try:
        return tomllib.load(open(f, "rb")).get("app", {}).get("id") or d.name
    except Exception:
        return d.name


def _id_to_dest() -> dict[str, str]:
    tiers = tomllib.load(open(ROOT / "release.toml", "rb")).get("tiers", {}) or {}
    out: dict[str, str] = {}
    for tier, dest in PRECEDENCE:
        for aid in tiers.get(tier, {}).get("apps", []) or []:
            out.setdefault(aid, dest)
    return out


def _git(args: list[str]) -> None:
    subprocess.run(["git", "-C", str(ROOT), *args], check=True)


def _move(src: Path, dst: Path) -> str:
    """git mv if tracked; fall back to a plain filesystem move for untracked /
    gitignored dirs (e.g. apps/test-app). Returns 'git' or 'fs'."""
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(["mv", str(src.relative_to(ROOT)), str(dst.relative_to(ROOT))])
        return "git"
    except subprocess.CalledProcessError:
        shutil.move(str(src), str(dst))
        return "fs"


def main() -> int:
    execute = "--execute" in sys.argv
    id2dest = _id_to_dest()

    moves: list[tuple[Path, Path, str, str]] = []
    skipped: list[str] = []
    for child in sorted(APPS.iterdir()):
        if not child.is_dir() or child.name in SKIP_TOP:
            continue
        aid = _manifest_id(child)
        if aid is None:
            skipped.append(f"{child.name}/ (no manifest.toml)")
            continue
        dest_group = id2dest.get(aid, DEFAULT_DEST)
        dest = APPS / dest_group / child.name
        moves.append((child, dest, aid, dest_group))

    # Plan summary, grouped by destination.
    by_dest: dict[str, list[str]] = {}
    for _src, _dst, aid, grp in moves:
        by_dest.setdefault(grp, []).append(aid)
    print(f"{'EXECUTING' if execute else 'DRY-RUN'} — {len(moves)} app moves\n")
    for grp in sorted(by_dest):
        ids = sorted(by_dest[grp])
        print(f"  {grp}/  ({len(ids)}): {', '.join(ids)}")
    if skipped:
        print(f"\n  skipped (not apps): {', '.join(skipped)}")
    others = by_dest.get(DEFAULT_DEST, [])
    print(f"\n  -> extension/others bucket: {sorted(others) or '(none)'}")

    if not execute:
        print("\n(dry-run; pass --execute to git mv)")
        return 0

    # Execute the moves (idempotent — only dirs still at the flat location).
    n_git = n_fs = 0
    for src, dst, _aid, _grp in moves:
        how = _move(src, dst)
        n_git += how == "git"
        n_fs += how == "fs"
    print(f"\nmoved {len(moves)} apps (git mv: {n_git}, fs move: {n_fs}).")

    # Scaffold empty staging/bucket folders with .gitkeep.
    for rel in SCAFFOLD:
        d = APPS / rel
        d.mkdir(parents=True, exist_ok=True)
        if not any(p for p in d.iterdir() if p.name != ".gitkeep"):
            keep = d / ".gitkeep"
            if not keep.exists():
                keep.write_text("", encoding="utf-8")
    print(f"scaffolded: {', '.join(SCAFFOLD)}")

    # Clean up apps/private/ iff it holds no real apps (manifests).
    priv = APPS / "private"
    if priv.is_dir():
        has_manifest = any(priv.rglob("manifest.toml"))
        if has_manifest:
            print("WARNING: apps/private/ still contains manifest.toml — left in place, inspect manually")
        else:
            import shutil
            shutil.rmtree(priv, ignore_errors=True)
            print("removed empty apps/private/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
