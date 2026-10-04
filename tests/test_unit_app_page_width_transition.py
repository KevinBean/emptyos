"""Pin FRONTEND-DESIGN-LANGUAGE §5 "never animate width" across app pages.

The 2026-09-12 audit fixed the shared `.eos-bar-fill { transition:width 0.5s }`
(pinned by `test_unit_eos_components_design_language.py`), but tracked app
pages still carried their own copies. The 2026-10-04 audit removed them: bars
rebuilt through `innerHTML` never animated anyway (no transition fires on first
paint), bars updated in place now snap as `.eos-bar-fill` does, and the three
where the motion carried meaning (gesture's per-frame confidence bars,
soundcheck's answer countdown) became `transform: scaleX` transitions.

No brand-island exemption: §12 keeps "Motion is still a whisper —
transform/opacity only" locked on islands, and `eos-ui: exempt` opts a page out
of the shared *bundle*, not out of the design language.

Scope is deliberately `width` only. `transition: all` (65 hover rules on
2026-10-04) and height/top/left are the same §5 class but a separate pass —
gating them here would turn this pin into a migration. Out of reach: a width
hidden behind a custom property (`transition: var(--t)`).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scanner_lib import page_files  # noqa: E402

# CSS: the property (optionally vendor-prefixed, never a `--custom-` name)
# whose value — up to `;`/`}`, across lines — names `width` (not max-/min-).
CSS_WIDTH = re.compile(
    r"(?<![\w-])(?:-webkit-|-moz-)?transition(?:-property)?\s*:[^;}\"'`]*?(?<![\w-])width\b",
    re.I,
)
# JS: `el.style.transition = 'width ' + ms + 'ms'` / `.transitionProperty = 'width'`.
JS_WIDTH = re.compile(
    r"\.style\.(?:webkit)?transition(?:property)?\s*=\s*[^;\n]*?(?<![\w-])width\b",
    re.I,
)
SKIP = ("/personal/", "/_retired/", "/legacy/", ".legacy.", "/dist/")


def _strip_comments(text: str) -> str:
    # CSS block comments only (`//` would eat `https://`); keep newlines so
    # reported line numbers stay real.
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def scan_text(text: str) -> list[int]:
    """Line numbers where a width transition starts."""
    src = _strip_comments(text)
    hits = {src.count("\n", 0, m.start()) + 1
            for rx in (CSS_WIDTH, JS_WIDTH) for m in rx.finditer(src)}
    return sorted(hits)


def _tracked_pages() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "apps"], cwd=ROOT, capture_output=True, text=True)
    tracked = set(out.stdout.splitlines())
    pages = page_files(ROOT, suffixes=(".html", ".js", ".css"))
    return [p for p in pages
            if p.relative_to(ROOT).as_posix() in tracked
            and not any(s in p.as_posix() for s in SKIP)]


def test_no_tracked_app_page_animates_width():
    pages = _tracked_pages()
    # Vacuity guard: an empty walk passes every assertion below.
    assert len(pages) > 200, f"walked only {len(pages)} page files"
    offenders = []
    for p in pages:
        for line in scan_text(p.read_text(encoding="utf-8", errors="replace")):
            offenders.append(f"{p.relative_to(ROOT).as_posix()}:{line}")
    assert not offenders, (
        "§5 forbids animating width — drop the transition (width updates land "
        "instantly, as .eos-bar-fill does) or animate transform: scaleX: "
        + ", ".join(offenders))


def test_scan_catches_each_width_shape():
    assert scan_text(".fill { height:100%; transition:width 0.5s }") == [1]
    assert scan_text(".fill { transition: opacity .2s, width .3s ease }") == [1]
    assert scan_text(".fill { transition-property: width; }") == [1]
    assert scan_text(".fill { -webkit-transition: width .3s }") == [1]
    assert scan_text(".fill { TRANSITION: WIDTH .3s }") == [1]
    assert scan_text(".fill {\n  transition:\n    width .3s;\n}") == [2]
    assert scan_text('<div style="width:40%;transition:width .4s ease"></div>') == [1]
    assert scan_text("bar.style.transition = 'width ' + ms + 'ms linear';") == [1]
    assert scan_text("el.style.transitionProperty = 'width';") == [1]


def test_scan_spares_legal_transitions():
    assert scan_text(".fill { transition: transform .25s ease-out }") == []
    assert scan_text(".panel { transition: max-width .2s, min-width .2s }") == []
    assert scan_text("/* transition: width 0.5s was here */ .fill { height:100% }") == []
    assert scan_text(":root { --bar-transition: width .3s }") == []
    assert scan_text("bar.style.transition = 'transform ' + ms + 'ms'; bar.style.width = '0%';") == []
    # The value stops at `;` — a later width declaration is not the transition's.
    assert scan_text(".fill { transition: opacity .2s; width: 40% }") == []


def test_brand_island_is_not_exempt():
    # §12: motion rules stay locked on islands; eos-ui: exempt is a bundle opt-out.
    page = ("<!-- eos-ui: exempt — full-bleed canvas -->\n"
            ":root { --p-bg:#000; --p-ink:#fff; --p-rule:#333 }\n"
            ".fill { transition:width .3s }")
    assert scan_text(page) == [3]
