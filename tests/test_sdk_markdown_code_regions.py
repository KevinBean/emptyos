"""Unit tests: markdown code regions — nothing rewrites markdown inside code.

`emptyos/sdk/markdown_render.py` gained `iter_code_segments` / `strip_code` /
`sub_outside_code` when `apps/public/core/link/linkindex.py` became the second
consumer of "where is the code in this document". Two consumers, opposite needs:
the renderer must *preserve* code verbatim, the link index must *exclude* it.

The bug this closes, confirmed by running the old code: `resolve_wikilinks` was
a plain `WIKILINK.sub` over the whole document, so a `[[Note]]` written inside a
fenced example became a real `<a href>` in the published site's rendered code
block — the reader saw markup where the author wrote a literal.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.markdown_render import (
    WIKILINK,
    iter_code_segments,
    resolve_wikilinks,
    strip_code,
    sub_outside_code,
)

SLUGS = {"alpha": ("alpha", "post")}


class TestSegmentsCoverTheText:
    @pytest.mark.parametrize("text", [
        "",
        "plain\n",
        "a `code` b\n",
        "```\nfenced\n```\n",
        "```\nunclosed\n",
        "~~~\ntilde\n~~~\ntail\n",
        "no trailing newline",
    ])
    def test_segments_reassemble_exactly(self, text):
        """The scanner must be lossless — every consumer rebuilds from it."""
        assert "".join(chunk for _, chunk in iter_code_segments(text)) == text


class TestStripCode:
    def test_blanks_fenced_and_inline(self):
        out = strip_code("keep `drop` keep\n```\ndrop\n```\nkeep\n")
        assert "drop" not in out
        assert out.count("keep") == 3

    def test_preserves_line_count(self):
        text = "a\n```\nb\n```\nc\n"
        assert strip_code(text).count("\n") == text.count("\n")

    def test_unclosed_fence_runs_to_end(self):
        assert "hidden" not in strip_code("a\n```\nhidden\n")

    def test_a_closing_run_must_match_the_opening_char(self):
        """A ``` does not close a ~~~ fence."""
        assert "inside" not in strip_code("~~~\ninside\n```\ninside2\n~~~\nout\n")
        assert "out" in strip_code("~~~\ninside\n```\ninside2\n~~~\nout\n")


class TestSubOutsideCode:
    def test_substitutes_outside_and_never_inside(self):
        text = "A [[Alpha]] here.\n\n```\n[[Alpha]]\n```\n\nAnd `[[Alpha]]`.\n"
        out = sub_outside_code(WIKILINK, lambda m: "X", text)
        assert out.count("X") == 1
        assert "```\n[[Alpha]]\n```" in out
        assert "`[[Alpha]]`" in out

    def test_is_a_noop_when_nothing_matches(self):
        text = "nothing to do\n```\ncode\n```\n"
        assert sub_outside_code(WIKILINK, lambda m: "X", text) == text


class TestResolveWikilinksSkipsCode:
    """The regression this whole extraction exists for."""

    MD = (
        "Real [[Alpha]].\n\n"
        "```js\nconst q = `[[Alpha]]`;\n```\n\n"
        "Inline `[[Alpha]]` too.\n\n"
        "~~~\n[[Alpha]]\n~~~\n\n"
        "Trailing [[Alpha]].\n"
    )

    def test_only_the_prose_links_are_rendered(self):
        out = resolve_wikilinks(self.MD, SLUGS)
        assert out.count("<a href") == 2

    def test_every_code_region_is_untouched(self):
        out = resolve_wikilinks(self.MD, SLUGS)
        assert "const q = `[[Alpha]]`;" in out
        assert "Inline `[[Alpha]]` too." in out
        assert "~~~\n[[Alpha]]\n~~~" in out

    def test_private_notes_still_degrade_to_a_span(self):
        out = resolve_wikilinks("[[Unpublished]]\n", SLUGS)
        assert 'class="wikilink-private"' in out

    def test_alias_display_text_survives(self):
        out = resolve_wikilinks("[[Alpha|see this]]\n", SLUGS)
        assert ">see this</a>" in out
