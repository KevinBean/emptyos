"""Readability-audit core (eos-readability.js) — both-direction pins.

Exercises the shared walk (window.__eosReadability.audit) against
`page.set_content()` fixture pages with mathematically-known colors, so the
suite needs no daemon page and can't drift with app content. Pins:

  - low-contrast text FAILS (dark-on-dark, incl. through an rgba card layer)
  - borderline contrast WARNS
  - tiny fonts + opacity-faded text WARN
  - text over a background-image is INFO, never a fail
  - `data-readability-ignore` subtrees and disabled controls are skipped
  - one bad CSS rule dedupes to one finding (count=N), not N findings
  - a healthy page produces zero findings

Per .claude/skills/eos-graduate-audit: a checker ships with tests pinning
both "fires on the real regression" and "silent on healthy code".
"""
from __future__ import annotations

from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "emptyos" / "web" / "static"
WALK_JS = (_STATIC / "eos-audit-walk.js").read_text(encoding="utf-8")   # shared harness, loads first
READABILITY_JS = (_STATIC / "eos-readability.js").read_text(encoding="utf-8")

# Dark page with one representative of each defect class. All colors chosen so
# the WCAG ratios land unambiguously inside the fail/warn bands:
#   #333333 on #1a1a1a  → ~1.4  (fail: < 2.5)
#   #5a5a5a on #1a1a1a  → ~2.75 (warn: 2.5–3.0)
#   #4a4a4a on rgba(255,255,255,.06) over #1a1a1a → ~1.7 (fail, via compositing)
BAD_PAGE = """
<!doctype html><html><body style="background:#1a1a1a; margin:0; font-size:16px">
  <p id="fail-contrast" style="color:#333333">FAILCONTRAST body text</p>
  <p id="warn-contrast" style="color:#5a5a5a">WARNCONTRAST body text</p>
  <button id="emoji-btn" style="color:#333333; background:transparent; border:0">😊</button>
  <span id="icon-only" style="color:#333333">✎</span>
  <p id="tiny" style="color:#eeeeee; font-size:8px">TINYTEXT fine print</p>
  <p id="faded" style="color:#ffffff; opacity:0.15">FADEDTEXT ghost label</p>
  <div style="background:rgba(255,255,255,0.06); padding:8px">
    <span id="card-text" style="color:#4a4a4a">CARDTEXT on translucent card</span>
  </div>
  <div data-readability-ignore>
    <p id="ignored" style="color:#333333">IGNOREDTEXT deliberately dim</p>
  </div>
  <button id="dis" disabled style="color:#333333; background:transparent; border:0">DISABLEDTEXT</button>
  <div class="dupwrap">
    <span class="dup" style="color:#303030; display:block">DUPLICATE one</span>
    <span class="dup" style="color:#303030; display:block">DUPLICATE two</span>
    <span class="dup" style="color:#303030; display:block">DUPLICATE three</span>
    <span class="dup" style="color:#303030; display:block">DUPLICATE four</span>
    <span class="dup" style="color:#303030; display:block">DUPLICATE five</span>
  </div>
  <div style="background-image:linear-gradient(#000000,#111111); padding:8px">
    <p id="grad" style="color:#555555">GRADTEXT over gradient</p>
  </div>
  <div style="background:color-mix(in srgb, #1d9463 90%, transparent); padding:8px">
    <span id="mix-ok" style="color:#ffffff">MIXTEXT on color-mix ground</span>
  </div>
</body></html>
"""

# Healthy page — token-style solid colors at comfortable ratios.
GOOD_PAGE = """
<!doctype html><html><body style="background:#1a1a1a; margin:0; font-size:16px">
  <h1 style="color:#ffffff">GOODHEADING</h1>
  <p style="color:#eeeeee">GOODBODY normal paragraph text</p>
  <div style="background:#242424; padding:12px">
    <span style="color:#dddddd">GOODCARD card text</span>
  </div>
  <p style="color:#9a9a9a; font-size:12px">GOODMUTED small but legible caption</p>
</body></html>
"""


