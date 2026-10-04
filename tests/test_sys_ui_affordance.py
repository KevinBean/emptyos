"""Affordance-audit core (eos-ui-affordance.js) — both-direction pins.

Exercises the shared walk (window.__eosAffordance.audit) against
`page.set_content()` fixture pages with deliberately-constructed layouts, so
the suite needs no daemon page and can't drift with app content. Pins:

  - content clipped by overflow:hidden with no scroll affordance is FOUND
  - the confident "declared scroll region clamped off by a clipping ancestor"
    (broken height chain) escalates to FAIL — the only gating signal
  - a properly-bounded scroll container (overflow:auto that CAN scroll) is silent
  - a roleless one-of-N button row (tab-like) WARNS
  - a real tablist (role=tablist/tab) and a toolbar of aria-pressed toggles are
    both silent — the two legitimate patterns the detector must not flag
  - `data-affordance-ignore` subtrees are exempt
  - a healthy page produces zero findings

Per .claude/skills/eos-graduate-audit: a checker ships with tests pinning both
"fires on the real regression" and "silent on healthy code". The buttons-as-tabs
detector never emits `fail` (a11y-semantic advice must not gate — audits.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "emptyos" / "web" / "static"
WALK_JS = (_STATIC / "eos-audit-walk.js").read_text(encoding="utf-8")   # shared harness, loads first
AFFORDANCE_JS = (_STATIC / "eos-ui-affordance.js").read_text(encoding="utf-8")

# Every case is sized so the geometry is unambiguous.
BAD_PAGE = """
<!doctype html><html><body style="margin:0">
  <!-- clipped content, no scroll affordance (warn) -->
  <div id="clipped" style="height:60px; overflow:hidden; border:1px solid #ccc">
    <p style="margin:0; height:300px">CLIPPEDCONTENT unreachable tail</p>
  </div>

  <!-- BROKEN HEIGHT CHAIN (fail): the inner scroller declared overflow:auto but
       got no bounded height, grew to content, and the ancestor clips it off. -->
  <div id="broken" style="height:80px; overflow:hidden; border:1px solid #ccc">
    <div id="inner-scroll" style="overflow-y:auto">
      <p style="margin:0; height:400px">BROKENSCROLL clamped-off scroll region</p>
    </div>
  </div>

  <!-- healthy scroller: bounded height, overflow:auto, CAN scroll (silent) -->
  <div id="ok-scroll" style="height:80px; overflow-y:auto; border:1px solid #ccc">
    <p style="margin:0; height:400px">OKSCROLL reachable by scrolling</p>
  </div>

  <!-- roleless one-of-N button row => tab-like (warn) -->
  <div id="fake-tabs" style="display:flex">
    <button class="tab active">FAKETABONE</button>
    <button class="tab">FAKETABTWO</button>
    <button class="tab">FAKETABTHREE</button>
  </div>

  <!-- a REAL tablist (silent) -->
  <div id="real-tabs" role="tablist" style="display:flex">
    <button role="tab" class="active" aria-selected="true">REALTABONE</button>
    <button role="tab" aria-selected="false">REALTABTWO</button>
  </div>

  <!-- toolbar of independent toggles: aria-pressed => NOT tabs (silent) -->
  <div id="toolbar" style="display:flex">
    <button class="active" aria-pressed="true">TOGGLEONE</button>
    <button aria-pressed="false">TOGGLETWO</button>
  </div>

  <!-- opt-out subtree (silent) -->
  <div data-affordance-ignore>
    <div id="ignored" style="height:60px; overflow:hidden">
      <p style="margin:0; height:300px">IGNOREDCLIP deliberately clipped</p>
    </div>
  </div>
</body></html>
"""

GOOD_PAGE = """
<!doctype html><html><body style="margin:0">
  <div style="height:100px; overflow-y:auto"><p style="margin:0; height:400px">HEALTHYSCROLL</p></div>
  <div style="height:100px; overflow:hidden"><p style="margin:0; height:40px">HEALTHYFITS</p></div>
  <div role="tablist" style="display:flex">
    <button role="tab" class="active" aria-selected="true">HEALTHYTABONE</button>
    <button role="tab" aria-selected="false">HEALTHYTABTWO</button>
  </div>
  <div style="display:flex"><button>PLAINONE</button><button>PLAINTWO</button></div>
