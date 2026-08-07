"""Unit tests for substation-project's model-shape guards.

Regression: `PUT /api/projects/{id}` accepted a LIST as `title` and persisted it,
and accepted a non-dict `model` (storing the junk key inside the model), while
`POST` raised AttributeError on `.strip()` for the same title — one bad input,
two wrong answers. `_normalize_model` is the choke point POST, PUT and reads all
share, so a note a pre-guard write already corrupted is repaired on read.
"""

from helpers import load_app_module

app_mod = load_app_module("substation-project", "app", preload=("reconcile",))
_normalize = app_mod.SubstationProjectApp._normalize_model


def test_normalize_coerces_list_title_to_string():
    m = _normalize({"id": "p", "title": ["x"]})
    assert isinstance(m["title"], str)
    assert "x" in m["title"]


def test_normalize_coerces_numeric_title_to_string():
    assert _normalize({"id": "p", "title": 42})["title"] == "42"


def test_normalize_leaves_a_real_string_title_untouched():
    assert _normalize({"id": "p", "title": "East Central"})["title"] == "East Central"


def test_normalize_leaves_absent_title_absent():
    """A missing title is the caller's business (PUT setdefaults it) — don't invent one."""
    assert "title" not in _normalize({"id": "p"})


def test_normalize_still_fills_basis_links_revision():
    m = _normalize({"id": "p", "title": "t"})
    assert isinstance(m["basis"], dict)
    assert isinstance(m["links"], dict)
    assert m["revision"] == "A"


def test_normalize_repairs_a_corrupted_note_on_read():
    """A note written before the guard carries a list title; reading it must not
    hand a list to anything that renders or slugifies the project."""
    assert isinstance(_normalize({"id": "p", "title": ["was", "corrupted"]})["title"], str)


# ── Section markdown sanitising (card expand) ────────────────────────

_strip = app_mod._strip_embedded_markup


def test_strip_removes_svg_blocks():
    md = "Before\n<svg xmlns='x' viewBox='0 0 5 5'><line x1='1'/></svg>\nAfter"
    out = _strip(md)
    assert "<svg" not in out and "</svg>" not in out
    assert "Before" in out and "After" in out
    assert "diagram omitted" in out


def test_strip_handles_multiple_and_multiline_svg():
    md = "<svg>\n<a>\n</svg>middle<SVG attr='1'>x</SVG>"
    out = _strip(md)
    assert "<svg" not in out.lower()
    assert "middle" in out


def test_strip_leaves_plain_markdown_untouched():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert _strip(md) == md


def test_strip_none_is_empty():
    assert _strip(None) == ""


def test_normalize_revision_none_and_blank_become_A():
    """setdefault alone leaves an explicit None in place; a bare str() would
    then persist the literal string "None" as the revision."""
    assert _normalize({"id": "p", "revision": None})["revision"] == "A"
    assert _normalize({"id": "p", "revision": "  "})["revision"] == "A"
    assert _normalize({"id": "p", "revision": "B"})["revision"] == "B"
    assert _normalize({"id": "p", "revision": 3})["revision"] == "3"
