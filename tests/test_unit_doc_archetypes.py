"""Unit tests for emptyos.sdk.doc_archetypes.

Pure — no daemon, no Playwright. The load-bearing test feeds each scaffold
through pdf.py's OWN parsers (_split_frontmatter, _extract_masthead,
_obsidian_clean) to prove every archetype produces profile-valid markdown that
pdf.py will render correctly, without launching a browser.
"""

import pytest

from emptyos.sdk import doc_archetypes as da
from emptyos.sdk.pdf import (
    PDF_THEMES,
    _extract_masthead,
    _obsidian_clean,
    _split_frontmatter,
)


def test_registry_nonempty_and_named():
    names = da.archetype_names()
    assert "resume" in names
    assert "research-report" in names
    assert names == sorted(names)


def test_get_archetype_is_case_insensitive():
    assert da.get_archetype("RESUME").name == "resume"
    assert da.get_archetype("  Research-Report ").name == "research-report"


def test_unknown_archetype_raises_loud():
    with pytest.raises(KeyError) as e:
        da.get_archetype("whitepaper")
    assert "whitepaper" in str(e.value)
    assert "resume" in str(e.value)  # lists known names


def test_archetype_style_resolves_to_a_real_theme():
    for name in da.archetype_names():
        style = da.archetype_style(name)
        assert style in PDF_THEMES, f"{name} -> unknown theme {style!r}"


@pytest.mark.parametrize("name", da.archetype_names())
def test_scaffold_is_profile_valid(name):
    """Every scaffold must parse through pdf.py's profile parsers: a leading
    fenced masthead with >=1 line, and a non-empty body of ## sections."""
    md = da.scaffold_markdown(name)
    body = _split_frontmatter(md)            # no frontmatter here, returns as-is
    masthead, body_md = _extract_masthead(body)

    assert masthead, f"{name}: masthead fence not detected"
    assert len(masthead) >= 1
    assert body_md.strip(), f"{name}: empty body after masthead"
    assert "## " in body_md, f"{name}: no sections in body"

    # Every declared section heading appears as a ## header in the body.
    arche = da.get_archetype(name)
    for sec in arche.sections:
        assert f"## {sec.heading}" in body_md


@pytest.mark.parametrize("name", da.archetype_names())
def test_guidance_comments_vanish_at_render(name):
    """%%…%% guidance is stripped by _obsidian_clean, so an UNFILLED scaffold
    renders with no leftover guidance markup."""
    md = da.scaffold_markdown(name, with_guidance=True)
    assert "%%" in md  # guidance present in the authored scaffold
    cleaned = _obsidian_clean(md)
    assert "%%" not in cleaned, f"{name}: guidance markers survived render"


def test_with_guidance_false_omits_comments():
    md = da.scaffold_markdown("resume", with_guidance=False)
    assert "%%" not in md
    assert "## Summary" in md  # headings still present


def test_compose_prompt_carries_persona_brief_and_scaffold():
    system, user = da.compose_prompt("resume", "10y power-systems engineer, targeting FDE roles")
    assert "resume writer" in system.lower()
    assert da.PROFILE_REMINDER.split(".")[0] in system  # profile reminder appended
    assert "power-systems engineer" in user
    assert "## Experience" in user                       # scaffold embedded
    assert "%%" in user                                  # guidance shown to the model


def test_resume_ships_an_example_entry():
    """The Experience section seeds one ### entry so the LLM sees the entry shape."""
    md = da.scaffold_markdown("resume")
    assert "### Company — Title" in md
