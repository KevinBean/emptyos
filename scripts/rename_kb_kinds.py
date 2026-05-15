"""One-shot vault migration: rename KB kind values to the unified 8-kind enum.

Three rename passes, applied in order so they don't collide:

1.  kind: reference → kind: clause   (the 41 verbatim source-clause notes;
    must run first so it doesn't sweep up the freshly-renamed standards)
2.  kind: standard  → kind: reference (the 8 whole-source landing pages)
3.  kind: concept   → kind: moc       on the cable-current-rating MOC note
    (and drop its `moc` tag — kind subsumes it)

Usage:
    python scripts/rename_kb_kinds.py --vault "$VAULT"            # dry-run
    python scripts/rename_kb_kinds.py --vault "$VAULT" --commit   # write

Pass the vault path from `notes.path` in your `emptyos.toml`.

The script is idempotent — running it twice is a no-op on the second run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Files matching this slug get the MOC promotion. Add more if other MOC notes
# turn up. Slug = filename stem.
MOC_SLUGS = {"cable-current-rating-moc"}

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _read_kind(fm: str) -> str | None:
    m = re.search(r"^kind:\s*(.+)$", fm, re.MULTILINE)
    return m.group(1).strip() if m else None


def _has_kb_tag(fm: str) -> bool:
    # Tolerant check for either inline ([kb, ...]) or block (- kb) tag form.
    if re.search(r"^tags:\s*\[[^\]]*\bkb\b[^\]]*\]", fm, re.MULTILINE):
        return True
    in_tags = False
    for line in fm.splitlines():
        if re.match(r"^tags:\s*$", line):
            in_tags = True
            continue
        if in_tags:
            if line.startswith("  - "):
                if line.strip() == "- kb":
                    return True
            else:
                in_tags = False
    return False


def _replace_kind(fm: str, new_kind: str) -> str:
    return re.sub(r"^kind:\s*.+$", f"kind: {new_kind}", fm, count=1, flags=re.MULTILINE)


def _drop_moc_tag(fm: str) -> str:
    # Block-style: a `  - moc` line under a `tags:` block.
    lines = fm.splitlines()
    out: list[str] = []
    in_tags = False
    for line in lines:
        if re.match(r"^tags:\s*$", line):
            in_tags = True
            out.append(line)
            continue
        if in_tags and line.startswith("  - "):
            if line.strip() == "- moc":
                continue  # drop
            out.append(line)
            continue
        in_tags = False
        out.append(line)
    text = "\n".join(out)
    # Inline-style: tags: [kb, moc, ...] → strip moc token only.
    text = re.sub(
        r"^(tags:\s*\[)([^\]]*)\]",
        lambda m: m.group(1) + ", ".join(t.strip() for t in m.group(2).split(",") if t.strip() != "moc") + "]",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    return text


def process_file(path: Path, commit: bool) -> tuple[bool, list[str]]:
    """Return (changed, list_of_changes_applied)."""
    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"  ! read error: {e}"]
    m = FRONTMATTER_RE.match(original)
    if not m:
        return False, []
    fm = m.group(1)
    if not _has_kb_tag(fm):
        return False, []
    kind = _read_kind(fm)
    slug = path.stem
    changes: list[str] = []
    new_fm = fm
    # Pass 1: kind: reference -> kind: clause
    if kind == "reference":
        new_fm = _replace_kind(new_fm, "clause")
        changes.append("kind reference→clause")
        kind = "clause"
    # Pass 2: kind: standard -> kind: reference
    elif kind == "standard":
        new_fm = _replace_kind(new_fm, "reference")
        changes.append("kind standard→reference")
        kind = "reference"
    # Pass 3: MOC promotion (slug-based)
    if slug in MOC_SLUGS and kind != "moc":
        new_fm = _replace_kind(new_fm, "moc")
        new_fm = _drop_moc_tag(new_fm)
        changes.append("kind→moc + drop moc tag")
    if not changes:
        return False, []
    new_text = "---\n" + new_fm + "\n---\n" + original[m.end():]
    if commit:
        path.write_text(new_text, encoding="utf-8")
    return True, changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument("--commit", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()
    vault = Path(args.vault)
    if not vault.is_dir():
        print(f"vault path not found: {vault}", file=sys.stderr)
        return 2
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"=== KB kind rename ({mode}) ===")
    print(f"vault: {vault}")
    n_total = 0
    n_changed = 0
    for f in vault.rglob("*.md"):
        n_total += 1
        changed, changes = process_file(f, args.commit)
        if changed:
            n_changed += 1
            rel = f.relative_to(vault).as_posix()
            print(f"  {rel}")
            for c in changes:
                print(f"      - {c}")
    print(f"\nscanned {n_total} files; {n_changed} would change (or did change in commit mode)")
    if not args.commit and n_changed:
        print("re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
