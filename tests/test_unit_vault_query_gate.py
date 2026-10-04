"""Unit tests for the MCP-foundry vault/query fail-closed gate.

Regression guard: an unset/empty vault_tags_allow must expose NOTHING (the
foundry's eos://vault/query resource is the only consumer), and a matched note
carrying a non-allowlisted tag must not leak its frontmatter via a co-tag
match. Tests the pure predicates extracted from the route — no daemon needed.
"""
from emptyos.web.server import _vault_query_restricted, _vault_query_visible


# ── _vault_query_restricted: True means "refuse / return nothing" ──

def test_empty_allowlist_fails_closed():
    # The headline fix: no allowlist configured -> refuse, never whole-vault.
    assert _vault_query_restricted(["kb"], []) is True
    assert _vault_query_restricted([], []) is True


def test_no_tags_refused_even_with_allowlist():
    assert _vault_query_restricted([], ["kb"]) is True


def test_unlisted_tag_refused():
    assert _vault_query_restricted(["salary"], ["kb"]) is True
    assert _vault_query_restricted(["kb", "salary"], ["kb"]) is True


def test_allowlisted_query_permitted():
    assert _vault_query_restricted(["kb"], ["kb"]) is False
    assert _vault_query_restricted(["kb", "formula"], ["kb", "formula"]) is False


def test_none_allow_treated_as_empty():
    assert _vault_query_restricted(["kb"], None) is True


# ── _vault_query_visible: True means "this note may be returned" ──

def test_note_with_only_allowlisted_tags_visible():
    assert _vault_query_visible(["kb"], ["kb", "formula"]) is True
    assert _vault_query_visible(["kb", "formula"], ["kb", "formula"]) is True


def test_note_with_sensitive_sibling_tag_hidden():
    # [kb, salary] when only "kb" opted in -> hidden (no frontmatter leak).
    assert _vault_query_visible(["kb", "salary"], ["kb"]) is False


def test_untagged_note_visible():
    assert _vault_query_visible([], ["kb"]) is True
    assert _vault_query_visible(None, ["kb"]) is True


def test_visible_with_empty_allow_only_for_untagged():
    assert _vault_query_visible([], []) is True
    assert _vault_query_visible(["kb"], []) is False
