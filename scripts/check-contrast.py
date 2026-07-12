#!/usr/bin/env python3
"""Static WCAG-contrast audit of EmptyOS's themes (emptyos/web/static/theme.css).

The graduated form of the 2026-06-07 design self-audit (the `designer` +
`design-system-*` registry pointed back at EmptyOS — see
.claude/rules/self-audit-loops.md). Parses each `.theme-*` block, computes the
contrast ratio for the key text/accent pairs, and flags AA violations so muted
text can't silently regress below legibility again.

Tokens are resolved before checking: solid hex directly; `var(--x)` one level
deep; `rgba(...)` alpha-composited over the theme's own `--bg` (so the dark
themes' translucent `--bg-card`/`--bg-input` surfaces are checkable too —
they were invisible to the hex-only v1). `color-mix(...)` stays statically
unresolvable and is skipped; the rendered-page sibling
(scripts/check_readability.py) covers what this can't see.

Pure file I/O — no daemon, no kernel import. Run from /preflight or release.

Exit code = number of hard FAILs (so it can gate). WARN is advisory — the
surface pairs (text on card/surface/input) are warn-only until a full
calibration pass shows them stable across all 6 themes.

Thresholds (WCAG 2.1 AA):
  body/secondary on bg     : FAIL < 4.5  (normal-size body text)
  muted on bg              : FAIL < 3.0, WARN < 3.5  (low-emphasis; AA-large floor)
  accent (link) on bg      : FAIL < 3.0, WARN < 4.5  (small link text wants 4.5)
  accent-ink on accent     : WARN < 4.5  (button label; advisory — sizes vary)
  text/secondary on card…  : WARN < 4.5  (surface pairs; advisory)
  muted on card/input      : WARN < 3.0  (placeholder legibility floor)
"""

from __future__ import annotations

import re
import sys
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import theme_css  # noqa: E402 — sibling script, path set above

THEME_CSS = theme_css.THEME_CSS

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$")
_VARREF = re.compile(r"^var\(\s*--([\w-]+)\s*\)$")


def _to_rgb(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0, min(255, v))):02x}" for v in rgb)


