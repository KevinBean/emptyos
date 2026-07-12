"""Wave-3 migration: merge the flipbook folder into kb/notes/.

After the Wave-3 node consolidation, all kb concepts (including those
with flipbook visuals) live at ``{vault}/30_Resources/EmptyOS/kb/notes/``
and carry ``tags: [kb]`` only (no separate ``flipbook`` tag). This
script moves any files still at the Wave-2 transitional location
``{vault}/30_Resources/EmptyOS/kb/flipbook/`` into ``kb/notes/`` and
strips the ``flipbook`` tag from their frontmatter.

Rewrites per file:
  - ``tags:`` — drop ``flipbook`` if present; ``kb`` stays
  - Move the .md file (preserving any parent-slug subfolder structure)

Asset + symbol handling:
  - ``_assets/`` subtree (PNG renders, SVG copies) moves into
    ``kb/notes/_assets/``. The flipbook-asset route accepts a fallback
    legacy path lookup so unmigrated PNGs still load mid-migration.
  - ``_symbols/`` subtree merges into ``kb/notes/_symbols/`` with no
    overwrite — the user's hand-edited symbols in the target folder
    are never clobbered.

Usage::

    python scripts/migrate_flipbook_folder.py --dry-run    # preview
    python scripts/migrate_flipbook_folder.py              # apply

Idempotent — re-running after success is a no-op (source folder gone or
empty). Safe to run with the daemon running; VaultIndex picks up the
new paths on its next scan.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_FOLDER = "30_Resources/EmptyOS/kb/flipbook"
NEW_FOLDER = "30_Resources/EmptyOS/kb/notes"


def vault_path() -> Path:
    cfg_path = REPO_ROOT / "emptyos.toml"
    if not cfg_path.exists():
        sys.exit(
            f"emptyos.toml not found at {cfg_path}. "
            "Run this from the repo root with a configured vault."
        )
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    notes_path = (cfg.get("notes") or {}).get("path", "")
    if not notes_path:
        sys.exit("emptyos.toml has no [notes] path — nothing to migrate.")
    vp = Path(notes_path)
    if not vp.exists():
        sys.exit(f"vault path does not exist: {vp}")
    return vp


def rewrite_frontmatter(text: str) -> tuple[str, list[str]]:
    """Drop the ``flipbook`` tag from frontmatter. Returns (new_text, changes)."""
    changes: list[str] = []
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return text, ["no frontmatter — skipped"]
    fm_block = m.group(1)
    rest = text[m.end():]
    lines = fm_block.split("\n")

    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Inline tags: tags: [a, b, c]
        if stripped.startswith("tags:") and ("[" in stripped):
            inline = stripped.split("tags:", 1)[1].strip().strip("[]")
            old_tags = [
                t.strip().strip('"').strip("'")
                for t in inline.split(",")
                if t.strip()
            ]
            new_tags = [t for t in old_tags if t != "flipbook"]
            if new_tags != old_tags:
                changes.append(f"tags {old_tags} -> {new_tags}")
            out_lines.append("tags:")
            for t in new_tags:
                out_lines.append(f"  - {t}")
            i += 1
            continue

        # Block-style tags
        if stripped == "tags:":
            old_tags: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.startswith("  - "):
                    tag_val = next_line.split("-", 1)[1].strip().strip('"').strip("'")
                    if tag_val:
                        old_tags.append(tag_val)
                    j += 1
                else:
                    break
            new_tags = [t for t in old_tags if t != "flipbook"]
            if new_tags != old_tags:
                changes.append(f"tags {old_tags} -> {new_tags}")
            out_lines.append("tags:")
            for t in new_tags:
                out_lines.append(f"  - {t}")
            i = j
            continue

        out_lines.append(line)
        i += 1

    return f"---\n" + "\n".join(out_lines) + "\n---\n" + rest, changes


def migrate(vault: Path, dry_run: bool) -> int:
    src = vault / LEGACY_FOLDER
    dst = vault / NEW_FOLDER
    if not src.exists():
        print(f"  legacy folder absent: {src}  (nothing to do)")
        return 0
    if not any(src.iterdir()):
        print(f"  legacy folder empty: {src}  (nothing to do)")
        return 0

    print(f"  source: {src}")
    print(f"  target: {dst}")
    print()

    moved = 0
    # Only top-level + parent-slug-nested .md files; skip _assets/_symbols
    # (handled separately so we get sensible subtree logging).
    md_files = []
    for f in sorted(src.rglob("*.md")):
        rel = f.relative_to(src)
        first = rel.parts[0] if rel.parts else ""
        if first in ("_assets", "_symbols"):
            continue
        md_files.append(f)

    print(f"  found {len(md_files)} markdown note(s) to migrate:")
    for f in md_files:
        rel = f.relative_to(src)
        target = dst / rel
        text = f.read_text(encoding="utf-8")
        new_text, changes = rewrite_frontmatter(text)
        marker = "[dry]" if dry_run else "[mv ]"
        print(f"    {marker} {rel}")
        if changes:
            for c in changes:
                print(f"           - {c}")
        # Conflict: target already exists (likely a stale duplicate
        # from a previous partial migration). Skip — never overwrite.
        if target.exists():
            print(f"           ! target exists — skipping to avoid overwrite")
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            f.unlink()
        moved += 1

    # Assets — move the whole subtree
    src_assets = src / "_assets"
    dst_assets = dst / "_assets"
    if src_assets.exists():
        print()
        print(f"  _assets/ — moving subtree into {dst_assets}")
        if not dry_run:
            dst_assets.mkdir(parents=True, exist_ok=True)
            for f in src_assets.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src_assets)
                    target = dst_assets / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        f.rename(target)
                    else:
                        f.unlink()  # destination already has a copy

    # Symbols — merge, never overwrite
    src_symbols = src / "_symbols"
    dst_symbols = dst / "_symbols"
    if src_symbols.exists():
        print()
        print(f"  _symbols/ — merging into {dst_symbols} (no overwrite)")
        if not dry_run:
            dst_symbols.mkdir(parents=True, exist_ok=True)
            for f in src_symbols.glob("*.svg"):
                target = dst_symbols / f.name
                if target.exists():
                    print(f"    [skip] {f.name} (already in target)")
                else:
                    f.rename(target)
                    print(f"    [mv  ] {f.name}")
            # Move the .seeded sentinel too so the new location doesn't
            # re-seed demos that the user may have removed.
            sentinel = src_symbols / ".seeded"
            if sentinel.exists():
                target = dst_symbols / ".seeded"
                if not target.exists():
                    sentinel.rename(target)

    print()
    if dry_run:
        print(f"  DRY-RUN: would migrate {moved} note(s). Re-run without --dry-run to apply.")
    else:
        print(f"  done: migrated {moved} note(s).")
        # Clean up empty subdirectories left behind
        try:
            for d in sorted(src.rglob("*"), key=lambda p: -len(str(p))):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            if not any(src.iterdir()):
                src.rmdir()
                print(f"  removed empty legacy folder: {src}")
        except OSError:
            pass

    return moved


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Wave-3 migration: kb/flipbook → kb/notes",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without writing anything",
    )
    args = ap.parse_args()

    vault = vault_path()
    print(f"vault: {vault}")
    print()
    print(
        "kb/flipbook → kb/notes migration "
        f"{'(dry-run)' if args.dry_run else ''}"
    )
    print("=" * 60)
    n = migrate(vault, args.dry_run)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
