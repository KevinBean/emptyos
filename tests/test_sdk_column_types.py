"""Unit tests for emptyos.sdk.column_types — pure, no daemon.

Covers the link-record coerce_fn and rollup validate_fn added alongside the
collection-app relational/computed field support (see .claude/rules/
boards-as-view-layer.md and emptyos/sdk/collection_app.py). Both are shared
ColumnTypeRegistry entries — every VaultLibrary-based consumer (boards'
own items.py, CollectionLibrary) gets these for free.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.column_types import ColumnTypeRegistry


class TestLinkRecordCoerce:
    def _ctype(self):
        return ColumnTypeRegistry.get("link-record")

    def test_coerce_none_to_empty_list(self):
        assert self._ctype().coerce(None, {}) == []

    def test_coerce_empty_string_to_empty_list(self):
        assert self._ctype().coerce("", {}) == []

    def test_coerce_single_string_to_list(self):
        assert self._ctype().coerce("author-1.md", {}) == ["author-1.md"]

    def test_coerce_list_passthrough(self):
        # `multi` defaults to False (single-target) when unspecified — same
        # convention as boards' own column config — so an unmarked field
        # truncates to one id even given several.
        assert self._ctype().coerce(["a.md", "b.md"], {"multi": True}) == ["a.md", "b.md"]

    def test_coerce_strips_blank_entries(self):
        assert self._ctype().coerce(["a.md", "", "  ", "b.md"], {"multi": True}) == ["a.md", "b.md"]

    def test_coerce_truncates_to_one_when_not_multi(self):
        assert self._ctype().coerce(["a.md", "b.md"], {"multi": False}) == ["a.md"]

    def test_coerce_keeps_multiple_when_multi(self):
        assert self._ctype().coerce(["a.md", "b.md"], {"multi": True}) == ["a.md", "b.md"]

    def test_validate_required_empty_raises(self):
        with pytest.raises(ValueError):
            self._ctype().validate([], {"required": True, "label": "Author"})

    def test_validate_required_present_passes(self):
        self._ctype().validate(["a.md"], {"required": True})  # no raise


class TestRollupValidate:
    def _ctype(self):
        return ColumnTypeRegistry.get("rollup")

    def test_validate_rejects_any_write(self):
        with pytest.raises(ValueError, match="computed field"):
            self._ctype().validate("some value", {"label": "Book count"})

    def test_validate_rejects_even_when_not_required(self):
        # A rollup is never writable regardless of `required` — the
        # generic required/pattern check never runs for it.
        with pytest.raises(ValueError):
            self._ctype().validate("", {"required": False})

    def test_coerce_is_still_passthrough(self):
        # No coerce_fn registered — only the validate_fn blocks writes,
        # so a value that reaches validate() was never mutated first.
        assert self._ctype().coerce("raw", {}) == "raw"
