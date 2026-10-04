"""Pins for ``emptyos.source_inspect.module_constant`` in both directions:
each accepted shape returns the author's literal with its wrapper type, and
each refused shape raises rather than returning something plausible.

Written to ``tempfile`` rather than ``tmp_path`` (``pytest-current`` has been
access-denied on this box, 2026-09-15); a single file, unlinked in ``finally``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from emptyos.source_inspect import module_constant

SRC = """
PLAIN = ["x", "y"]
ANNOTATED: frozenset[str] = frozenset({"store", "hub"})
WRAPPED_TUPLE = tuple([1, 2])
COMPUTED = compute()
TWICE = 1
TWICE = 2
KEYWORDED = frozenset({"a"}, x=1)


def f():
    LOCAL_ONLY = 1
    return LOCAL_ONLY
"""


@pytest.fixture
def src():
    fd, name = tempfile.mkstemp(prefix="eos-source-inspect-", suffix=".py")
    os.close(fd)
    p = Path(name)
    p.write_text(SRC, encoding="utf-8")
    try:
        yield p
    finally:
        p.unlink(missing_ok=True)


def test_plain_assignment(src):
    assert module_constant(src, "PLAIN") == ["x", "y"]


def test_annotated_frozenset_keeps_its_type(src):
    value = module_constant(src, "ANNOTATED")
    assert value == {"store", "hub"}
    assert isinstance(value, frozenset)


def test_one_argument_wrapper_is_applied(src):
    assert module_constant(src, "WRAPPED_TUPLE") == (1, 2)


def test_non_literal_value_raises(src):
    with pytest.raises(ValueError):
        module_constant(src, "COMPUTED")


def test_keyword_wrapper_is_not_guessed(src):
    with pytest.raises(ValueError):
        module_constant(src, "KEYWORDED")


def test_bound_twice_raises(src):
    with pytest.raises(LookupError):
        module_constant(src, "TWICE")


def test_function_local_is_not_a_module_constant(src):
    with pytest.raises(LookupError):
        module_constant(src, "LOCAL_ONLY")


def test_missing_name_raises(src):
    with pytest.raises(LookupError):
        module_constant(src, "NOPE")
