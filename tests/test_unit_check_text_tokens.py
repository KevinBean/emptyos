"""Both-direction pins for scripts/check-text-tokens.py.

Per .claude/skills/eos-graduate-audit: a graduated checker ships with tests
pinning BOTH "fires on the real regression" and "silent on healthy code".
The two shapes it guards are the top-two defect classes from the 2026-07-11
readability audit (docs/READABILITY-AUDIT-2026-07-11.md).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-text-tokens.py"
_spec = importlib.util.spec_from_file_location("check_text_tokens", SCRIPT)
ctt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctt)


def _scan(tmp_path: Path, body: str, name: str = "index.html"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return ctt.scan(p)


class TestFiresOnRegression:
    def test_white_on_accent_css_rule(self, tmp_path):
        hits = _scan(tmp_path, ".btn { background: var(--accent); color: #fff; }")
        assert [h[1] for h in hits] == ["T1"], hits

    def test_white_on_accent_inline_style(self, tmp_path):
        hits = _scan(tmp_path, '<b style="background:var(--accent);color:white">x</b>')
        assert [h[1] for h in hits] == ["T1"], hits

    def test_white_on_aliased_accent_with_fallback(self, tmp_path):
        # `var(--learn-accent, #6c5ce7)` — the alias + fallback form that the
        # first regex sweep missed and the live audit caught.
        hits = _scan(tmp_path, ".chip { background: var(--learn-accent, #6c5ce7); color: #ffffff; }")
        assert [h[1] for h in hits] == ["T1"], hits

    def test_white_on_a_status_background(self, tmp_path):
        # White on a *status* fill (not just accent): the dark themes' status
        # colours are bright (mint #34d399, rose #f87171) → white reads 1.6-2.3:1.
        hits = _scan(tmp_path, ".badge { background: var(--danger); color: #fff; }")
        assert [h[1] for h in hits] == ["T1"], hits

    def test_white_via_nonexistent_token_fallback(self, tmp_path):
        # `--accent-text` was a typo for --accent-ink, so `white` actually won.
        hits = _scan(tmp_path, ".t { background: var(--accent); color: var(--accent-text, white); }")
        assert [h[1] for h in hits] == ["T1"], hits

    def test_status_hex_as_text(self, tmp_path):
        hits = _scan(tmp_path, ".ok { color: #34d399; }")
        assert len(hits) == 1 and hits[0][1] == "T2"
        assert "var(--success)" in hits[0][2]

    def test_status_hex_shorthand(self, tmp_path):
        hits = _scan(tmp_path, ".bad { color:#c77; }")
        assert [h[1] for h in hits] == ["T2"], hits
        assert "var(--danger)" in hits[0][2]

    def test_theme_token_hijacked_at_root(self, tmp_path):
        # eos-flipbook.css declared `:root { --accent: #6f5d3f }` — same specificity
        # as .theme-*, loaded later, so it replaced the ACTIVE THEME'S accent across
        # every page that imported the bundle (the global nav included).
        hits = _scan(tmp_path, ":root { --accent: #6f5d3f; --paper: #f5efe6; }", "comp.css")
        assert [h[1] for h in hits] == ["T3"], hits
        assert "--accent" in hits[0][2]

    def test_theme_token_hijack_on_body(self, tmp_path):
        hits = _scan(tmp_path, "body { --bg: #000; }", "comp.css")
        assert [h[1] for h in hits] == ["T3"], hits

    def test_reports_the_right_line(self, tmp_path):
        hits = _scan(tmp_path, "a{}\nb{}\n.x { color: #fbbf24; }\n")
        assert hits[0][0] == 3, hits

    def test_status_hex_as_a_surface(self, tmp_path):
        # T4. `--red/--amber/--green` alias the per-theme status tokens, so the
        # literal is frozen where the token adapts — the theme-invariant-palette
        # defect the 2026-07-11 readability audit found, one property over.
        hits = _scan(tmp_path, ".bar-over { background:#ef4444 }")
        assert [h[1] for h in hits] == ["T4"], hits
        assert "var(--danger)" in hits[0][2]

    def test_status_hex_on_a_border(self, tmp_path):
        hits = _scan(tmp_path, ".viol { border-left:3px solid #10b981 }")
        assert [h[1] for h in hits] == ["T4"], hits

    def test_short_form_status_hex_as_a_surface(self, tmp_path):
        # The 3-char form is the same literal (grid-analytics used #d66/#c96/#8b8).
        hits = _scan(tmp_path, ".ring { border-color:#d66 }")
        assert [h[1] for h in hits] == ["T4"], hits

    def test_one_line_two_declarations_reports_once(self, tmp_path):
        # `border-color:X; background:X` is one defect, not two.
        hits = _scan(tmp_path, ".e::before { border-color:#3b82f6; background:#3b82f6 }")
        assert [h[1] for h in hits] == ["T4"], hits


class TestSilentOnHealthy:
    def test_semantic_tokens_are_fine(self, tmp_path):
        assert _scan(tmp_path, """
            .ok   { color: var(--success); }
            .warn { color: var(--warning); }
            .btn  { background: var(--accent); color: var(--accent-ink, #fff); }
        """) == []

    def test_ink_tokens_are_the_prescribed_fix(self, tmp_path):
        # The checker must not condemn its own prescribed fix — including the
        # nested-fallback form used by the shared .eos-mode-btn.
        assert _scan(tmp_path, """
            .a { background: var(--accent); color: var(--ink-on-vivid); }
            .b { background: var(--danger); color: var(--ink-on-vivid); }
            .c { background: var(--mode-active-bg, var(--accent)); color: var(--mode-active-fg, var(--accent-ink, #fff)); }
        """) == []

    def test_svg_fills_and_palette_arrays_are_fine(self, tmp_path):
        # T4 narrowed the old "backgrounds are never touched" rule, but only for
        # background/border/outline/box-shadow. SVG fills and strokes stay exempt
        # (a chart's fill is a categorical axis, not a status surface), and so do
        # palette arrays and gradients.
        assert _scan(tmp_path, """
            circle  { fill: #22d3ee; stroke: #60a5fa; }
            var COLORS = ['#34d399','#fbbf24'];
            .coin   { background: linear-gradient(135deg,#f59e0b,#fbbf24); }
        """) == []

    def test_white_text_without_accent_bg_is_fine(self, tmp_path):
        # White on a dark literal background is a visual island's business.
        assert _scan(tmp_path, ".hero { background: #0e1116; color: #fff; }") == []

    def test_non_status_hex_is_fine(self, tmp_path):
        assert _scan(tmp_path, ".x { color: #3d2817; }") == []

    def test_namespaced_component_tokens_are_fine(self, tmp_path):
        # A component may define whatever it likes, as long as it doesn't collide
        # with a GLOBAL theme token (the --ex-* / --aura-* / --qr-* convention).
        assert _scan(tmp_path, ":root { --ex-accent: #6f5d3f; --paper: #f5efe6; --qr-sans: serif; }",
                     "comp.css") == []

    def test_theme_css_itself_may_define_theme_tokens(self, tmp_path):
        assert _scan(tmp_path, ":root { --accent: #6c5ce7; --bg: #fff; }", "theme.css") == []

    def test_token_named_in_a_comment_is_not_a_declaration(self, tmp_path):
        # quickref's own explanatory comment ("mixed toward --text:") tripped this.
        assert _scan(tmp_path, ":root {\n  /* mixed toward --text: keeps the hue */\n"
                               "  --qr-accent: color-mix(in srgb, #e8c547 52%, var(--text));\n}",
                     "comp.css") == []

    def test_surface_hex_exclusions(self, tmp_path):
        # The four shapes that separate T4's signal, each measured against the
        # real tree rather than guessed (2026-08-17).
        assert _scan(tmp_path, ".c { background: linear-gradient(135deg,#f59e0b,#d97706) }") == []
        assert _scan(tmp_path, ".p { background:#10b981; border-color:#ef4444; outline-color:#fbbf24 }") == []
        assert _scan(tmp_path, ".t { background: var(--danger, #ef4444) }") == []
        assert _scan(tmp_path, "var m = {ok:'#10b981'};") == []

    def test_brand_island_keeps_its_own_surface_palette(self, tmp_path):
        # A file owning a --xx-* namespace is exempt, same as under DL-1.
        assert _scan(tmp_path, """
            :root { --cm-panel:#161a22; --cm-line:#262c38; --cm-text:#e7ecf5; }
            .alert { background:#ef4444 }
        """, "cm.css") == []

    def test_surface_opt_out_marker(self, tmp_path):
        assert _scan(tmp_path, "/* text-tokens: ignore — map marker palette */\n"
                               ".pin { background:#d55 }") == []

    def test_opt_out_marker_same_line_and_line_above(self, tmp_path):
        assert _scan(tmp_path, ".x { color: #34d399; } /* text-tokens: ignore — dark-only island */") == []
        assert _scan(tmp_path, "/* text-tokens: ignore — brand locked */\n.y { color: #fbbf24; }") == []


class TestTokenSetIsDerived:
    """T3's token list is read from theme.css, not duplicated in the checker.

    A hardcoded list drifts silently — `--ink-on-vivid` was added to theme.css the
    same day this checker was written. These pin the derivation, not the values.
    """

    def test_colour_tokens_are_all_present(self):
        for tok in ("accent", "accent-ink", "bg", "bg-card", "text", "text-muted",
                    "border", "success", "warning", "danger", "info", "shadow",
                    "red", "amber", "green", "blue", "purple", "ink-on-vivid"):
            assert tok in ctt._THEME_TOKENS, f"--{tok} should be guarded"

    def test_non_colour_scale_is_not_guarded(self):
        # Redefining --radius / --font / --space-* is a type/layout concern
        # (FDL §2-§3), not a readability one. T3 must not police it.
        for tok in ("radius", "font", "mono", "space-1", "fs-1", "dur-fast"):
            assert tok not in ctt._THEME_TOKENS, f"--{tok} is not a colour token"


class TestStatusPaletteIsSingleSource:
    """STATUS_HEX and the suggestion come from one map.

    They were two hand-kept copies of the same four sets, so a literal added to
    the membership test but not the suggester silently proposed var(--danger).
    """

    def test_membership_is_the_union(self):
        union = frozenset().union(*ctt.STATUS_PALETTE.values())
        assert ctt.STATUS_HEX == union

    def test_every_hex_suggests_its_own_category(self):
        for category, hexes in ctt.STATUS_PALETTE.items():
            for h in hexes:
                assert ctt._suggest(h) == ctt.SUGGEST[category], h

    def test_categorical_literals_are_deliberately_absent(self):
        # The list is empirical: a literal earns membership from how it is USED
        # here, not from its position in a colour ramp. These were measured and
        # excluded — purple is theme.css's "rare / special" accent
        # (.eos-pill-purple, a debug overlay, a PiP document), and #38bdf8 is an
        # accent too (eos-deck's speaker-b AND its style-tech --deck-accent).
        # Adding either only manufactures false positives.
        for h in ("#8b5cf6", "#a855f7", "#a78bfa", "#38bdf8"):
            assert h not in ctt.STATUS_HEX, f"{h} is categorical, not a status"

    def test_status_literals_with_observed_state_use_are_present(self):
        # The other side of the same test — each of these was found on a real
        # state in the tree (.scene-status.draft, .urg-chip.weak, a saved
        # indicator, a recording indicator), so each must stay guarded.
        for h in ("#22c55e", "#059669", "#dc2626", "#d97706", "#f85149"):
            assert h in ctt.STATUS_HEX, f"{h} is used as a status here"


class TestBrandIslandDetection:
    """The shared detector asks what a prefix *means*, not how long it is."""

    def test_private_namespace_is_an_island(self, tmp_path):
        assert _scan(tmp_path, """
            .x { --cm-panel:#161a22; --cm-line:#262c38; --cm-text:#e7ecf5; }
            .alert { background:#ef4444 }
        """, "cm.css") == []

    def test_long_prefix_is_still_private(self, tmp_path):
        # --boards-* / --series-* are 6 chars; a 4-char bound missed them.
        assert _scan(tmp_path, """
            :root { --boards-a:#111; --boards-b:#222; --boards-c:#333; }
            .alert { background:#ef4444 }
        """, "b.css") == []

    def test_namespace_outside_root_is_still_private(self, tmp_path):
        # cymcap-modifier declares its palette under a scoped selector, which a
        # :root-only rule could not see.
        assert _scan(tmp_path, """
            .wrap { --cm-a:#111; --cm-b:#222; --cm-c:#333; }
            .alert { background:#ef4444 }
        """, "cm.css") == []

    def test_global_families_do_not_make_an_island(self, tmp_path):
        # --accent-ink/-dim/-bg is theme.css's own family, not a private
        # namespace — treating it as one would exempt the file wholesale.
        hits = _scan(tmp_path, """
            .x { --accent-ink:#fff; --accent-dim:#666; --accent-bg:#eee; }
            .alert { background:#ef4444 }
        """, "a.css")
        assert [h[1] for h in hits] == ["T4"], hits


class TestRealTree:
    def test_repo_is_clean(self):
        """The tree was brought to zero on 2026-07-11 — keep it there."""
        offenders = {}
        for p in ctt.targets():
            hits = ctt.scan(p)
            if hits:
                offenders[str(p)] = hits
        assert not offenders, f"text-colour regressions: {offenders}"
