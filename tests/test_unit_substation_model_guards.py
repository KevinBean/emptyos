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
