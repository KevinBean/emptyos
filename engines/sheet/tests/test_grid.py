"""Cell addressing + range expansion."""

import pytest

from engines.sheet.grid import (
    Grid,
    col_to_index,
    expand_range,
    index_to_col,
    parse_a1,
    to_a1,
)


@pytest.mark.parametrize(
    "col,idx", [("A", 0), ("B", 1), ("Z", 25), ("AA", 26), ("AB", 27), ("BA", 52)]
)
def test_col_index_roundtrip(col, idx):
    assert col_to_index(col) == idx
    assert index_to_col(idx) == col


@pytest.mark.parametrize("ref,col,row", [("A1", 0, 1), ("B2", 1, 2), ("AA10", 26, 10)])
def test_parse_a1(ref, col, row):
    assert parse_a1(ref) == (col, row)
    assert to_a1(col, row) == ref


def test_parse_a1_lowercase():
    assert parse_a1("b2") == (1, 2)


@pytest.mark.parametrize("bad", ["", "1A", "A0", "A", "1", "A-1", "$A$1"])
def test_parse_a1_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_a1(bad)


def test_expand_range_column():
    assert expand_range("B2:B4") == ["B2", "B3", "B4"]


def test_expand_range_block_column_major():
    assert expand_range("A1:B2") == ["A1", "A2", "B1", "B2"]


def test_expand_range_unordered_endpoints():
    assert expand_range("B4:B2") == ["B2", "B3", "B4"]


def test_grid_set_and_clear():
    g = Grid(rows=5, cols=5)
    g.set("A1", "5")
    assert g.raw("A1") == "5"
    g.set("a1", "")  # case-insensitive, empty clears
    assert g.raw("A1") == ""
    assert "A1" not in g.cells


def test_grid_raw_grid_uppercases():
    g = Grid({"a1": {"raw": "5"}, "B2": "=A1+1"})
    assert g.raw_grid() == {"A1": "5", "B2": "=A1+1"}
