"""Regenerate DESIGN.md frontmatter from emptyos/web/static/theme.css.

theme.css is the runtime source of truth. DESIGN.md is the portable contract
external AI tools read. This script keeps them in lockstep — runs in <50ms,
safe to wire as a pre-commit hook.

Usage:
    python scripts/gen-design-md.py            # rewrite DESIGN.md in place
    python scripts/gen-design-md.py --check    # exit 1 if out of date (CI)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME_CSS = ROOT / "emptyos" / "web" / "static" / "theme.css"
DESIGN_MD = ROOT / "DESIGN.md"

TOKEN_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")


def parse_block(css: str, selector: str) -> dict[str, str]:
    """Return {var-name: value} for the first block matching `selector { ... }`."""
    pattern = re.compile(re.escape(selector) + r"\s*\{([^}]*)\}", re.DOTALL)
    m = pattern.search(css)
    if not m:
        raise SystemExit(f"theme.css missing block: {selector}")
    return {name: val.strip() for name, val in TOKEN_RE.findall(m.group(1))}


def yaml_str(v: str) -> str:
    """Quote a value for YAML — strings always quoted to dodge color/number ambiguity."""
    return '"' + v.replace('"', '\\"') + '"'


def render_frontmatter(root: dict[str, str], eos: dict[str, str]) -> str:
    def c(key: str) -> str:
        return yaml_str(eos[key])

    def s(key: str) -> str:
        return yaml_str(root[key])

    lines = [
        "---",
        "version: alpha",
        "name: EmptyOS",
        "description: |",
        "  Visual identity tokens for EmptyOS — a mind companion that thinks and creates",
        "  with the user, not for them. The product looks like a well-designed reading",
        "  app, not like Material/iOS/Linear. This file is the machine-readable token",
        "  contract; docs/FRONTEND-DESIGN-LANGUAGE.md is the human-readable language",
        "  (states, AI-surface treatment, motion philosophy, refused patterns).",
        "  Source of truth: emptyos/web/static/theme.css.",
        "  Regenerate with: python scripts/gen-design-md.py",
        "",
        "colors:",
        "  # Base — eos signature theme (warm stone paper, quiet ink, violet thread)",
        f"  bg:             {c('bg')}",
        f"  bg-surface:     {c('bg-surface')}",
        f"  bg-card:        {c('bg-card')}",
        f"  bg-card-hover:  {c('bg-card-hover')}",
        f"  bg-input:       {c('bg-input')}",
        "",
        f"  text:            {c('text')}",
        f"  text-heading:    {c('text-heading')}",
        f"  text-secondary:  {c('text-secondary')}",
        f"  text-muted:      {c('text-muted')}",
        "",
        f"  border:         {c('border')}",
        f"  border-strong:  {c('border-strong')}",
        "",
        f"  accent:         {c('accent')}",
        f"  accent-dim:     {c('accent-dim')}",
        f"  accent-ink:     {c('accent-ink')}",
        "",
        "  # Semantic data colors — used by intent, never decoratively.",
        f"  red:     {c('danger')}    # overdue / error",
        f"  amber:   {c('warning')}   # warning / stale",
        f"  green:   {c('success')}   # done / success",
        f"  blue:    {s('blue')}     # info / pending",
        f"  purple:  {s('purple')}     # rare / special",
        "",
        "typography:",
        "  display:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-h1')}",
        f"    fontWeight: {root['fw-display']}",
        f"    lineHeight: {root['lh-heading']}",
        f"    letterSpacing: {yaml_str(root['tracking-display'])}",
        "  h1:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-h1')}",
        f"    fontWeight: {root['fw-display']}",
        f"    lineHeight: {root['lh-heading']}",
        f"    letterSpacing: {yaml_str(root['tracking-display'])}",
        "  h2:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-h2')}",
        f"    fontWeight: {root['fw-label']}",
        f"    lineHeight: {root['lh-heading']}",
        f"    letterSpacing: {yaml_str(root['tracking-heading'])}",
        "  prose:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-prose')}",
        f"    fontWeight: {root['fw-body']}",
        f"    lineHeight: {root['lh-prose']}",
        "  body:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-body')}",
        f"    fontWeight: {root['fw-body']}",
        f"    lineHeight: {root['lh-prose']}",
        "  small:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-small')}",
        f"    fontWeight: {root['fw-body']}",
        f"    lineHeight: {root['lh-prose']}",
        "  meta:",
        '    fontFamily: "DM Sans"',
        f"    fontSize: {s('fs-meta')}",
        f"    fontWeight: {root['fw-label']}",
        f"    lineHeight: {root['lh-ui']}",
        f"    letterSpacing: {yaml_str(root['tracking-meta'])}   # all-caps labels only",
        "  mono:",
        '    fontFamily: "IBM Plex Mono"',
        f"    fontSize: {s('fs-mono')}",
        f"    fontWeight: {root['fw-body']}",
        f"    lineHeight: {root['lh-ui']}",
        "",
        "rounded:",
        f"  sm:   {s('radius-sm')}",
        f"  base: {s('radius')}   # inputs, buttons",
        f"  lg:   {s('radius-lg')}  # cards, panels, modals",
        f"  pill: {s('radius-pill')}",
        "",
        "spacing:",
        f'  "1": {s("space-1")}',
        f'  "2": {s("space-2")}',
        f'  "3": {s("space-3")}',
        f'  "4": {s("space-4")}',
        f'  "5": {s("space-5")}',
        f'  "6": {s("space-6")}',
        f'  "7": {s("space-7")}',
        "",
        "motion:",
        "  duration:",
        f"    fast:  {s('dur-fast')}   # hover, focus",
        f"    base:  {s('dur-base')}   # default",
        f"    slide: {s('dur-slide')}   # drawers, slide panels",
        f"    max:   {s('dur-max')}   # upper bound for visible motion",
        "  easing:",
        f"    base:   {s('ease')}",
        f"    out:    {s('ease-out')}",
        f"    spring: {s('ease-spring')}",
        "",
        "layout:",
        f"  maxProse: {s('max-prose')}   # journal, notes, articles",
        f"  maxApp:   {s('max-app')}  # dashboards, lists",
        '  edgePadMobile:  "16px"',
        '  edgePadDesktop: "24px"',
        "  breakpoints:",
        f"    grid:    {s('bp-grid')}   # content grids collapse",
        f"    nav:     {s('bp-nav')}   # nav chrome collapses",
        f"    sidebar: {s('bp-sidebar')}   # sidebar layouts collapse",
        "",
        "components:",
        "  button:",
        '    backgroundColor: "{colors.accent}"',
        '    textColor:       "{colors.accent-ink}"',
        '    rounded:         "{rounded.base}"',
        '    padding:         "8px 20px"',
        '    typography:      "{typography.body}"',
        "  buttonGhost:",
        '    backgroundColor: "transparent"',
        '    textColor:       "{colors.text-secondary}"',
        '    rounded:         "{rounded.base}"',
        '    padding:         "8px 20px"',
        "  card:",
        '    backgroundColor: "{colors.bg-card}"',
        '    rounded:         "{rounded.lg}"',
        '    padding:         "{spacing.4}"',
        "  input:",
        '    backgroundColor: "{colors.bg-input}"',
        '    textColor:       "{colors.text}"',
        '    rounded:         "{rounded.base}"',
        '    padding:         "8px 12px"',
        '    typography:      "{typography.body}"',
        "  badge:",
        '    rounded:    "{rounded.sm}"',
        '    padding:    "2px 10px"',
        '    typography: "{typography.meta}"',
        "  navBar:",
        '    backgroundColor: "{colors.bg}"   # tinted with 92% + backdrop blur at runtime',
        '    height:          "46px"',
        "",
        "themes:",
        "  - eos          # default — warm stone paper, violet thread (tokens above)",
        "  - void-dark    # pure black, minimal",
        "  - warm-dark    # amber lantern",
        "  - nord         # arctic twilight",
        "  - soft-light   # paper daylight",
        "---",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    check_only = "--check" in sys.argv

    css = THEME_CSS.read_text(encoding="utf-8")
    root = parse_block(css, ":root")
    eos = parse_block(css, ".theme-eos")

    new_frontmatter = render_frontmatter(root, eos)

    existing = DESIGN_MD.read_text(encoding="utf-8") if DESIGN_MD.exists() else ""
    parts = existing.split("---", 2)
    body = parts[2].lstrip("\n") if len(parts) >= 3 else ""
    if not body:
        body = "# EmptyOS — DESIGN.md\n\nSee docs/FRONTEND-DESIGN-LANGUAGE.md for the full DNA.\n"

    new_content = new_frontmatter + "\n" + body

    if check_only:
        if existing != new_content:
            print("DESIGN.md is out of date. Run: python scripts/gen-design-md.py", file=sys.stderr)
            return 1
        print("DESIGN.md is in sync with theme.css")
        return 0

    if existing == new_content:
        print("DESIGN.md already in sync — no changes")
        return 0

    DESIGN_MD.write_text(new_content, encoding="utf-8")
    print(f"DESIGN.md regenerated from theme.css ({len(new_content)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
