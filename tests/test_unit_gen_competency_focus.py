"""scripts/gen_competency_focus.py — both directions, daemon-free and vault-free.

`audits.md`: a checker sitting at zero on a healthy tree has proved nothing until
it has been watched going red for each failure shape it claims to cover. The
generated block converged on the real note in one run, so every red case here is
synthetic and deliberate.

The shapes covered: a changed Strength (the drift that actually matters, since
Strength moves as elements get evidenced), a changed name, a missing element, an
off-vocabulary Strength word, a duplicated element, and the two exit paths that
must NOT fail — an absent vault, and a healthy tree.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "gen_competency_focus", SCRIPTS / "gen_competency_focus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load()


# A minimal note: the two tables' shape, not the real prose. Element 5 is
# Adequate and 11 is a Gap so both focus kinds are exercised.
def _note(rows: list[tuple[int, str, str]]) -> str:
    head = "## Unit 1\n\n| # | Element | Strongest evidence | Strength |\n|---|---|---|---|\n"
    body = "".join(
        f"| {n} | {name} | some evidence prose | **{strength}** |\n"
        for n, name, strength in rows
    )
    return head + body


def _full(overrides: dict[int, tuple[str, str]] | None = None) -> str:
    """All sixteen elements, Strong unless overridden."""
    names = {
        1: "Deal with ethical issues", 2: "Practise competently",
        3: "Responsibility for engineering activities",
        4: "Develop safe and sustainable solutions",
        5: "Engage with the relevant community and stakeholders",
        6: "Identify, assess and manage risks",
        7: "Meet legal and regulatory requirements", 8: "Communication",
        9: "Performance", 10: "Taking action", 11: "Judgement",
        12: "Advanced engineering knowledge", 13: "Local engineering knowledge",
        14: "Problem analysis", 15: "Creativity and innovation", 16: "Evaluation",
    }
    ov = overrides or {}
    rows = []
    for n in range(1, 17):
        name, strength = ov.get(n, (names[n], "Strong"))
        rows.append((n, name, strength))
    return _note(rows)


# ── parsing: the green direction ────────────────────────────────────────────

def test_parses_all_sixteen_with_no_problems():
    names, focus, problems = gen.parse_note(_full())
    assert problems == []
    assert sorted(names) == list(range(1, 17))
    assert focus == {}, "every element Strong -> focus map must be EMPTY"


def test_strength_maps_to_focus_per_the_notes_own_key():
    names, focus, problems = gen.parse_note(_full({
        1: ("Deal with ethical issues", "Adequate"),
        11: ("Judgement", "Gap"),
    }))
    assert problems == []
    assert focus == {1: "thin", 11: "gap"}


def test_trailing_prose_after_the_strength_word_is_tolerated():
    # The real note writes `**Gap — structural.** See gap note.` and
    # `**Strong** — the downstream adoption is what makes this one land.`
    text = _full().replace(
        "| 13 | Local engineering knowledge | some evidence prose | **Strong** |",
        "| 13 | Local engineering knowledge | some evidence prose | **Gap — structural.** See gap note. |",
    )
    _, focus, problems = gen.parse_note(text)
    assert problems == []
    assert focus == {13: "gap"}


def test_evidence_column_containing_a_pipe_in_a_code_span():
    text = _full().replace(
        "| 7 | Meet legal and regulatory requirements | some evidence prose | **Strong** |",
        "| 7 | Meet legal and regulatory requirements | ran `a | b` and more | **Adequate** |",
    )
    names, focus, problems = gen.parse_note(text)
    assert problems == []
    assert names[7] == "Meet legal and regulatory requirements"
    assert focus == {7: "thin"}


# ── parsing: the red direction ─────────────────────────────────────────────

def test_missing_element_is_a_problem_not_a_silent_short_table():
    rows = [(n, f"Element {n}", "Strong") for n in range(1, 16)]  # 1..15 only
    _, _, problems = gen.parse_note(_note(rows))
    assert problems and "16" in problems[0]


def test_off_vocabulary_strength_is_refused():
    _, focus, problems = gen.parse_note(_full({4: ("Develop safe and sustainable solutions", "Partial")}))
    assert any("Partial" in p for p in problems)
    assert 4 not in focus, "an unrecognised strength must not silently become Strong"


def test_duplicated_element_is_a_problem():
    rows = [(n, f"Element {n}", "Strong") for n in range(1, 17)]
    rows.append((3, "Element 3 again", "Gap"))
    _, focus, problems = gen.parse_note(_note(rows))
    assert any("twice" in p for p in problems)
    assert focus.get(3) != "gap", "the duplicate must not overwrite the first row"


def test_out_of_range_rows_are_ignored_not_errors():
    rows = [(n, f"Element {n}", "Strong") for n in range(1, 17)]
    rows.append((99, "Not an element", "Gap"))   # e.g. a CPD-hours table row
    names, focus, problems = gen.parse_note(_note(rows))
    assert problems == []
    assert 99 not in names and 99 not in focus


# ── render + splice ────────────────────────────────────────────────────────

def test_render_is_deterministic_and_ascending():
    a = gen.render_block({2: "B", 1: "A"}, {2: "gap"})
    b = gen.render_block({1: "A", 2: "B"}, {2: "gap"})
    assert a == b
    assert a.index('1: "A"') < a.index('2: "B"')
    assert a.startswith(gen.BEGIN) and a.endswith(gen.END)


def test_splice_preserves_everything_outside_the_markers():
    doc = f"BEFORE\n{gen.BEGIN}\nold junk\n{gen.END}\nAFTER\n"
    out = gen.splice(doc, gen.render_block({1: "A"}, {}))
    assert out.startswith("BEFORE\n") and out.endswith("\nAFTER\n")
    assert "old junk" not in out


def test_splice_without_markers_raises_rather_than_appending():
    with pytest.raises(SystemExit):
        gen.splice("no markers here", gen.render_block({1: "A"}, {}))


# ── the drift the script exists for ────────────────────────────────────────

def test_a_changed_strength_changes_the_rendered_block():
    """The load-bearing case: element 11 gets evidenced, note flips to Strong."""
    before = gen.render_block(*gen.parse_note(_full({11: ("Judgement", "Gap")}))[:2])
    after = gen.render_block(*gen.parse_note(_full())[:2])
    assert '11: "gap"' in before
    assert '11: "gap"' not in after
    assert before != after, "closing a gap MUST show up as drift"


def test_a_changed_name_changes_the_rendered_block():
    a = gen.render_block(*gen.parse_note(_full())[:2])
    b = gen.render_block(*gen.parse_note(_full({9: ("Perfomance", "Strong")}))[:2])
    assert a != b


# ── the live tree agrees with the live note ────────────────────────────────

def test_shipped_shared_py_matches_the_note_when_a_vault_is_present():
    root = gen.vault_root()
    note = (root / gen.NOTE_REL) if root else None
    if note is None or not note.is_file():
        pytest.skip("no vault configured — the script skips here too, by design")
    names, focus, problems = gen.parse_note(note.read_text(encoding="utf-8"))
    assert problems == []
    current = gen.TARGET.read_text(encoding="utf-8")
    assert gen.splice(current, gen.render_block(names, focus)) == current, (
        "shared.py has drifted — run python scripts/gen_competency_focus.py")


def test_vault_root_returns_none_rather_than_raising_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gen, "REPO_ROOT", Path("/definitely/not/a/repo"))
    assert gen.vault_root() is None
