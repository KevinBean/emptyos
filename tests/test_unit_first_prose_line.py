"""`first_prose_line` — one short line to represent a markdown document.

Extracted at the second consumer (CLAUDE.md rule 9): `agent/skills.py` had been
plucking a description fallback out of a SKILL.md since before daily-brief
needed the same thing for a push notification. The two disagree on exactly one
point, which is why `allow_heading` exists rather than being hardcoded — a
SKILL.md with only headings has no description (empty is the honest answer),
while a brief with only a title should still say the title.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.utils import first_prose_line


class TestPicksTheFirstProse:
    def test_plain_first_line(self):
        assert first_prose_line("Batteries got cheaper.") == "Batteries got cheaper."

    def test_skips_leading_blank_lines(self):
        assert first_prose_line("\n\n  \nBatteries got cheaper.") == "Batteries got cheaper."

    def test_stops_at_the_first_one(self):
        assert first_prose_line("First line.\nSecond line.") == "First line."

    @pytest.mark.parametrize("md,expected", [
        ("- Batteries got cheaper.", "Batteries got cheaper."),
        ("* Batteries got cheaper.", "Batteries got cheaper."),
        ("1. Batteries got cheaper.", "Batteries got cheaper."),
        ("**Batteries** got _cheaper_.", "Batteries got cheaper."),
        ("~~Batteries~~ got cheaper.", "Batteries got cheaper."),
        ("`Batteries` got cheaper.", "Batteries got cheaper."),
        ("[Batteries](https://x.invalid) got cheaper.", "Batteries got cheaper."),
        ("![chart](x.png) Batteries got cheaper.", "chart Batteries got cheaper."),
    ])
    def test_markdown_is_stripped(self, md, expected):
        assert first_prose_line(md) == expected


class TestSkipsStructure:
    @pytest.mark.parametrize("structure", [
        "> a pull quote",
        "| a | table | row |",
        "---",
        "***",
        "___",
        "- - -",
        "* * *",
    ])
    def test_non_prose_lines_are_skipped(self, structure):
        assert first_prose_line(f"{structure}\n\nReal line.") == "Real line."

    def test_a_bullet_is_not_a_horizontal_rule(self):
        """`* item` must survive the rule check that kills `* * *`."""
        assert first_prose_line("* Batteries got cheaper.") == "Batteries got cheaper."

    def test_fenced_code_contents_never_leak(self):
        md = "```python\nprint('not prose')\n```\n\nReal line."
        assert first_prose_line(md) == "Real line."

    def test_unclosed_fence_swallows_the_rest(self):
        """Better to return nothing than to quote code as if it were prose."""
        assert first_prose_line("```\nprint('x')\nstill code") == ""

    def test_empty_and_none_are_safe(self):
        assert first_prose_line("") == ""
        assert first_prose_line(None) == ""  # type: ignore[arg-type]

    def test_whitespace_only(self):
        assert first_prose_line("\n   \n\t\n") == ""


class TestHeadingPolicy:
    def test_heading_never_shadows_prose(self):
        assert first_prose_line("# Today's brief\n\nBatteries got cheaper.") == \
            "Batteries got cheaper."

    def test_heading_is_skipped_by_default(self):
        """agent/skills.py semantics — a heading is a section name, not a
        description, and empty correctly signals 'nothing to say'."""
        assert first_prose_line("# Overview\n## Details") == ""

    def test_heading_is_the_fallback_when_allowed(self):
        """daily-brief semantics — say the title rather than nothing."""
        assert first_prose_line("# Overview\n## Details", allow_heading=True) == "Overview"

    def test_first_heading_wins_as_fallback(self):
        assert first_prose_line("### Deep\n# Shallow", allow_heading=True) == "Deep"

    @pytest.mark.parametrize("level", ["#", "##", "###", "####", "#####", "######"])
    def test_every_heading_level_recognised(self, level):
        assert first_prose_line(f"{level} Title\n\nProse.") == "Prose."


class TestCallerOwnsTruncation:
    def test_returns_the_whole_line(self):
        """Limit and ellipsis are display decisions — the two consumers use
        different caps (240 vs 160) and different ellipsis policies."""
        out = first_prose_line("x" * 500)
        assert len(out) == 500
