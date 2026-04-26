"""
Round off-scale padding/margin/gap/border-radius in app pages to the
FRONTEND-DESIGN-LANGUAGE scales. Only edits inside <style> blocks — JS,
HTML attrs, and inline style="" are left alone because a px in JS is
often a computed threshold, not a visual token.

Usage: python scripts/ui_mechanical_pass.py [--dry-run]
"""

from __future__ import annotations
import re
import sys
from pathlib import Path

SKIP_DIRS = {"_retired", "_example", "tmpl", "tests", "personal"}

# Scales per docs/FRONTEND-DESIGN-LANGUAGE.md §1, §2:
#   radius ∈ {0, 4, 6, 8, 14, 999}
#   spacing ∈ {0, 2, 4, 8, 12, 16, 24, 32, 48}
# Mappings pick the nearest in-scale value; 10px radius → 8 (button bias,
# cards already use 14 explicitly); 99px radius → 999 (pill).
RADIUS_MAP = {
    1: 0, 2: 4, 3: 4, 5: 4, 7: 8, 9: 8, 10: 8, 11: 8,
    12: 14, 13: 14, 15: 14, 16: 14, 18: 14, 20: 14, 99: 999,
}
SPACING_MAP = {
    1: 2, 3: 4, 5: 4, 6: 8, 7: 8, 9: 8, 10: 12, 11: 12,
    14: 16, 15: 16, 18: 16, 20: 24, 22: 24, 28: 32,
}

STYLE_RE = re.compile(r"(<style[^>]*>)(.*?)(</style>)", re.DOTALL | re.IGNORECASE)
RADIUS_DECL_RE = re.compile(r"(border-radius\s*:\s*)([^;}\n]+)", re.IGNORECASE)
SPACING_DECL_RE = re.compile(r"((?:padding|margin|gap|row-gap|column-gap)\s*:\s*)([^;}\n]+)", re.IGNORECASE)
SPACING_SIDE_DECL_RE = re.compile(
    r"((?:padding|margin)-(?:top|right|bottom|left)\s*:\s*)([^;}\n]+)", re.IGNORECASE
)

PX_TOKEN_RE = re.compile(r"\b(\d+)px\b")


def _map_values(values: str, mapping: dict[int, int]) -> tuple[str, int]:
    """Replace every Npx token in `values` per `mapping`. Returns (new, change_count)."""
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        n = int(m.group(1))
        if n in mapping:
            count += 1
            return f"{mapping[n]}px"
        return m.group(0)

    return PX_TOKEN_RE.sub(sub, values), count


def process_style_block(css: str) -> tuple[str, dict[str, int]]:
    stats = {"radius": 0, "spacing": 0}

    def do_radius(m: re.Match) -> str:
        lead, vals = m.group(1), m.group(2)
        new_vals, n = _map_values(vals, RADIUS_MAP)
        stats["radius"] += n
        return lead + new_vals

    def do_spacing(m: re.Match) -> str:
        lead, vals = m.group(1), m.group(2)
        new_vals, n = _map_values(vals, SPACING_MAP)
        stats["spacing"] += n
        return lead + new_vals

    css = RADIUS_DECL_RE.sub(do_radius, css)
    css = SPACING_DECL_RE.sub(do_spacing, css)
    css = SPACING_SIDE_DECL_RE.sub(do_spacing, css)
    return css, stats


def process_file(path: Path, dry: bool) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    total = {"radius": 0, "spacing": 0}

    def do_style(m: re.Match) -> str:
        open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)
        new_body, stats = process_style_block(body)
        for k, v in stats.items():
            total[k] += v
        return open_tag + new_body + close_tag

    new_text = STYLE_RE.sub(do_style, text)
    if (total["radius"] or total["spacing"]) and not dry:
        path.write_text(new_text, encoding="utf-8")
    return total


def main():
    dry = "--dry-run" in sys.argv
    root = Path(__file__).parent.parent / "apps"
    paths: list[Path] = []
    for p in root.glob("*/pages/*.html"):
        app_dir = p.parent.parent.name
        if app_dir in SKIP_DIRS:
            continue
        paths.append(p)

    grand_r = grand_s = 0
    touched_files = 0
    for p in sorted(paths):
        stats = process_file(p, dry)
        if stats["radius"] or stats["spacing"]:
            touched_files += 1
            rel = p.relative_to(root.parent)
            print(f"  {rel}: radius={stats['radius']} spacing={stats['spacing']}")
        grand_r += stats["radius"]
        grand_s += stats["spacing"]

    action = "DRY-RUN" if dry else "APPLIED"
    print(f"\n{action}: {touched_files} files, radius={grand_r}, spacing={grand_s}")


if __name__ == "__main__":
    main()
