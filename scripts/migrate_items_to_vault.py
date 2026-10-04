"""Migrate items.json → per-item vault .md files with frontmatter.

Each item becomes a markdown note in 30_Resources/Items/{category}/:
    30_Resources/Items/Electronics/iphone-15-pro-max.md

Safety:
- Never deletes original items.json
- Skips items that already have a .md file
- Dry-run by default (--apply to write)

Usage:
    python scripts/migrate_items_to_vault.py              # dry-run
    python scripts/migrate_items_to_vault.py --apply      # write files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

VAULT = Path(os.environ.get("EOS_VAULT", "."))
ITEMS_JSON = VAULT / "20_Areas/Personal-Info/items.json"
ITEMS_DIR = VAULT / "30_Resources/Items"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def item_to_frontmatter(item: dict) -> str:
    lines = ["---"]
    lines.append("type: item")
    lines.append(f'title: "{item.get("name", "")}"')

    for key in (
        "category",
        "location",
        "brand",
        "price",
        "currency",
        "purchase_date",
        "warranty_expires",
        "vault_link",
    ):
        val = item.get(key, "")
        if val:
            if isinstance(val, str) and (":" in val or '"' in val or "[[" in val):
                lines.append(f'{key}: "{val}"')
            else:
                lines.append(f"{key}: {val}")

    # Tags
    tags = item.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if "item" not in tags:
        tags.insert(0, "item")
    lines.append("tags:")
    for t in tags:
        lines.append(f"  - {t}")

    # Status: derive from tags
    if "sold" in tags or "disposed" in tags:
        lines.append("status: disposed")
    elif "storage" in tags:
        lines.append("status: stored")
    else:
        lines.append("status: owned")

    # Metadata
    if item.get("added"):
        lines.append(f"added: {item['added'][:10]}")
    if item.get("id"):
        lines.append(f"item_id: {item['id']}")

    lines.append("---")
    return "\n".join(lines)


def item_to_body(item: dict) -> str:
    lines = []
    name = item.get("name", "")
    lines.append(f"# {name}")
    lines.append("")

    desc = item.get("description", "")
    if desc:
        lines.append(desc)
        lines.append("")

    # Purpose
    purpose = item.get("purpose", "")
    if purpose:
        lines.append(f"> {purpose}")
        lines.append("")

    # Vault link
    vlink = item.get("vault_link", "")
    if vlink:
        lines.append(f"Related: {vlink}")
        lines.append("")

    return "\n".join(lines)


def main():
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Migrate items.json to vault .md files")
    parser.add_argument("--apply", action="store_true", help="Write files (default: dry-run)")
    args = parser.parse_args()

    if not ITEMS_JSON.exists():
        print(f"items.json not found: {ITEMS_JSON}")
        sys.exit(1)

    items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    mode = "APPLY" if args.apply else "DRY-RUN"

    print(f"\n{'=' * 60}")
    print(f"  Items Migration: JSON → Vault .md ({mode})")
    print(f"{'=' * 60}\n")
    print(f"Source: {ITEMS_JSON}")
    print(f"Target: {ITEMS_DIR}/{{category}}/")
    print(f"Total items: {len(items)}\n")

    created = []
    skipped = []

    for item in items:
        name = item.get("name", "")
        if not name:
            skipped.append({"name": "(empty)", "reason": "no name"})
            continue

        category = item.get("category", "Other")
        slug = slugify(name)
        target_dir = ITEMS_DIR / category
        target_file = target_dir / f"{slug}.md"

        if target_file.exists():
            skipped.append({"name": name, "reason": "already exists"})
            continue

        fm = item_to_frontmatter(item)
        body = item_to_body(item)
        content = fm + "\n\n" + body

        if args.apply:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file.write_text(content, encoding="utf-8")

        created.append(
            {
                "name": name,
                "path": str(target_file.relative_to(VAULT)),
                "category": category,
            }
        )

    # Report
    print("--- Results ---")
    print(f"{'Created' if args.apply else 'Would create'}: {len(created)}")
    print(f"Skipped: {len(skipped)}")

    if created:
        print(f"\n--- {'Created' if args.apply else 'Would Create'} ---")
        # Group by category
        by_cat: dict[str, list] = {}
        for c in created:
            by_cat.setdefault(c["category"], []).append(c)
        for cat in sorted(by_cat.keys()):
            items_in_cat = by_cat[cat]
            print(f"\n  {cat}/ ({len(items_in_cat)} items)")
            for c in items_in_cat:
                print(f"    {c['name']}")

    if skipped:
        reasons = {}
        for s in skipped:
            r = s["reason"]
            reasons[r] = reasons.get(r, 0) + 1
        print("\n--- Skipped ---")
        for r, n in reasons.items():
            print(f"  {r}: {n}")

    if not args.apply and created:
        print(f"\n  Run with --apply to create {len(created)} files.")

    # Update vault-map suggestion
    if args.apply and created:
        print('\n  vault-map: items_dir = "30_Resources/Items"')
        print(f"  Original items.json preserved at: {ITEMS_JSON}")

    print()


if __name__ == "__main__":
    main()
