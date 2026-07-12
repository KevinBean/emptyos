#!/usr/bin/env python3
"""Static guard: text colours must survive all six themes.

The graduated form of the 2026-07-11 readability audit (see
docs/READABILITY-AUDIT-2026-07-11.md). That audit found ~60 apps whose text was
unreadable in at least one theme, and every defect reduced to the same mistake:
*a colour chosen against one background, then rendered against six.*

The rendered audit (scripts/check_readability.py) catches it properly but needs a
live daemon and ~1h for a full sweep. This is the cheap static half — no daemon,
runs in a second, so it can gate preflight and stop the two highest-volume shapes
from ever coming back:

  T1  White text inside a rule that paints a THEME-VARYING background
      (`var(--accent)`, a status token, or either nested in a var() fallback).
      Invisible-as-a-bug on the purple/blue themes; **1.68:1** on warm-dark's
      amber accent. `var(--accent-ink)` exists per theme for exactly this.
      (Was wrong in 25+ files, incl. the shared page-assistant/chat-shell bundles.)

  T2  A hardcoded status-palette hex used as a *text* colour. `color:#34d399` is
      a dark-theme mint that reads **1.9:1 on white**. Use the semantic token —
      var(--success|--warning|--danger|--info) — which is tuned per theme.

  T3  A stylesheet other than theme.css redefining a GLOBAL theme token at :root.
      `eos-flipbook.css` declared `:root { --accent: #6f5d3f }` — same specificity
      as `.theme-*` but loaded later, so it *won*, silently replacing the active
      theme's accent across the whole page (the global nav breadcrumb included) on
      every surface that imported the bundle. Namespace component tokens
      (`--ex-accent`), or scope the block to the component root.

All three are high-confidence — none has a legitimate use — and T1/T2 are DL-1
(hardcoded colours) compliance as much as contrast fixes. Backgrounds, fills,
strokes, borders and chart palettes are never touched: only `color:` declarations,
and (for T3) token declarations in a global block.

Opt-out (rare — a genuinely deliberate case): put an inline marker on the line
or the line above:

    color: #34d399;  /* text-tokens: ignore — brand-locked, dark-only island */

The marker requires a rationale after the dash, so every opt-out carries context.

Exit code = number of violations (so it can gate).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import theme_css  # noqa: E402 — sibling script, path set above

REPO = Path(__file__).resolve().parent.parent
_blank_comments = theme_css.blank_comments

# Status-palette hexes that must never be a text colour. These are the literals
# the audit actually found in the tree, in both long and short form.
STATUS_HEX = {
    # info / cyan / blue
    "#22d3ee", "#06b6d4", "#60a5fa", "#3b82f6",
    # success / green
    "#34d399", "#10b981", "#1fc66b", "#34c759", "#88bb88", "#66aa66", "#84cc16",
    "#8b8", "#6a6",
    # warning / amber
    "#fbbf24", "#f59e0b", "#eab308", "#cc9966", "#c96",
    # danger / red
    "#fb7185", "#ff6565", "#cc7777", "#dd6666", "#ef4444",
    "#d66", "#d55", "#c55", "#c66", "#c77",
}

SUGGEST = {
    "info": "var(--info)", "success": "var(--success)",
    "warning": "var(--warning)", "danger": "var(--danger)",
}


def _suggest(h: str) -> str:
    h = h.lower()
    if h in {"#22d3ee", "#06b6d4", "#60a5fa", "#3b82f6"}:
        return SUGGEST["info"]
    if h in {"#34d399", "#10b981", "#1fc66b", "#34c759", "#88bb88", "#66aa66", "#84cc16", "#8b8", "#6a6"}:
        return SUGGEST["success"]
    if h in {"#fbbf24", "#f59e0b", "#eab308", "#cc9966", "#c96"}:
        return SUGGEST["warning"]
    return SUGGEST["danger"]


IGNORE = re.compile(r"text-tokens:\s*ignore\s*—|text-tokens:\s*ignore\s*--")

# `color: <hex>` — never background-color / border-color / -webkit-text-fill-color
_COLOR_HEX = re.compile(
    r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{3,6})\b", re.IGNORECASE)

# A block painting a *theme-varying* colour as its background. Includes --accent,
# the four status tokens, and their aliases, in any `var()` nesting — the 2026-07-11
# warm-dark pass found all three of these forms in the wild:
#     background: var(--mode-active-bg, var(--accent))     ← accent nested in a fallback
#     background: var(--warning, #f59e0b)                  ← status token, not accent
#     background: var(--accent)                            ← the plain form
_THEMED_BG = re.compile(
    r"background(?:-color)?\s*:[^;]*var\(\s*--(?:[\w-]*-)?"
    r"(?:accent|success|warning|danger|info|red|amber|green|blue|purple)\b")

# Text that resolves to white — literal, or via a var() fallback whose default is
# white (`var(--accent-text, white)` — a token that doesn't exist, so white wins).
#
# An `*-ink` token is the EXCEPTION: `var(--accent-ink, #fff)` is the correct,
# theme-aware answer (the fallback only applies where the token is undefined), so
# it must not be flagged — otherwise the checker condemns its own prescribed fix.
_WHITE_TEXT = re.compile(
    r"(?<![-\w])color\s*:\s*(?:"
    r"(?P<literal>#fff\b|#ffffff\b|white\b)"                          # literal
    r"|var\(\s*(?P<token>--[\w-]+)[^)]*,\s*(?:#fff\b|#ffffff\b|white\b)\s*\)"  # var(--x, white)
    r")", re.IGNORECASE)


def _is_white_violation(m: re.Match) -> bool:
    # Any chain that reaches an `*-ink` token is already correct, including the
    # nested form `var(--mode-active-fg, var(--accent-ink, #fff))`.
    return "-ink" not in m.group(0).lower()


# T3 — global theme tokens. Any stylesheet OTHER than theme.css that declares one
# of these at :root (or on html/body) hijacks the active theme for the whole page.
# Component stylesheets must namespace their own tokens.
_ROOT_BLOCK = theme_css.ROOT_BLOCK
_TOKEN_DECL = re.compile(r"--([\w-]+)\s*:")

# A :root value that IS a colour (or aliases one). theme.css's :root also carries
# the non-colour scale — fonts, radius, spacing, motion — and redefining those is a
# type/layout concern (FDL §2/§3), not a readability one. Only colour is this
# checker's business.
_COLOURISH = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|color-mix\(|"
    r"var\(\s*--(?:accent|bg|text|border|success|warning|danger|info|shadow|glow)",
    re.IGNORECASE)


def _global_theme_tokens() -> frozenset[str]:
    """The global COLOUR tokens, read from theme.css — never duplicated here.

    A hardcoded list would drift the moment a token is added (`--ink-on-vivid`
    landed the same day this checker was written) and the checker would silently
    stop guarding it. So: every token in a `.theme-*` block (those are colour by
    construction), plus the `:root` tokens whose value is itself a colour — which
    picks up the `--red/--amber/--green/--blue/--purple` data set and
    `--ink-on-vivid` while skipping `--radius`, `--font`, `--space-*`, `--dur-*`.
    """
    css = theme_css.load()
    if not css:
        return frozenset()
    toks = {t.lower() for tokens in theme_css.parse_themes(css).values() for t in tokens}
    toks |= {t.lower() for t, v in theme_css.parse_root(css).items() if _COLOURISH.search(v)}
    return frozenset(toks)


_THEME_TOKENS = _global_theme_tokens()


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _ignored(lines: list[str], lineno: int) -> bool:
    """Marker on the offending line, or the line immediately above it."""
    for i in (lineno - 1, lineno - 2):
        if 0 <= i < len(lines) and IGNORE.search(lines[i]):
            return True
    return False


def scan(path: Path) -> list[tuple[int, str, str]]:
    """-> [(lineno, code, message)]"""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []
    src = _blank_comments(raw)
    lines = raw.split("\n")   # opt-out markers live IN comments — match on the raw text
    out: list[tuple[int, str, str]] = []

    # T1 — white text inside an accent-background block (CSS rules + inline styles).
    blocks: list[tuple[int, str]] = []
    for m in re.finditer(r"\{([^{}]*)\}", src):
        blocks.append((m.start(1), m.group(1)))
    for m in re.finditer(r'style\s*=\s*"([^"]*)"', src):
        blocks.append((m.start(1), m.group(1)))
    for offset, body in blocks:
        if not _THEMED_BG.search(body):
            continue
        for wm in _WHITE_TEXT.finditer(body):
            if not _is_white_violation(wm):
                continue
            ln = _line_of(src, offset + wm.start())
            if _ignored(lines, ln):
                continue
            out.append((ln, "T1",
                        f"`{wm.group(0).strip()}` on a themed background — white does not "
                        f"survive every theme (1.68:1 on warm-dark's amber). Use "
                        f"var(--accent-ink) on --accent, or the tint idiom on a status colour"))

    # T3 — a non-theme.css stylesheet redefining a global theme token at :root.
    if path.name != "theme.css":
        for m in _ROOT_BLOCK.finditer(src):
            for tm in _TOKEN_DECL.finditer(m.group(1)):
                tok = tm.group(1).lower()
                if tok not in _THEME_TOKENS:
                    continue
                ln = _line_of(src, m.start(1) + tm.start())
                if _ignored(lines, ln):
                    continue
                out.append((ln, "T3",
                            f"`--{tok}` is a GLOBAL theme token; redefining it at :root here "
                            f"overrides the active theme for the whole page (theme.css loses on "
                            f"load order). Namespace it (e.g. --ex-{tok}) or scope it to the component"))

    # T2 — hardcoded status hex as a text colour.
    for m in _COLOR_HEX.finditer(src):
        h = m.group(1).lower()
        if h not in STATUS_HEX:
            continue
        ln = _line_of(src, m.start())
        if _ignored(lines, ln):
            continue
        out.append((ln, "T2",
                    f"`color: {m.group(1)}` is a hardcoded status colour — "
                    f"use {_suggest(h)} (theme-tuned; the literal fails on some themes)"))

    return out


def targets() -> list[Path]:
    seen: list[Path] = []
    for pat in ("apps/**/pages/*.html", "apps/**/pages/*.js", "apps/**/pages/*.css",
                "emptyos/web/static/*.css", "emptyos/web/static/*.js"):
        seen += REPO.glob(pat)
    return sorted({p for p in seen
                   if "_retired" not in p.parts and "_archive" not in p.parts
                   and not p.name.endswith(".legacy.html")})


def main() -> int:
    only = None
    args = sys.argv[1:]
    if "--app" in args:
        only = args[args.index("--app") + 1]

    violations = 0
    files = 0
    for p in targets():
        rel = p.relative_to(REPO).as_posix()
        if only and f"/{only}/" not in f"/{rel}":
            continue
        hits = scan(p)
        if not hits:
            continue
        files += 1
        print(f"\n  {rel}")
        for ln, code, msg in hits:
            print(f"    {code} :{ln}  {msg}")
            violations += 1

    if violations:
        print(f"\n✗ {violations} text-colour violation(s) in {files} file(s).")
        print("  Text colour must survive all 6 themes — see docs/FRONTEND-DESIGN-LANGUAGE.md §4.1.")
        print("  Measure a page live with `?debug=readability`, or "
              "`python scripts/check_readability.py --app <id>`.")
    else:
        print("✓ text colours clean — no hardcoded status hex, no #fff on --accent.")
    return violations


if __name__ == "__main__":
    sys.exit(main())
