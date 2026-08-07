"""Both-direction pins for the [T] tap-fail signal in check-ios-safe-area.py.

Per .claude/skills/eos-graduate-audit: a graduated checker ships with tests
pinning BOTH "fires on the real regression" and "silent on healthy code".

The [T] signal over-fired in two ways (found 2026-08-05):

  1. It accepted only `cursor: pointer`, so a lightbox backdrop correctly
     marked `cursor: zoom-out` read as a tap-fail.
  2. It collected selector tokens from classes only, so an overlay addressed
     by id — the normal way to address a full-screen overlay — could never
     satisfy it no matter what cursor it declared.

Complementary to tests/test_sys_ios_layout.py, which checks the CSS the daemon
actually serves; this file checks the scanner's own judgment.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))  # the script imports check_base
SCRIPT = REPO / "scripts" / "check-ios-safe-area.py"
_spec = importlib.util.spec_from_file_location("check_ios_safe_area", SCRIPT)
cisa = importlib.util.module_from_spec(_spec)
sys.modules["check_ios_safe_area"] = cisa  # @dataclass resolves via sys.modules
_spec.loader.exec_module(cisa)


def _codes(tmp_path: Path, html: str) -> list[str]:
    """Scan one synthetic page, resolving cursor tokens from the page itself."""
    page = tmp_path / "index.html"
    page.write_text(html, encoding="utf-8")
    pointer = cisa._collect_pointer_classes([page])
    return [f.code for f in cisa.scan_file(page, pointer)]


class TestTapFailFiresOnRegression:
    def test_bare_div_onclick_flagged(self, tmp_path):
        html = "<style>.plain{padding:4px}</style><div class='plain' onclick='go()'>x</div>"
        assert "T" in _codes(tmp_path, html)

    def test_div_with_no_class_or_id_flagged(self, tmp_path):
        assert "T" in _codes(tmp_path, "<div onclick='go()'>x</div>")

    def test_non_interactive_cursor_still_flagged(self, tmp_path):
        """`cursor: default` is not an affordance."""
        html = "<style>#ov{cursor:default}</style><div id='ov' onclick='go()'>x</div>"
        assert "T" in _codes(tmp_path, html)


class TestTapFailSilentOnHealthy:
    def test_class_with_pointer_cursor(self, tmp_path):
        html = "<style>.mnu{cursor:pointer}</style><div class='mnu' onclick='go()'>x</div>"
        assert "T" not in _codes(tmp_path, html)

    def test_inline_pointer_cursor(self, tmp_path):
        assert "T" not in _codes(tmp_path, "<div onclick='go()' style='cursor:pointer'>x</div>")

    def test_id_selector_with_interactive_cursor(self, tmp_path):
        """A full-screen overlay is addressed by id, not class."""
        html = "<style>#lightbox{cursor:zoom-out}</style><div id='lightbox' onclick='go()'>x</div>"
        assert "T" not in _codes(tmp_path, html)

    def test_grab_cursor_accepted(self, tmp_path):
        html = "<style>.canvas{cursor:grab}</style><div class='canvas' onclick='go()'>x</div>"
        assert "T" not in _codes(tmp_path, html)

    def test_native_button_never_flagged(self, tmp_path):
        assert "T" not in _codes(tmp_path, "<button onclick='go()'>x</button>")


class TestViewportSignal:
    def test_100vh_flagged(self, tmp_path):
        assert "V" in _codes(tmp_path, "<style>.a{height:100vh}</style>")

    def test_100dvh_clean(self, tmp_path):
        assert "V" not in _codes(tmp_path, "<style>.a{height:100dvh}</style>")


class TestOptOut:
    def test_opt_out_suppresses_named_code(self, tmp_path):
        html = "<style>.a{height:100vh; /* eos-ios-ok: V — fixed-size kiosk shell */}</style>"
        assert "V" not in _codes(tmp_path, html)

    def test_opt_out_does_not_suppress_other_codes(self, tmp_path):
        html = (
            "<style>.a{position:fixed;top:0;bottom:0;height:100vh;"
            "/* eos-ios-ok: V — only the viewport unit is intentional */}</style>"
        )
        codes = _codes(tmp_path, html)
        assert "V" not in codes
        assert "N" in codes or "H" in codes
