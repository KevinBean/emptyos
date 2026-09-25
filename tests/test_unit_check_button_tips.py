"""Pins `scripts/check-button-tips.py` in both directions.

The check was narrowed on 2026-09-06 (audit F7). It used to flag every button
without a `title=`, which fired on **87% of apps** — the shape
`.claude/rules/audits.md` says gets a check disabled within a month. Measuring
split it:

  * 1971 buttons with a visible label, whose label already IS their accessible
    name. `title` there is a redundant hover tooltip, not a defect.
  * 88 icon-only buttons with no name at all, where a screen reader announces
    "button" and nothing more.

Only the second is a finding now. The silent-direction cases below are each a
false-positive class the old rule produced — `aria_label_is_a_name` in
particular pins 37 buttons that were already labelled correctly and flagged
anyway.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_button_tips", ROOT / "scripts" / "check-button-tips.py"
)
cbt = importlib.util.module_from_spec(_SPEC)
sys.modules["check_button_tips"] = cbt
_SPEC.loader.exec_module(cbt)


def scan(tmp_path, html: str):
    """Run the real scan_file over one synthetic page."""
    p = tmp_path / "index.html"
    p.write_text(html, encoding="utf-8")
    findings, dynamic, no_title = cbt.scan_file(p)
    return findings, dynamic, no_title


# ── fires ────────────────────────────────────────────────────────────────────

def test_icon_only_button_with_no_name_is_a_finding(tmp_path):
    f, _, _ = scan(tmp_path, '<button class="close" onclick="x()">&times;</button>')
    assert len(f) == 1, "an icon-only button with no accessible name must be reported"


def test_emoji_only_button_is_a_finding(tmp_path):
    f, _, _ = scan(tmp_path, '<button onclick="s()">⚙</button>')
    assert len(f) == 1


def test_button_whose_glyph_is_wrapped_in_a_span(tmp_path):
    """<span> around the glyph must not read as a label."""
    f, _, _ = scan(tmp_path, '<button onclick="x()"><span>&times;</span></button>')
    assert len(f) == 1


# ── silent: every false-positive class the old rule produced ─────────────────

def test_aria_label_is_a_name(tmp_path):
    """The regression that motivated the narrowing: 37 icon buttons were
    correctly labelled with aria-label and flagged anyway."""
    f, _, _ = scan(tmp_path, '<button aria-label="Close" onclick="x()">&times;</button>')
    assert f == []


def test_title_is_still_a_name(tmp_path):
    f, _, _ = scan(tmp_path, '<button title="Close" onclick="x()">&times;</button>')
    assert f == []


def test_visible_label_is_its_own_name(tmp_path):
    """1971 of the old rule's 2069 findings. A labelled button needs no title."""
    f, _, _ = scan(tmp_path, '<button class="eos-btn" onclick="r()">Refresh</button>')
    assert f == []


def test_label_inside_a_nested_tag_still_counts(tmp_path):
    f, _, _ = scan(tmp_path, '<button onclick="r()"><span class="i">Refresh</span></button>')
    assert f == []


def test_dynamically_assembled_button_is_never_a_finding(tmp_path):
    f, dynamic, _ = scan(tmp_path, "h += '<button ' + attrs + '>x</button>';")
    assert f == []
    assert dynamic >= 1


# ── the advisory stat ────────────────────────────────────────────────────────

def test_no_title_stat_still_counts_labelled_buttons(tmp_path):
    """The broad number survives as an advisory so nothing is lost — but it
    must not become a finding."""
    f, _, no_title = scan(tmp_path, '<button onclick="r()">Refresh</button>')
    assert f == []
    assert no_title == 1


def test_named_button_is_not_in_the_advisory_stat(tmp_path):
    _, _, no_title = scan(tmp_path, '<button title="Reload" onclick="r()">Refresh</button>')
    assert no_title == 0


# ── live tree ────────────────────────────────────────────────────────────────

def test_live_tree_signal_stays_narrow():
    """Pin the ratio, not the count.

    Asserting an exact number fails on other people's correct work. What must
    hold is that the finding class stays far smaller than the advisory one — if
    they converge, the narrowing has been undone and the check is back to
    firing on the whole tree.
    """
    import subprocess, json
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-button-tips.py"), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    ).stdout
    data = json.loads(out)["data"]
    assert data["no_title_advisory"] > 500, "advisory stat should still count the broad class"
    assert data["total"] < data["no_title_advisory"] / 5, (
        f"findings ({data['total']}) approaching the advisory class "
        f"({data['no_title_advisory']}) — the narrowing has regressed"
    )
