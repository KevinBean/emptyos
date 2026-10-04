"""The toast's own text/tint pair must clear WCAG AA on every theme.

`check-contrast.py` cannot cover this. It walks *token* pairs, and neither side
of a toast is a token: the background is a literal `rgba()` the component paints
itself, and the text is a literal hex chosen to sit on that specific ground.
So the pair had no gate at all, and `.eos-toast-err` shipped at **3.13–3.89 —
below AA on all ten themes** until 2026-09-05.

Keeping the literals is deliberate, not drift. The toast supplies its own
near-opaque ground, so `var(--text)` would swing with the theme while the ground
stayed put — tokenising this pair is the one "fix" that would reintroduce the
bug. What the literals owe is contrast, which is what this pins.

The 0.92 alpha lets a little of `--bg` through, so the ratio is a narrow range
across themes rather than a single number; the assertion is on the worst theme.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "emptyos" / "web" / "static"

_SPEC = importlib.util.spec_from_file_location(
    "check_contrast", ROOT / "scripts" / "check-contrast.py"
)
cc = importlib.util.module_from_spec(_SPEC)
sys.modules["check_contrast"] = cc
_SPEC.loader.exec_module(cc)

AA_NORMAL = 4.5

#: `.<class>` -> the rgba ground it paints for itself.
TOASTS = {
    "eos-toast-ok": None,
    "eos-toast-err": None,
}


def _rule(css: str, cls: str) -> tuple[str, tuple[int, int, int], float]:
    """(text hex, tint rgb, alpha) for one toast class."""
    m = re.search(r"\." + re.escape(cls) + r"\s*\{([^}]*)\}", css)
    assert m, f".{cls} not found — the selector was renamed; update this test"
    body = m.group(1)
    fg = re.search(r"color\s*:\s*(#[0-9a-fA-F]{3,6})", body)
    bg = re.search(r"background\s*:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", body)
    assert fg, f".{cls} has no literal text colour"
    assert bg, f".{cls} no longer paints its own rgba ground — re-read this test's premise"
    r, g, b, a = int(bg.group(1)), int(bg.group(2)), int(bg.group(3)), float(bg.group(4))
    return fg.group(1), (r, g, b), a


def _theme_bgs(theme_css: str) -> list[str]:
    bgs = [m.group(1) for m in re.finditer(r"--bg:\s*(#[0-9a-fA-F]{3,6})", theme_css)]
    assert len(bgs) >= 2, "expected several themes to define --bg"
    return bgs


def _composite(tint: tuple[int, int, int], alpha: float, bg_hex: str) -> str:
    bg = cc._to_rgb(bg_hex)
    return cc._to_hex(tuple(tint[i] * alpha + bg[i] * (1 - alpha) for i in range(3)))


@pytest.mark.parametrize("cls", sorted(TOASTS))
def test_toast_text_clears_aa_on_every_theme(cls):
    comp = (STATIC / "eos-components.css").read_text(encoding="utf-8")
    theme = (STATIC / "theme.css").read_text(encoding="utf-8")
    fg, tint, alpha = _rule(comp, cls)
    worst, worst_bg = None, None
    for bg_hex in _theme_bgs(theme):
        r = cc.contrast(fg, _composite(tint, alpha, bg_hex))
        if worst is None or r < worst:
            worst, worst_bg = r, bg_hex
    assert worst >= AA_NORMAL, (
        f".{cls}: text {fg} on its own rgba{tint} @ {alpha} over --bg {worst_bg} "
        f"is {worst:.2f}:1, below AA {AA_NORMAL}. Darken the text — do NOT swap "
        f"it for a theme token; the ground is fixed, so a token would swing "
        f"under it."
    )
