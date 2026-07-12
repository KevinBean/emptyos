"""Unit tests for the agent-bus L0 abstract index (pure file IO).

Covers `_extract_abstract` (the L0 one-liner extractor) and `list_bus_entries`
(the scan-then-drill menu). The `agent_bus` module is stdlib-only and never
boots the kernel, so this is safe to run while the daemon is up
(per `.claude/rules/daemon-handling.md`).

Load-bearing invariant: the index reads the SAME `.agent-bus/` store that
`bus_context` reads, so the menu and a follow-up drill never disagree; and an
explicit `abstract:` frontmatter override must beat the H1 fallback.
"""

from pathlib import Path

from emptyos.sdk.agent_bus import _extract_abstract, list_bus_entries

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── _extract_abstract (priority + truncation) ───────────────────────────────


def test_abstract_prefers_frontmatter_abstract_over_h1():
    text = "---\nabstract: The tight one-liner.\n---\n# A much longer H1 heading\n\nbody"
    assert _extract_abstract(text, fallback="x") == "The tight one-liner."


def test_abstract_uses_description_for_skills():
    text = "---\nname: foo\ndescription: What the skill does.\n---\n# Foo\n"
    assert _extract_abstract(text, fallback="foo") == "What the skill does."


def test_abstract_falls_back_to_h1_when_no_frontmatter():
    text = "# Addons Rule — Config-Driven Extension Points\n\nbody text"
    assert _extract_abstract(text, fallback="addons") == (
        "Addons Rule — Config-Driven Extension Points"
    )


def test_abstract_falls_back_to_h1_when_frontmatter_has_no_abstract():
    text = "---\npaths: ['apps/**']\n---\n# Real Heading Here\n\nbody"
    assert _extract_abstract(text, fallback="x") == "Real Heading Here"


def test_abstract_falls_back_to_name_when_nothing_matches():
    assert _extract_abstract("just prose, no heading", fallback="my-rule") == "my-rule"


def test_abstract_truncates_to_limit():
    text = "# " + ("z" * 500)
    assert len(_extract_abstract(text, fallback="x", limit=240)) == 240


# ── list_bus_entries against the real .agent-bus/ store ─────────────────────


def test_list_bus_entries_shape_and_nonempty_abstracts():
    entries = list_bus_entries(_REPO_ROOT)
    assert isinstance(entries, list) and entries, "expected a populated bus menu"
    for e in entries:
        assert set(e) == {"kind", "name", "abstract"}
        assert e["kind"] in {"rule", "section", "skill"}
        assert e["name"] and e["abstract"], f"empty field in {e!r}"


def test_list_bus_entries_includes_known_rules():
    names = {e["name"] for e in list_bus_entries(_REPO_ROOT) if e["kind"] == "rule"}
    # agent-bus and testing are long-standing rules; if these vanish the index
    # (or the .agent-bus sync) regressed.
    assert "agent-bus" in names
    assert "testing" in names


def test_list_bus_entries_filters_kinds():
    only_rules = list_bus_entries(_REPO_ROOT, sections=False, skills=False)
    assert only_rules and all(e["kind"] == "rule" for e in only_rules)


def test_list_bus_entries_missing_store_returns_empty(tmp_path):
    # No .agent-bus/ dir at all → empty list, never raises.
    assert list_bus_entries(tmp_path) == []


def test_frontmatter_override_beats_h1_end_to_end(tmp_path):
    rules = tmp_path / ".agent-bus" / "rules"
    rules.mkdir(parents=True)
    (rules / "demo.md").write_text(
        "---\nabstract: Overridden L0.\n---\n# A Long Ignored H1 Heading\n\nbody",
        encoding="utf-8",
    )
    entries = list_bus_entries(tmp_path, sections=False, skills=False)
    assert entries == [{"kind": "rule", "name": "demo", "abstract": "Overridden L0."}]