</body></html>
"""


def _audit(page, html: str) -> dict:
    page.set_content(html, wait_until="domcontentloaded")
    page.add_script_tag(content=WALK_JS)
    page.add_script_tag(content=AFFORDANCE_JS)
    return page.evaluate("() => window.__eosAffordance.audit()")


def _by_sel(result: dict, needle: str) -> list[dict]:
    return [f for f in result["findings"] if needle in (f.get("sel") or "")]


def _by_type(result: dict, t: str) -> list[dict]:
    return [f for f in result["findings"] if f["type"] == t]


@pytest.mark.interactive
class TestAffordanceCore:
    def test_clipped_content_found(self, page):
        result = _audit(page, BAD_PAGE)
        clipped = _by_sel(result, "#clipped")
        assert clipped, "content clipped by overflow:hidden must be found"
        assert clipped[0]["type"] == "overflow-clip"

    def test_broken_height_chain_is_the_gating_fail(self, page):
        # The ONLY confident signal: a declared scroll region clamped off by a
        # clipping ancestor. This is the CAD-class bug and the one that gates.
        result = _audit(page, BAD_PAGE)
        broken = _by_sel(result, "#broken")
        assert broken and broken[0]["severity"] == "fail", broken
        assert "clamped" in broken[0]["detail"]

    def test_bare_clip_is_advisory_not_fail(self, page):
        # A collapsed accordion legitimately clips — bare clipping must warn, not gate.
        result = _audit(page, BAD_PAGE)
        clipped = _by_sel(result, "#clipped")
        assert clipped[0]["severity"] == "warn", clipped

    def test_healthy_scroller_silent(self, page):
        result = _audit(page, BAD_PAGE)
        assert not _by_sel(result, "#ok-scroll"), "a bounded overflow:auto scroller is correct"

    def test_roleless_tab_row_warns(self, page):
        result = _audit(page, BAD_PAGE)
        fake = _by_sel(result, "#fake-tabs")
        assert fake and fake[0]["type"] == "buttons-as-tabs", fake
        assert fake[0]["severity"] == "warn", "a11y-semantic advice must never gate"

    def test_real_tablist_and_toggle_toolbar_silent(self, page):
        result = _audit(page, BAD_PAGE)
        assert not _by_sel(result, "#real-tabs"), "role=tablist is the correct pattern"
        assert not _by_sel(result, "#toolbar"), "aria-pressed toggles are a toolbar, not tabs"

    def test_opt_out_subtree_exempt(self, page):
        result = _audit(page, BAD_PAGE)
        assert not _by_sel(result, "#ignored"), "data-affordance-ignore subtree must be exempt"

    def test_buttons_as_tabs_never_fails(self, page):
        result = _audit(page, BAD_PAGE)
        assert all(f["severity"] == "warn" for f in _by_type(result, "buttons-as-tabs"))

    def test_good_page_silent(self, page):
        result = _audit(page, GOOD_PAGE)
        assert result["findings"] == [], result["findings"]
        assert result["stats"]["scanned"] >= 4   # it did actually look at the DOM

    def test_mark_mode_stamps_and_clears(self, page):
        page.set_content(BAD_PAGE, wait_until="domcontentloaded")
        page.add_script_tag(content=WALK_JS)
        page.add_script_tag(content=AFFORDANCE_JS)
        page.evaluate("() => window.__eosAffordance.audit({mark: true})")
        sev = page.evaluate("() => document.getElementById('broken').getAttribute('data-eos-affordance')")
        assert sev == "fail"
        page.evaluate("() => window.__eosAffordance.clearMarks()")
        assert page.evaluate("() => document.querySelectorAll('[data-eos-affordance]').length") == 0
