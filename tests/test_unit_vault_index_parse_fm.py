"""Regression tests for vault_index._parse_fm — empty values.

A quoted empty string (`key: ""`) must parse as `""`, not as the start of a
block list. The bug: `follow_up_due: ""` opened a block-list that the next key
closed as `[]`, so a str-typed VaultModel field surfaced as an empty list and
the whole note failed validation + dropped out of read_all.

The *bare* empty (`key:` with nothing after it) had the same defect one level
down and was left in place at the time, because the coercion layer was thought
to absorb it. It closed as `[]` mid-block and `""` at the end — a type that
depended on position — and only one of the two coercion layers absorbed it.
Both flush sites now agree on `""`.
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


def test_bare_empty_value_reads_the_same_wherever_it_sits():
    """A bare empty value must not depend on what follows it in the block.

    This test used to assert the opposite — that a bare `notes:` mid-block
    stays `[]` — on the grounds that "the VaultModel base coercion handles
    `[] -> ""` downstream for str-typed fields". That is true of `VaultModel`
    and false of `VaultLibrary`, whose `_coerce` ran a bare `str()` and so
    produced the *truthy* string "[]". It named one of two coercion layers,
    and the unnamed one is the older and far more widely used standard.

    The value it pinned was never stable anyway: the mid-loop flush wrote
    `[]` while the final flush wrote `""`, so the same `notes:` parsed as a
    list or a string purely by position. Both flushes now agree on `""`,
    which is what `sdk.parse_frontmatter` agrees on too — see
    `test_sdk_utils.py::test_both_frontmatter_parsers_agree`.

    A bare empty key that *does* have `- ` children is still a real list;
    `test_bare_empty_value_still_opens_block_list` above holds that.
    """
    mid = _fm("notes:\ncompany: Acme")
    last = _fm("company: Acme\nnotes:")
    assert mid["notes"] == "" == last["notes"]   # position cannot change the type
    assert mid["company"] == "Acme"


def test_inline_array_unquoted_still_parses():
    fm = _fm("tags: [a, b, c]")
    assert fm["tags"] == ["a", "b", "c"]


def test_quoted_bracket_string_is_not_an_array():
    """A quoted "[...]" is a JSON-in-frontmatter string, not a YAML array."""
    fm = _fm('svg_callouts: "[{\\"x\\": 1}]"')
    assert fm["svg_callouts"] == '[{"x": 1}]'
