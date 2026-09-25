"""Unit tests for generated-artifact identity + provenance (offline, no daemon).

Covers the two pure pieces that make a viz/designer artifact a connected vault
citizen rather than an isolated node:

* ``artifact_title`` — the deterministic human label that replaces a hash
  heading, so search / boards / the vault graph show a name.
* ``resolve_pattern_examples_detail`` — which pattern slugs *actually* reached
  the system prompt, which is what a record may honestly write to ``related:``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from emptyos.sdk.html_artifact import artifact_title, extract_svg
from emptyos.sdk.pattern_examples import (
    resolve_pattern_examples,
    resolve_pattern_examples_detail,
)


# ── artifact_title ───────────────────────────────────────────────────────


def test_title_takes_the_first_clause():
    prompt = (
        "Simple engineering elevation schematic of a 132 kV cable rising to a "
        "cable sealing end. From the top: a CSE box; 890 mm down to Cleat 1."
    )
    assert artifact_title(prompt) == (
        "Simple engineering elevation schematic of a 132 kV cable rising to a…"
    )


def test_title_short_prompt_is_returned_whole_without_ellipsis():
    assert artifact_title("A public landing page for English Academy") == (
        "A public landing page for English Academy"
    )


def test_title_cuts_at_the_first_newline_then_collapses_runs():
    """A brief's line 1 is its subject — cut there, don't run the lines together.

    Real vault prompts look like "Create a slide deck titled: X\\nBrief: …",
    so collapsing whitespace before cutting would swallow the boundary and
    truncate mid-sentence instead.
    """
    assert artifact_title("Create a  deck titled:  Blob Storage\nBrief: a long tail") == (
        "Create a deck titled: Blob Storage"
    )


def test_title_does_not_cut_at_a_decimal_point():
    """A `.` between digits is a decimal, not a sentence end.

    Engineering briefs are dense with them ("11/0.415 kV", "R2.65 m"), and
    cutting there yields "utility incoming -> 11/0", which reads as a truncated
    record rather than a naming choice.
    """
    out = artifact_title("11 kV single-line: utility incoming to 11/0.415 kV transformer")
    assert "11/0.415 kV" in out
    assert artifact_title("A bend of R2.65 m at 0.6/1 kV") == "A bend of R2.65 m at 0.6/1 kV"


def test_title_still_cuts_at_a_real_sentence_end():
    assert artifact_title("Elevation schematic. From the top: a CSE box.") == (
        "Elevation schematic"
    )


def test_title_strips_a_stray_edge_quote():
    """Frontmatter parsing strips one quote layer, so a brief authored as
    '...' inside "..." keeps an inner quote that would head the title."""
    assert artifact_title("'Render a Gantt chart") == "Render a Gantt chart"
    assert artifact_title('"Render a Gantt chart"') == "Render a Gantt chart"


def test_title_truncates_on_a_word_boundary():
    out = artifact_title("alpha bravo charlie delta echo foxtrot golf", limit=20)
    assert out.endswith("…")
    # The cut never splits a word.
    assert all(w in {"alpha", "bravo", "charlie", "delta", "…"} for w in out.rstrip("…").split())
    assert len(out) <= 21


def test_title_hard_slices_a_single_long_token():
    """No space to cut on — a hard slice beats returning the whole 200 chars."""
    out = artifact_title("x" * 200, limit=30)
    assert out == "x" * 30 + "…"


def test_title_falls_back_when_prompt_is_empty():
    assert artifact_title("") == ""
    assert artifact_title("   ", fallback="Viz artifact ab12") == "Viz artifact ab12"
    assert artifact_title(None, fallback="Designer page cd34") == "Designer page cd34"


# ── pattern provenance ───────────────────────────────────────────────────


class _StubApp:
    def __init__(self, notes: dict):
        self._notes = notes  # {slug: (title, body)}
        self.vault_root = Path("/nonexistent-vault-root")

    async def call_app(self, app: str, method: str, **kw):
        if app == "kb" and method == "get_note":
            slug = kw.get("slug")
            if slug in self._notes:
                title, body = self._notes[slug]
                return {"body": body, "properties": {"title": title}}
            return {"error": "not found", "slug": slug}
        return None


_SVG_NOTE = ("House style", "# House style\n\n```svg\n<svg viewBox=\"0 0 860 400\"/>\n```\n")
_PY_ONLY = ("Python only", "# Python only\n\n```python\nprint('wrong language')\n```\n")


def _run(coro):
    return asyncio.run(coro)


def test_detail_reports_only_slugs_whose_code_landed():
    app = _StubApp({"house-style": _SVG_NOTE})
    block, resolved = _run(
        resolve_pattern_examples_detail(app, ["house-style"], langs={"svg"})
    )
    assert "<svg viewBox" in block
    assert resolved == ["house-style"]


def test_detail_omits_an_unresolvable_slug():
    """A requested-but-missing pattern must not be recorded as provenance."""
    app = _StubApp({"house-style": _SVG_NOTE})
    block, resolved = _run(
        resolve_pattern_examples_detail(app, ["house-style", "ghost"], langs={"svg"})
    )
    assert resolved == ["house-style"]
    assert "ghost" not in block


def test_detail_omits_a_note_whose_fences_are_the_wrong_language():
    """Resolved but contributing nothing to the prompt is still not provenance."""
    app = _StubApp({"py-only": _PY_ONLY})
    block, resolved = _run(
        resolve_pattern_examples_detail(app, ["py-only"], langs={"svg"})
    )
    assert block == ""
    assert resolved == []


def test_detail_empty_request_is_empty():
    app = _StubApp({})
    assert _run(resolve_pattern_examples_detail(app, [], langs={"svg"})) == ("", [])
    assert _run(resolve_pattern_examples_detail(app, None, langs={"svg"})) == ("", [])


def test_legacy_wrapper_still_returns_just_the_block():
    """The 3 existing callers (viz/cad/robot-modeller) see no signature change."""
    app = _StubApp({"house-style": _SVG_NOTE})
    block = _run(resolve_pattern_examples(app, ["house-style"], langs={"svg"}))
    assert isinstance(block, str)
    assert "<svg viewBox" in block


# ── extract_svg ──────────────────────────────────────────────────────────
#
# Every assertion here pins a failure that is *visual*, not structural: the
# naive substring grab yields a well-formed SVG that renders as a black blob
# (paths lose `fill:none` and fill solid) or does not render at all. Nothing
# short of these checks distinguishes it from the real diagram.

_ARTIFACT = """<!doctype html>
<html><head><style>
:root{ --bg:#f4f7fb; --cable:#2f3d4d; }
html,body{ background:var(--bg); }
.cable{ fill:none; stroke:var(--cable); stroke-width:9; }
</style></head>
<body><div class="wrap">
<svg viewBox="0 0 1200 760" role="img" aria-label="A cable rise">
  <path class="cable" d="M100 500 L600 500 L600 100"/>
</svg>
</div></body></html>
"""


def test_extract_svg_adds_the_xmlns_a_standalone_file_needs():
    """Inline SVG in HTML needs no namespace; a bare .svg without it is blank."""
    out = extract_svg(_ARTIFACT)
    assert out is not None
    assert 'xmlns="http://www.w3.org/2000/svg"' in out.split(">", 1)[0]


def test_extract_svg_inlines_the_page_css_the_shapes_depend_on():
    """`.cable{fill:none}` lives in the page <style>; lose it and the path fills black."""
    out = extract_svg(_ARTIFACT)
    assert ".cable{ fill:none;" in out
    # Custom properties come too — `:root` resolves to the <svg> element.
    assert "--cable:#2f3d4d" in out


def test_extract_svg_paints_a_background():
    """The canvas lives on html,body and does not travel; without a rect the
    figure is transparent and a dark palette lands invisibly on a dark note."""
    out = extract_svg(_ARTIFACT)
    assert '<rect width="100%" height="100%" fill="var(--bg, #ffffff)"/>' in out


def test_extract_svg_background_is_optional():
    out = extract_svg(_ARTIFACT, background=None)
    assert "<rect" not in out
    assert ".cable{ fill:none;" in out  # styles still inlined


def test_extract_svg_keeps_the_original_viewbox_and_content():
    out = extract_svg(_ARTIFACT)
    assert 'viewBox="0 0 1200 760"' in out
    assert 'd="M100 500 L600 500 L600 100"' in out
    assert out.rstrip().endswith("</svg>")


def test_extract_svg_is_depth_aware_over_a_nested_svg():
    """A non-greedy regex would stop at the inner </svg> and emit an unclosed file."""
    html = (
        "<html><body><svg viewBox='0 0 10 10'>"
        "<svg viewBox='0 0 2 2'><rect/></svg>"
        "<circle id='outer'/></svg></body></html>"
    )
    out = extract_svg(html, background=None)
    assert out.count("<svg") == 2 and out.count("</svg") == 2
    assert "outer" in out  # content after the inner svg survived


def test_extract_svg_does_not_duplicate_an_svg_internal_style():
    html = "<html><head><style>.a{fill:red}</style></head><body>" \
           "<svg viewBox='0 0 4 4'><style>.b{fill:blue}</style><rect class='b'/></svg></body></html>"
    out = extract_svg(html, background=None)
    assert out.count(".b{fill:blue}") == 1
    assert ".a{fill:red}" in out


def test_extract_svg_returns_none_when_there_is_no_svg():
    assert extract_svg("<html><body><canvas id='c'></canvas></body></html>") is None
    assert extract_svg("") is None
    assert extract_svg(None) is None


def test_extract_svg_refuses_an_unbalanced_document():
    """Better no file than a truncated one that renders as half a diagram."""
    assert extract_svg("<html><body><svg viewBox='0 0 4 4'><rect/></body></html>") is None
