"""Dependency-ordered recompute."""

from engines.sheet.grid import Grid
from engines.sheet.recompute import recompute


def _g(cells):
    return Grid(cells, rows=10, cols=10)


def test_literals_coerce():
    c = recompute(_g({"A1": "5", "A2": "hello", "A3": "3.5"}))
    assert c["A1"] == 5
    assert c["A2"] == "hello"
    assert c["A3"] == 3.5


def test_simple_arithmetic():
    c = recompute(_g({"A1": "5", "B1": "7", "C1": "=A1+B1"}))
    assert c["C1"] == 12


def test_sum_range():
    c = recompute(_g({"A1": "5", "A2": "7", "A3": "=SUM(A1:A2)"}))
    assert c["A3"] == 12


def test_sum_discrete_args():
    c = recompute(_g({"A1": "1", "A2": "2", "B1": "3", "C1": "=SUM(A1,A2,B1)"}))
    assert c["C1"] == 6


def test_avg_block_range():
    c = recompute(
        _g({"A1": "2", "A2": "4", "B1": "6", "B2": "8", "C1": "=AVG(A1:B2)"})
    )
    assert c["C1"] == 5


def test_if_with_string_branches_no_false_dep():
    # B2 / C3 appear only inside string literals — must not become deps.
    c = recompute(_g({"A1": "15", "D1": '=IF(A1>10,"B2","C3")'}))
    assert c["D1"] == "B2"


def test_dependency_chain_any_order():
    # Declared out of order; recompute must topo-sort.
    c = recompute(_g({"C1": "=B1", "B1": "=A1+1", "A1": "5"}))
    assert c["A1"] == 5
    assert c["B1"] == 6
    assert c["C1"] == 6


def test_self_reference_is_cycle():
    c = recompute(_g({"A1": "=A1"}))
    assert c["A1"] == "#CYCLE"


def test_two_cell_cycle():
    c = recompute(_g({"A1": "=B1", "B1": "=A1"}))
    assert c["A1"] == "#CYCLE"
    assert c["B1"] == "#CYCLE"


def test_today_no_args():
    from datetime import date

    c = recompute(_g({"A1": "=TODAY()"}))
    assert c["A1"] == date.today().isoformat()


def test_empty_cell_reference_is_zero():
    c = recompute(_g({"A1": "=B1+1"}))  # B1 is empty
    assert c["A1"] == 1


def test_bad_formula_is_err():
    c = recompute(_g({"A1": "=SUM("}))
    assert c["A1"] == "#ERR"
