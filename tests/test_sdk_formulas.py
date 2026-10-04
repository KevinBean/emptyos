"""Regression for emptyos.sdk.formulas — pure, no daemon.

Pins the backward-compatible `*args` aggregate change (so the sheet engine
can call `SUM(A1,A2,A3)`) without breaking the boards link-column shape
`SUM(deliverables.weight_hours)` (one list arg).
"""

from emptyos.sdk.formulas import evaluate, format_result


# ── Legacy single-iterable shape (boards link columns) ────────────────


def test_sum_single_list_arg():
    # boards passes a pre-resolved list as one arg.
    assert evaluate("SUM(weights)", {"weights": [1, 2, 3]}) == 6


def test_sum_link_attribute_walk():
    # `col.field` resolves to a list of values; SUM reduces it.
    ctx = {"deliverables": [{"hours": 2}, {"hours": 5}]}
    assert evaluate("SUM(deliverables.hours)", ctx) == 7


def test_avg_single_list_arg():
    assert evaluate("AVG(xs)", {"xs": [2, 4, 6]}) == 4


def test_count_single_list_arg():
    assert evaluate("COUNT(xs)", {"xs": [1, "", 3, None]}) == 2


def test_min_max_single_list_arg():
    assert evaluate("MIN(xs)", {"xs": [3, 1, 2]}) == 1
    assert evaluate("MAX(xs)", {"xs": [3, 1, 2]}) == 3


def test_aggregate_scalar_arg_still_works():
    # A non-list scalar arg coerces via _as_iterable -> single-element.
    assert evaluate("SUM(x)", {"x": 5}) == 5


# ── New varargs shape (sheet discrete cell refs) ──────────────────────


def test_sum_discrete_args():
    assert evaluate("SUM(a, b, c)", {"a": 1, "b": 2, "c": 3}) == 6


def test_avg_discrete_args():
    assert evaluate("AVG(a, b, c)", {"a": 2, "b": 4, "c": 6}) == 4


def test_min_max_discrete_args():
    assert evaluate("MIN(a, b, c)", {"a": 3, "b": 1, "c": 2}) == 1
    assert evaluate("MAX(a, b, c)", {"a": 3, "b": 1, "c": 2}) == 3


def test_mixed_list_and_scalar_args():
    assert evaluate("SUM(xs, y)", {"xs": [1, 2], "y": 3}) == 6


# ── Unchanged behaviours ──────────────────────────────────────────────


def test_if_and_arithmetic_unchanged():
    assert evaluate("IF(a>10,'hi','lo')", {"a": 15}) == "hi"
    assert evaluate("a*b+1", {"a": 3, "b": 4}) == 13


def test_format_result_whole_float():
    assert format_result(12.0) == "12"


# ── Rollup aggregates (Notion-parity over linked fields) ──────────────


def test_earliest_latest_over_dates():
    ctx = {"tasks": [{"due": "2026-07-10"}, {"due": "2026-03-02"}, {"due": "2026-12-01"}]}
    assert evaluate("EARLIEST(tasks.due)", ctx) == "2026-03-02"
    assert evaluate("LATEST(tasks.due)", ctx) == "2026-12-01"


def test_earliest_ignores_non_dates_and_empties():
    ctx = {"tasks": [{"due": ""}, {"due": "not a date"}, {"due": "2026-05-05"}]}
    assert evaluate("EARLIEST(tasks.due)", ctx) == "2026-05-05"


def test_earliest_empty_list_is_blank():
    assert evaluate("EARLIEST(tasks.due)", {"tasks": []}) == ""


def test_min_still_number_only_regression():
    # MIN coerces to number, so a date collapses to 0 — that's why EARLIEST exists.
    assert evaluate("MIN(tasks.due)", {"tasks": [{"due": "2026-05-05"}]}) == 0.0


def test_count_unique():
    ctx = {"tags": [{"t": "a"}, {"t": "A"}, {"t": "b"}, {"t": ""}]}
    assert evaluate("COUNT_UNIQUE(tags.t)", ctx) == 2  # a/A folded, "" dropped


def test_percent_checked():
    ctx = {"subs": [{"done": "true"}, {"done": "false"}, {"done": "true"}, {"done": "yes"}]}
    assert evaluate("PERCENT_CHECKED(subs.done)", ctx) == 75.0


def test_percent_checked_empty_is_zero():
    assert evaluate("PERCENT_CHECKED(subs.done)", {"subs": []}) == 0.0
