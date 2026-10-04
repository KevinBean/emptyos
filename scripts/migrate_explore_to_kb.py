"""One-shot migration: move explore notes into kb's flipbook folder.

After the explore→kb merge in apps/kb/, flipbook notes live at
``{vault}/30_Resources/EmptyOS/kb/flipbook/`` with frontmatter
``tags: [kb, flipbook]``. This script walks the legacy
``{vault}/30_Resources/Explore/`` folder and:

  1. Rewrites each .md file's frontmatter:
       - ``tags`` → ensure ``kb`` and ``flipbook`` are both present
         (drop the bare ``explore`` tag if it was the only entry)
       - ``domain: explore`` → ``domain: flipbook``
  2. Moves the file into the new folder, preserving the parent-slug
     subfolder structure (``Explore/guitar/nut.md`` → ``kb/flipbook/guitar/nut.md``)
  3. Moves the ``_assets/`` subtree (PNG renders, SVG copies)
  4. Merges the ``_symbols/`` subtree (per-file, never overwrites an existing
     symbol in the target — keeps the user's hand-edits)

Usage::

    python scripts/migrate_explore_to_kb.py --dry-run    # preview, no writes
    python scripts/migrate_explore_to_kb.py              # apply

Idempotent: re-running after success is a no-op (source folder is empty
or absent). Safe to run with the daemon running — VaultIndex picks up the
new files on its next scan; the kb app reads from the new location only
(legacy folder remains as read-only fallback via
``flipbook_io.py::_load_legacy``).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_FOLDER = "30_Resources/Explore"
NEW_FOLDER = "30_Resources/EmptyOS/kb/flipbook"


def vault_path() -> Path:
    """Read the vault path from emptyos.toml at the repo root."""
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
    """Rewrite a note's frontmatter to use new tags + domain.

    Returns (new_text, changes_summary).
    """
    changes: list[str] = []
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return text, ["no frontmatter — skipped"]

    fm_block = m.group(1)
    rest = text[m.end():]
    lines = fm_block.split("\n")

    # Parse tag block — handle both inline (tags: [a, b]) and block style
    out_lines: list[str] = []
    i = 0
    saw_tags = False
    saw_domain = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Inline tags
        if stripped.startswith("tags:") and ("[" in stripped):
            saw_tags = True
            inline = stripped.split("tags:", 1)[1].strip()
            inline = inline.strip("[]")
            old_tags = [t.strip().strip('"').strip("'") for t in inline.split(",") if t.strip()]
            new_tags = _normalize_tags(old_tags)
            if new_tags != old_tags:
                changes.append(f"tags {old_tags} -> {new_tags}")
            out_lines.append("tags:")
            for t in new_tags:
                out_lines.append(f"  - {t}")
            i += 1
            continue

        # Block-style tags
        if stripped == "tags:":
            saw_tags = True
            old_tags: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.startswith("  - ") or next_line.startswith("  -"):
                    tag_val = next_line.split("-", 1)[1].strip().strip('"').strip("'")
                    if tag_val:
                        old_tags.append(tag_val)
                    j += 1
                else:
                    break
            new_tags = _normalize_tags(old_tags)
            if new_tags != old_tags:
                changes.append(f"tags {old_tags} -> {new_tags}")
            out_lines.append("tags:")
            for t in new_tags:
                out_lines.append(f"  - {t}")
            i = j
            continue

        # domain: explore  → domain: flipbook
        if stripped.startswith("domain:"):
            saw_domain = True
            val = stripped.split(":", 1)[1].strip()
            if val == "explore":
                changes.append("domain: explore -> flipbook")
                out_lines.append("domain: flipbook")
            else:
                out_lines.append(line)
            i += 1
            continue

        out_lines.append(line)
        i += 1

    if not saw_tags:
        # Note had no tags at all — add ours
        out_lines.insert(0, "tags:")
        out_lines.insert(1, "  - kb")
        out_lines.insert(2, "  - flipbook")
        changes.append("tags added: [kb, flipbook]")

    new_fm = "\n".join(out_lines)
    return f"---\n{new_fm}\n---\n{rest}", changes


def _normalize_tags(tags: list[str]) -> list[str]:
    """Ensure 'kb' and 'flipbook' are present; drop bare 'explore' if it's
    the only legacy-marker tag (keep otherwise so users can find via
    legacy tag search)."""
    out = list(tags)
    if "explore" in out and len(out) == 1:
        # Pure explore tag — replace wholesale
        return ["kb", "flipbook"]
    if "kb" not in out:
        out.append("kb")
    if "flipbook" not in out:
        out.append("flipbook")
    return out


def migrate(vault: Path, dry_run: bool) -> int:
    """Returns the number of files migrated (or that would be migrated)."""
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
    md_files = sorted(src.rglob("*.md"))
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
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            f.unlink()
        moved += 1

    # Assets: move _assets subtree
    src_assets = src / "_assets"
    dst_assets = dst / "_assets"
    if src_assets.exists():
        print()
        print(f"  _assets/ — moving subtree to {dst_assets}")
        if not dry_run:
            dst_assets.parent.mkdir(parents=True, exist_ok=True)
            for f in src_assets.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src_assets)
                    target = dst_assets / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        f.rename(target)
                    else:
                        f.unlink()  # already migrated copy in destination

    # Symbols: merge, never overwrite
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

    print()
    if dry_run:
        print(f"  DRY-RUN: would migrate {moved} note(s). Re-run without --dry-run to apply.")
    else:
        print(f"  done: migrated {moved} note(s).")
        # Best-effort: remove the legacy folder if empty
        try:
            # Sweep empty subdirectories left after the move
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without writing anything",
    )
    args = ap.parse_args()

    vault = vault_path()
    print(f"vault: {vault}")
    print()
    print(f"explore → kb flipbook migration {'(dry-run)' if args.dry_run else ''}")
    print("=" * 60)
    n = migrate(vault, args.dry_run)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
