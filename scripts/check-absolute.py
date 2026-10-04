"""Lint: flag inline `position:absolute` that has no `position:relative`
ancestor in the same HTML file.

The meditation app shipped a ⚙ settings button with `style="position:absolute"`
inside a `.page-header` that defaulted to `position:static`. The button
floated to <body>'s top-right and collided with the global nav's ⋯ menu.

This script catches the same pattern in any `apps/**/pages/*.html` file:
for every element with inline `style` containing `position:absolute`, look
for at least one ancestor (selector hit in <style> blocks, OR inline
`style` containing `position:relative|absolute|fixed`) that establishes a
positioning context. Heuristic, not perfect — flags suspicious cases for
manual review.

Exit code:
    0  → no findings
    1  → suspicious files found
    2  → input error
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# rglob below is depth-agnostic, so a single root covers the whole track tree
# (public/extension/personal at any depth).
APP_DIRS = [ROOT / "apps"]

# Class/id names that we KNOW set position via CSS (theme.css globals).
# When a parent's class matches one of these, we assume it has positioning.
KNOWN_POSITIONED = {
    "app-drawer", "app-drawer-overlay", "eos-modal", "modal", "modal-backdrop",
    "page", "eos-header", "page-header", "eos-fab-dock", "timer-ring",
    "topbar", "nav",
}

# Files whose CSS we trust to set position:relative on parents the inline
# style assumes (project-wide common containers).
ALLOWLIST_FILES: set[str] = set()


def _file_css_relative_selectors(html: str) -> set[str]:
    """Return the set of class/id names that have `position: relative|absolute|fixed`
    declared in any <style> block of the file. Heuristic — matches simple
    `.foo { ... position: relative; ... }` rules; misses media queries and
    descendant selectors, but catches the common case."""
    classes: set[str] = set()
    for style_block in re.findall(r"<style[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL):
        # Find every "selector { ...position: relative|absolute|fixed... }" rule
        for rule in re.finditer(r"([^{}]+)\{([^{}]+)\}", style_block):
            selector, body = rule.group(1), rule.group(2)
            if not re.search(r"position\s*:\s*(relative|absolute|fixed|sticky)", body):
                continue
            # Pull bare class/id tokens out of the selector
            for tok in re.findall(r"[.#][A-Za-z0-9_-]+", selector):
                classes.add(tok[1:])
    return classes


def _scan_html(path: Path) -> list[dict]:
    html = path.read_text(encoding="utf-8", errors="replace")
    positioned_classes = _file_css_relative_selectors(html) | KNOWN_POSITIONED
    findings = []

    # Find every element with inline style containing position:absolute
    for m in re.finditer(
        r"<([a-zA-Z][a-zA-Z0-9_-]*)\b[^>]*\bstyle\s*=\s*(['\"])([^'\"]*position\s*:\s*absolute[^'\"]*)\2[^>]*>",
        html,
    ):
        tag = m.group(1).lower()
        offset = m.start()
        line_no = html.count("\n", 0, offset) + 1

        # Walk back from this position to find ancestor tags.
        # Crude: look at the *attributes* of all previously-opened tags whose
        # close tag hasn't appeared yet. We approximate by scanning every
        # `<elementWithClass="..."` before this point and checking class/id
        # names against `positioned_classes`. False-positive-leaning by design.
        prefix = html[:offset]
        # Gather class names mentioned in opening tags before this element
        ancestor_classes = set()
        ancestor_has_relative_inline = False
        for tm in re.finditer(r"<[a-zA-Z][^>]*", prefix):
            tag_open = tm.group(0)
            cm = re.search(r"class\s*=\s*(['\"])([^'\"]+)\1", tag_open)
            if cm:
                for cls in cm.group(2).split():
                    ancestor_classes.add(cls)
            im = re.search(r"\bid\s*=\s*(['\"])([^'\"]+)\1", tag_open)
            if im:
                ancestor_classes.add(im.group(2))
            if re.search(r"style\s*=\s*['\"][^'\"]*position\s*:\s*(relative|absolute|fixed|sticky)", tag_open):
                ancestor_has_relative_inline = True

        # body is always a positioning context for fixed; <body> default is static,
        # so absolute children of body float relative to the viewport — that's
        # almost never intended. Don't grant body as ancestor.

        established = ancestor_has_relative_inline or bool(
            ancestor_classes & positioned_classes
        )
        if not established:
            findings.append({
                "line": line_no,
                "tag": tag,
                "style": m.group(3).strip()[:120],
                "ancestor_classes": sorted(ancestor_classes)[:5],
            })
    return findings


def main() -> int:
    targets: list[Path] = []
    args = sys.argv[1:]
    if args:
        for a in args:
            p = Path(a)
            if not p.is_absolute():
                p = ROOT / a
            if p.is_dir():
                targets.extend(p.rglob("*.html"))
            elif p.is_file():
                targets.append(p)
    else:
        for d in APP_DIRS:
            if d.exists():
                targets.extend(d.rglob("pages/*.html"))
                targets.extend(d.rglob("pages/**/*.html"))

    targets = sorted({t.resolve() for t in targets if "node_modules" not in t.parts})
    if not targets:
        print("No HTML files to scan."); return 0

    total = 0
    flagged_files = 0
    for path in targets:
        if path.name in ALLOWLIST_FILES:
            continue
        findings = _scan_html(path)
        if findings:
            flagged_files += 1
            try:
                rel = path.relative_to(ROOT)
            except ValueError:
                rel = path
            print(f"\n{rel}")
            for f in findings:
                print(f"  L{f['line']}: <{f['tag']} style=\"{f['style']}\">")
                if f['ancestor_classes']:
                    print(f"        nearby ancestor classes: {f['ancestor_classes']}")
                else:
                    print(f"        (no ancestor with positioning context found)")
                total += 1

    if total:
        print(f"\n❌ {total} inline `position:absolute` element(s) in {flagged_files} file(s) "
              f"have no obvious `position:relative` ancestor.")
        print(f"   Fix: add `position:relative` to the containing element, or to its CSS rule.")
        print(f"   See `apps/personal/meditation/pages/index.html` for the canonical fix.")
        return 1

    print(f"✅ Scanned {len(targets)} HTML file(s) — no orphan position:absolute elements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
