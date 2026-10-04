"""Markdown table round-trip."""

from engines.sheet.grid import Grid
from engines.sheet.markdown_io import parse, serialize
from engines.sheet.recompute import recompute


def test_roundtrip_preserves_raw_source():
    g = Grid({"A1": "5", "B1": "7", "C1": "=SUM(A1:B1)"}, rows=3, cols=4)
    body = serialize(g)
    g2 = parse(body)
    assert g2.raw("A1") == "5"
    assert g2.raw("C1") == "=SUM(A1:B1)"
    assert g2.cols == 4
    assert g2.rows == 3


def test_serialize_is_markdown_table():
    g = Grid({"A1": "1"}, rows=2, cols=2)
    body = serialize(g)
    lines = body.strip().splitlines()
    assert lines[0].startswith("|") and "A" in lines[0] and "B" in lines[0]
    assert set(lines[1].replace("|", "").replace("-", "").strip()) <= {" ", ""}


def test_empty_cells_roundtrip():
    g = Grid({"B2": "9"}, rows=3, cols=3)
    g2 = parse(serialize(g))
    assert g2.raw("B2") == "9"
    assert g2.raw("A1") == ""


def test_pipe_in_formula_escapes():
    # A literal containing a pipe must survive the round-trip.
    g = Grid({"A1": "a|b"}, rows=1, cols=1)
    g2 = parse(serialize(g))
    assert g2.raw("A1") == "a|b"


def test_parsed_grid_recomputes():
    g = Grid({"A1": "5", "A2": "7", "A3": "=SUM(A1:A2)"}, rows=3, cols=2)
    g2 = parse(serialize(g))
    assert recompute(g2)["A3"] == 12


def test_parse_empty_body():
    g = parse("")
    assert g.rows == 1 and g.cols == 1