def _audit(page, html: str) -> dict:
    page.set_content(html, wait_until="domcontentloaded")
    page.add_script_tag(content=WALK_JS)
    page.add_script_tag(content=READABILITY_JS)
    return page.evaluate("() => window.__eosReadability.audit()")


def _by_sample(result: dict, token: str) -> list[dict]:
    return [f for f in result["findings"] if token in (f.get("sample") or "")]


@pytest.mark.interactive
class TestReadabilityCore:
    def test_bad_page_findings(self, page):
        result = _audit(page, BAD_PAGE)

        fail = _by_sample(result, "FAILCONTRAST")
        assert fail and fail[0]["type"] == "contrast" and fail[0]["severity"] == "fail", fail
        assert fail[0]["ratio"] < 2.5

        warn = _by_sample(result, "WARNCONTRAST")
        assert warn and warn[0]["type"] == "contrast" and warn[0]["severity"] == "warn", warn
        assert 2.5 <= warn[0]["ratio"] < 3.0

        tiny = _by_sample(result, "TINYTEXT")
        assert tiny and tiny[0]["type"] == "tiny-font" and tiny[0]["severity"] == "warn", tiny

        faded = _by_sample(result, "FADEDTEXT")
        assert faded and faded[0]["type"] == "faded" and faded[0]["severity"] == "warn", faded

        card = _by_sample(result, "CARDTEXT")
        assert card and card[0]["severity"] == "fail", card
        # Effective bg must be the COMPOSITED card color, not the raw body bg.
        assert card[0]["bg"] != "#1a1a1a"

    def test_opt_out_and_disabled_skipped(self, page):
        result = _audit(page, BAD_PAGE)
        assert not _by_sample(result, "IGNOREDTEXT"), "data-readability-ignore subtree must be exempt"
        assert not _by_sample(result, "DISABLEDTEXT"), "disabled controls are WCAG-exempt"

    def test_glyph_only_text_skipped(self, page):
        # Emoji render as color bitmaps regardless of `color`; symbol-only
        # affordances are dim by design. Neither may produce a finding.
        result = _audit(page, BAD_PAGE)
        assert not _by_sample(result, "😊"), "emoji must not be contrast-checked"
        assert not _by_sample(result, "✎"), "symbol-only glyphs must not be contrast-checked"
        assert result["stats"]["glyphOnly"] >= 2

    def test_bg_image_is_info_never_fail(self, page):
        result = _audit(page, BAD_PAGE)
        grad = _by_sample(result, "GRADTEXT")
        assert grad and grad[0]["type"] == "bg-image" and grad[0]["severity"] == "info", grad
        assert result["stats"]["bgImage"] >= 1

    def test_color_mix_background_is_measured(self, page):
        # color-mix() computes to color(srgb …); the parser must composite it
        # (white on 90% #1d9463 over dark ≈ 4.4:1 → clean), never drop the
        # layer and measure against the body instead.
        result = _audit(page, BAD_PAGE)
        assert not _by_sample(result, "MIXTEXT"), _by_sample(result, "MIXTEXT")

    def test_dedupe_collapses_repeated_rule(self, page):
        result = _audit(page, BAD_PAGE)
        dups = _by_sample(result, "DUPLICATE")
        assert len(dups) == 1, f"expected one grouped finding, got {len(dups)}"
        assert dups[0]["count"] == 5

    def test_good_page_silent(self, page):
        result = _audit(page, GOOD_PAGE)
        assert result["findings"] == [], result["findings"]
        assert result["stats"]["scanned"] >= 4  # it did actually look at the text

    def test_mark_mode_stamps_attributes(self, page):
        page.set_content(BAD_PAGE, wait_until="domcontentloaded")
        page.add_script_tag(content=WALK_JS)
        page.add_script_tag(content=READABILITY_JS)
        page.evaluate("() => window.__eosReadability.audit({mark: true})")
        sev = page.evaluate(
            "() => document.getElementById('fail-contrast').getAttribute('data-eos-readability')"
        )
        assert sev == "fail"
        # clearMarks removes every stamp
        page.evaluate("() => window.__eosReadability.clearMarks()")
        assert page.evaluate("() => document.querySelectorAll('[data-eos-readability]').length") == 0
