"""Unit tests for the DESIGN.md token linter (scripts/check-design-md.py).

Pure — no daemon, no kernel. Validates the three error classes (unresolved
refs, ref cycles, malformed hex) plus the clean-on-real-DESIGN.md contract.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_design_md", ROOT / "scripts" / "check-design-md.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
lint_design_md = _mod.lint_design_md
lint_file = _mod.lint_file


def test_clean_design_md_has_no_errors():
    assert lint_file(ROOT / "DESIGN.md") == []


def test_resolved_refs_pass():
    data = {
        "colors": {"accent": "#6c5ce7", "ink": "#ffffff"},
        "rounded": {"base": "8px"},
        "components": {
            "button": {
                "backgroundColor": "{colors.accent}",
                "textColor": "{colors.ink}",
                "rounded": "{rounded.base}",
            }
        },
    }
    assert lint_design_md(data) == []


def test_unresolved_ref_flagged():
    data = {"colors": {"accent": "#6c5ce7"},
            "components": {"button": {"textColor": "{colors.missing}"}}}
    errs = lint_design_md(data)
    assert any("unresolved ref {colors.missing}" in e for e in errs)


def test_subtree_ref_resolves():
    # {typography.body} points at a map, not a leaf — still a valid target.
    data = {"typography": {"body": {"fontSize": "16px"}},
            "components": {"input": {"typography": "{typography.body}"}}}
    assert lint_design_md(data) == []


def test_ref_cycle_flagged():
    data = {"colors": {"a": "{colors.b}", "b": "{colors.a}"}}
    errs = lint_design_md(data)
    assert any("ref cycle" in e for e in errs)


def test_malformed_hex_flagged():
    data = {"colors": {"bad": "#12g", "ok": "#abc", "okk": "#aabbcc"}}
    errs = lint_design_md(data)
    assert any("malformed hex" in e and "#12g" in e for e in errs)
    assert not any("#abc" in e or "#aabbcc" in e for e in errs)


def test_nonhash_colors_not_flagged():
    # transparent / rgb() / named colors / refs are left alone (zero FP).
    data = {"colors": {"a": "transparent", "b": "rgb(1,2,3)",
                       "c": "rebeccapurple", "d": "{colors.a}"}}
    assert not any("malformed hex" in e for e in lint_design_md(data))