def _lum(hex_str: str) -> float:
    r, g, b = (v / 255 for v in _to_rgb(hex_str))
    f = lambda c: (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return round((hi + 0.05) / (lo + 0.05), 2)


def resolve(name: str, tokens: dict[str, str], depth: int = 0) -> str | None:
    """Resolve a token to a solid hex color.

    hex → itself; var(--x) → one hop; rgba(...) → composited over the theme's
    own --bg. Returns None for color-mix()/missing/deep-nested values (not
    statically checkable).
    """
    if depth > 2:
        return None
    val = (tokens.get(name) or "").strip()
    if not val:
        return None
    if _HEX.match(val):
        return val
    m = _VARREF.match(val)
    if m:
        return resolve(m.group(1), tokens, depth + 1)
    m = _RGBA.match(val)
    if m:
        base = resolve("bg", tokens, depth + 1)
        if not base:
            return None
        r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        br, bgr, bb = _to_rgb(base)
        return _to_hex((r * a + br * (1 - a), g * a + bgr * (1 - a), b * a + bb * (1 - a)))
    return None  # color-mix() etc.


#: theme.css is parsed in exactly one place — scripts/theme_css.py. This alias keeps
#: the local call sites (and tests) reading naturally.
_parse_themes = theme_css.parse_themes


# (fg_token, bg_token, fail_below, warn_below, label)
_CHECKS = [
    ("text", "bg", 4.5, 4.5, "body/bg"),
    ("text-secondary", "bg", 4.5, 4.5, "secondary/bg"),
    ("text-muted", "bg", 3.0, 3.5, "muted/bg"),
    ("accent", "bg", 3.0, 4.5, "link/bg"),
    ("accent-ink", "accent", 0.0, 4.5, "btn-label/accent"),
    # Surface pairs — the text actually sits on cards/inputs, whose tokens are
    # rgba in the dark themes (composited over --bg by resolve()). Warn-only
    # until calibration shows them stable; promote per-pair after that.
    ("text", "bg-card", 0.0, 4.5, "body/card"),
    ("text", "bg-surface", 0.0, 4.5, "body/surface"),
    ("text", "bg-input", 0.0, 4.5, "body/input"),
    ("text-secondary", "bg-card", 0.0, 4.5, "secondary/card"),
    ("text-muted", "bg-card", 0.0, 3.0, "muted/card"),
    ("text-muted", "bg-input", 0.0, 3.0, "placeholder/input"),
    # Semantic status colors — used as text on chips/stats across many apps.
    # The 2026-07-11 rendered audit caught soft-light shipping the dark-theme
    # mint --success (1.9:1 on white); these pin all four per theme.
    ("success", "bg", 0.0, 3.0, "success/bg"),
    ("warning", "bg", 0.0, 3.0, "warning/bg"),
    ("danger", "bg", 0.0, 3.0, "danger/bg"),
    ("info", "bg", 0.0, 3.0, "info/bg"),
    ("success", "bg-card", 0.0, 3.0, "success/card"),
    ("danger", "bg-card", 0.0, 3.0, "danger/card"),
]


# Status colours are most often rendered as text on their OWN tint — the standard
# chip idiom: `background: color-mix(in srgb, var(--success) 15%, var(--bg-card));
# color: var(--success)`. That tint is a color-mix() the token parser can't
# resolve, so the pair is checked by *deriving* the tint here. This is the
# dominant text shape across the app tree; it was 2.7-2.8:1 before 2026-07-11.
_TINT_PCT = 0.18   # a little past the common 15% — the pessimistic end of the idiom
_TINT_CHECKS = [("success", 3.0), ("warning", 3.0), ("danger", 3.0), ("info", 3.0)]


def _tint_over(fg_hex: str, base_hex: str, pct: float = _TINT_PCT) -> str:
    """The chip's ground: fg mixed pct into base."""
    fr, fg_, fb = _to_rgb(fg_hex)
    br, bg_, bb = _to_rgb(base_hex)
    return _to_hex((fr * pct + br * (1 - pct),
                    fg_ * pct + bg_ * (1 - pct),
                    fb * pct + bb * (1 - pct)))


def audit(path: Path = THEME_CSS) -> int:
    if not path.exists():
        print(f"check-contrast: {path} not found", file=sys.stderr)
        return 0
    themes = _parse_themes(path.read_text(encoding="utf-8"))
    fails = warns = skipped = 0
    print(f"WCAG contrast audit — {path.relative_to(path.parents[3]) if len(path.parents) >= 4 else path}\n")
    for name, tok in sorted(themes.items()):
        lines = []
        for fg, bg, fail_below, warn_below, label in _CHECKS:
            fg_hex, bg_hex = resolve(fg, tok), resolve(bg, tok)
            if not fg_hex or not bg_hex:
                if fg in tok and bg in tok:
                    skipped += 1  # declared but not statically resolvable (color-mix)
                continue
            v = contrast(fg_hex, bg_hex)
            if v < fail_below:
                tag, mark = "FAIL", "✗"; fails += 1
            elif v < warn_below:
                tag, mark = "warn", "·"; warns += 1
            else:
                tag, mark = "ok", "✓"
            lines.append(f"    {mark} {label:18} {v:>5}  {tag}")

        # Status colour as text on its own tint (the chip idiom) — warn-only.
        card = resolve("bg-card", tok) or resolve("bg", tok)
        for status, warn_below in _TINT_CHECKS:
            fg_hex = resolve(status, tok)
            if not fg_hex or not card:
                continue
            v = contrast(fg_hex, _tint_over(fg_hex, card))
            if v < warn_below:
                warns += 1
                lines.append(f"    · {status + '/own-tint':18} {v:>5}  warn")

        flag = "✗" if any("FAIL" in ln for ln in lines) else ("·" if any("warn" in ln for ln in lines) else "✓")
        print(f"  {flag} .theme-{name}")
        print("\n".join(lines))
    print(f"\n{len(themes)} themes · {fails} FAIL · {warns} warn"
          + (f" · {skipped} pair(s) skipped (color-mix — see check_readability.py)" if skipped else ""))
    if fails:
        print("Muted/secondary text below the AA floor — bump the token (same hue, "
              "more lightness) until it clears. See .claude/rules/self-audit-loops.md.")
    return fails


if __name__ == "__main__":
    sys.exit(audit())
