"""Regression tests for vault_index._parse_fm — quoted-empty-string handling.

A quoted empty string (`key: ""`) must parse as `""`, not as the start of a
block list. The bug: `follow_up_due: ""` opened a block-list that the next key
closed as `[]`, so a str-typed VaultModel field surfaced as an empty list and
the whole note failed validation + dropped out of read_all.
"""

from __future__ import annotations

from emptyos.runtime.vault_index import _parse_fm


def _fm(body: str) -> dict:
    return _parse_fm("---\n" + body + "\n---\n")


def test_quoted_empty_string_is_string_not_list():
    fm = _fm('follow_up_due: ""\nfollow_up_note: "x"')
    assert fm["follow_up_due"] == ""        # NOT []
    assert fm["follow_up_note"] == "x"


def test_quoted_empty_string_single_quotes():
    fm = _fm("follow_up_due: ''\nstatus: applied")
    assert fm["follow_up_due"] == ""
    assert fm["status"] == "applied"


def test_quoted_empty_string_as_last_key():
    fm = _fm('company: Acme\nfollow_up_due: ""')
    assert fm["follow_up_due"] == ""


def test_bare_empty_value_still_opens_block_list():
    """An unquoted empty value followed by `- ` items is still a real list."""
    fm = _fm("tags:\n  - a\n  - b")
    assert fm["tags"] == ["a", "b"]


def test_bare_empty_value_mid_doc_stays_list_unchanged():
    """A *bare* (unquoted) empty value opens a block list — unchanged by this
    fix. It must stay `[]` so an empty `tags:` followed by a non-item line
    doesn't collapse to a string and break tag handling. The VaultModel base
    coercion handles `[] -> ""` downstream for str-typed fields."""
    fm = _fm("notes:\ncompany: Acme")
    assert fm["notes"] == []          # bare empty, not quoted — pre-existing behavior
    assert fm["company"] == "Acme"


def test_inline_array_unquoted_still_parses():
    fm = _fm("tags: [a, b, c]")
    assert fm["tags"] == ["a", "b", "c"]


def test_quoted_bracket_string_is_not_an_array():
    """A quoted "[...]" is a JSON-in-frontmatter string, not a YAML array."""
    fm = _fm('svg_callouts: "[{\\"x\\": 1}]"')
    assert fm["svg_callouts"] == '[{"x": 1}]'
