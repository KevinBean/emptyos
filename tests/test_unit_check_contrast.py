"""Unit tests for scripts/check-contrast.py token resolution + compositing.

The 2026-07-11 extension made rgba surface tokens (--bg-card etc. in the dark
themes) statically checkable by alpha-compositing them over the theme's own
--bg, and added var(--x) one-hop resolution. These pin that math and the
audit()'s fail counting, without a daemon.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-contrast.py"
_spec = importlib.util.spec_from_file_location("check_contrast", SCRIPT)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


class TestResolve:
    TOKENS = {
        "bg": "#08080f",
        "text": "#c8c8d0",
        "bg-card": "rgba(255,255,255,0.025)",
        "bg-alias": "var(--bg-card)",
        "accent-bg": "color-mix(in srgb, var(--accent) 10%, transparent)",
        "short": "#abc",
    }

    def test_hex_passthrough(self):
        assert cc.resolve("text", self.TOKENS) == "#c8c8d0"
        assert cc.resolve("short", self.TOKENS) == "#abc"

    def test_rgba_composites_over_bg(self):
        # 2.5% white over #08080f → each channel += (255-c)*0.025
        out = cc.resolve("bg-card", self.TOKENS)
        assert out == "#0e0e15", out

    def test_var_reference_one_hop(self):
        assert cc.resolve("bg-alias", self.TOKENS) == "#0e0e15"

    def test_color_mix_unresolvable(self):
        assert cc.resolve("accent-bg", self.TOKENS) is None

    def test_missing_token(self):
        assert cc.resolve("nope", self.TOKENS) is None

    def test_opaque_rgba(self):
        toks = {"bg": "#ffffff", "solid": "rgba(16, 32, 48, 1)"}
        assert cc.resolve("solid", toks) == "#102030"


class TestContrast:
    def test_wcag_reference_values(self):
        assert cc.contrast("#000000", "#ffffff") == 21.0
        assert cc.contrast("#ffffff", "#ffffff") == 1.0

    def test_symmetry(self):
        assert cc.contrast("#333333", "#1a1a1a") == cc.contrast("#1a1a1a", "#333333")


class TestAudit:
    def test_fail_counted_for_bad_theme(self, tmp_path):
        css = """
        .theme-bad {
          --bg: #ffffff; --text: #cccccc; --text-secondary: #cccccc;
          --text-muted: #eeeeee; --accent: #dddddd; --accent-ink: #ffffff;
        }
        """
        p = tmp_path / "theme.css"
        p.write_text(css, encoding="utf-8")
        assert cc.audit(p) >= 3  # body, secondary, muted all fail on white

    def test_surface_pairs_warn_only(self, tmp_path):
        # Unreadable text on a translucent card must WARN, not fail (exit 0),
        # per the calibration posture: surface pairs are advisory until stable.
        css = """
        .theme-x {
          --bg: #000000; --text: #ffffff; --text-secondary: #ffffff;
          --text-muted: #999999; --accent: #ffffff; --accent-ink: #000000;
          --bg-card: rgba(255,255,255,0.9);
        }
        """
        p = tmp_path / "theme.css"
        p.write_text(css, encoding="utf-8")
        # text #fff on card ≈ #e5e5e5 → ~1.2:1, but surface pairs are warn-only.
        assert cc.audit(p) == 0
