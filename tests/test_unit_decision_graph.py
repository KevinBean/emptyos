"""Unit tests for the pure decision-graph engine (emptyos/sdk/decision_graph.py).

Kernel-free pure logic — runs offline without the daemon. Covers the three
Narrative-Canvas concepts the engine generalises: choice branching, auto/condition
routing, and variable set/read carried along the walked path.
"""

import pytest

from emptyos.sdk.decision_graph import (
    Condition,
    DecisionGraph,
    DecisionGraphError,
    DecisionRun,
    Mutation,
    Node,
    Transition,
    validate_graph,
)


# ─────────────────────────────── conditions ───────────────────────────────


def test_condition_leaf_ops():
    v = {"score": 5, "name": "kev", "flag": True, "tags": ["a", "b"]}
    assert Condition(var="score", op="==", value=5).evaluate(v)
    assert Condition(var="score", op="!=", value=4).evaluate(v)
    assert Condition(var="score", op=">", value=3).evaluate(v)
    assert Condition(var="score", op="<=", value=5).evaluate(v)
    assert not Condition(var="score", op="<", value=5).evaluate(v)
    assert Condition(var="flag", op="truthy").evaluate(v)
    assert Condition(var="missing", op="falsy").evaluate(v)
    assert Condition(var="name", op="in", value=["kev", "x"]).evaluate(v)
    assert not Condition(var="name", op="in", value=["nope"]).evaluate(v)
    assert Condition(var="name", op="nin", value=["x"]).evaluate(v)


def test_condition_missing_var_is_falsy_not_crash():
    assert Condition(var="nope", op="truthy").evaluate({}) is False
    # Ordered comparison against a missing (None) var must not raise.
    assert Condition(var="nope", op=">", value=3).evaluate({}) is False


def test_condition_empty_is_always_true():
    assert Condition().evaluate({}) is True


def test_condition_groups():
    v = {"a": 1, "b": 2}
    cond = Condition(all=[Condition(var="a", op="==", value=1), Condition(var="b", op="==", value=2)])
    assert cond.evaluate(v)
    cond2 = Condition(any=[Condition(var="a", op="==", value=9), Condition(var="b", op="==", value=2)])
    assert cond2.evaluate(v)
    cond3 = Condition(not_=Condition(var="a", op="==", value=1))
    assert not cond3.evaluate(v)


def test_condition_unknown_op_rejected():
    with pytest.raises(DecisionGraphError):
        Condition.from_dict({"var": "x", "op": "approx"})


# ─────────────────────────────── mutations ────────────────────────────────


def test_mutations():
    v: dict = {}
    Mutation("x", "set", 10).apply(v)
    assert v["x"] == 10
    Mutation("x", "inc").apply(v)  # default +1
    assert v["x"] == 11
    Mutation("x", "inc", 4).apply(v)
    assert v["x"] == 15
    Mutation("x", "dec", 5).apply(v)
    assert v["x"] == 10
    Mutation("done", "toggle").apply(v)
    assert v["done"] is True
    Mutation("done", "toggle").apply(v)
    assert v["done"] is False
    Mutation("seen", "append", "a").apply(v)
    Mutation("seen", "append", "b").apply(v)
    assert v["seen"] == ["a", "b"]
    Mutation("seen", "remove", "a").apply(v)
    assert v["seen"] == ["b"]
    Mutation("x", "del").apply(v)
    assert "x" not in v


def test_mutation_unknown_op_rejected():
    with pytest.raises(DecisionGraphError):
        Mutation.from_dict({"var": "x", "op": "multiply", "value": 2})


# ─────────────────────────── linear + branching ───────────────────────────


def _linear_graph() -> DecisionGraph:
    return DecisionGraph(
        start="a",
        nodes={
            "a": Node("a", data={"text": "first"}, transitions=[Transition(to="b", kind="auto")]),
            "b": Node("b", data={"text": "second"}, transitions=[Transition(to="c", kind="auto")]),
            "c": Node("c", kind="end", data={"text": "done"}),
        },
    )


def test_linear_auto_walk_runs_to_end():
    run = DecisionRun(_linear_graph())
    view = run.start()
    assert view.status == "ended"
    assert view.node_id == "c"
    assert run.history == ["a", "b", "c"]


def _branch_graph() -> DecisionGraph:
    return DecisionGraph(
        start="ask",
        nodes={
            "ask": Node(
                "ask",
                data={"text": "left or right?"},
                transitions=[
                    Transition(to="left", kind="choice", label="Go left", set=[Mutation("path", "set", "L")]),
                    Transition(to="right", kind="choice", label="Go right", set=[Mutation("path", "set", "R")]),
                ],
            ),
            "left": Node("left", kind="end", data={"text": "went left"}),
            "right": Node("right", kind="end", data={"text": "went right"}),
        },
    )


def test_choice_branch_by_index():
    run = DecisionRun(_branch_graph())
    view = run.start()
    assert view.status == "running"
    assert [c.label for c in view.choices] == ["Go left", "Go right"]
    after = run.choose(1)
    assert after.node_id == "right"
    assert after.status == "ended"
    assert run.variables["path"] == "R"


def test_choice_branch_by_target_id():
    run = DecisionRun(_branch_graph())
    run.start()
    after = run.choose("left")
    assert after.node_id == "left"
    assert run.variables["path"] == "L"


def test_choose_invalid_raises():
    run = DecisionRun(_branch_graph())
    run.start()
    with pytest.raises(DecisionGraphError):
        run.choose(9)
    with pytest.raises(DecisionGraphError):
        run.choose("nonexistent")


# ───────────────────── condition routing + var gating ─────────────────────


def _gated_graph() -> DecisionGraph:
    # An auto-router that sends a returning visitor straight to the end,
    # and gates a choice on a variable so it only appears when unlocked.
    return DecisionGraph(
        start="gate",
        vars={"visited": False, "unlocked": False},
        nodes={
            "gate": Node(
                "gate",
                enter_set=[Mutation("visited", "toggle")],
                transitions=[
                    Transition(to="skip", kind="auto", when=Condition(var="returning", op="truthy")),
                    Transition(to="hub", kind="auto"),
                ],
            ),
            "hub": Node(
                "hub",
                data={"text": "hub"},
                transitions=[
                    Transition(to="secret", kind="choice", label="Secret", when=Condition(var="unlocked", op="truthy")),
                    Transition(to="exit", kind="choice", label="Leave"),
                ],
            ),
            "skip": Node("skip", kind="end", data={"text": "welcome back"}),
            "secret": Node("secret", kind="end"),
            "exit": Node("exit", kind="end"),
        },
    )


def test_auto_router_picks_first_eligible():
    # returning=True -> auto edge to "skip" fires before the fallthrough.
    run = DecisionRun(_gated_graph(), variables={"returning": True})
    view = run.start()
    assert view.node_id == "skip"
    assert view.status == "ended"


def test_auto_router_fallthrough_and_enter_set():
    run = DecisionRun(_gated_graph())
    view = run.start()
    assert view.node_id == "hub"
    assert run.variables["visited"] is True  # enter_set on the gate fired
    # "secret" gated off because unlocked is False.
    assert [c.label for c in view.choices] == ["Leave"]


def test_gated_choice_appears_when_unlocked():
    run = DecisionRun(_gated_graph(), variables={"unlocked": True})
    view = run.start()
    assert [c.label for c in view.choices] == ["Secret", "Leave"]


# ────────────────────────────── safety guards ─────────────────────────────


def test_auto_cycle_guard_raises():
    # Two nodes auto-pointing at each other with always-true guards.
    g = DecisionGraph(
        start="x",
        nodes={
            "x": Node("x", transitions=[Transition(to="y", kind="auto")]),
            "y": Node("y", transitions=[Transition(to="x", kind="auto")]),
        },
    )
    with pytest.raises(DecisionGraphError):
        DecisionRun(g).start()


def test_dead_end_content_node_is_terminal():
    g = DecisionGraph(start="a", nodes={"a": Node("a", data={"text": "stuck"})})
    view = DecisionRun(g).start()
    assert view.status == "ended"
    assert view.node_id == "a"


def test_view_before_start_raises():
    with pytest.raises(DecisionGraphError):
        DecisionRun(_branch_graph()).view()


# ───────────────────────────── validation ─────────────────────────────────


def test_validate_clean_graph():
    assert validate_graph(_branch_graph()) == []


def test_validate_missing_start_and_dangling_edge():
    g = DecisionGraph(
        start="missing",
        nodes={"a": Node("a", transitions=[Transition(to="ghost", kind="auto")])},
    )
    errs = validate_graph(g)
    assert any("start node" in e for e in errs)
    assert any("unknown target 'ghost'" in e for e in errs)


def test_validate_empty_graph():
    g = DecisionGraph(start="a", nodes={})
    assert validate_graph(g) == ["graph has no nodes"]


def test_validate_unreachable_is_soft_warn():
    g = DecisionGraph(
        start="a",
        nodes={
            "a": Node("a", kind="end"),
            "orphan": Node("orphan", kind="end"),
        },
    )
    errs = validate_graph(g)
    assert errs == ["warn: node 'orphan' is unreachable from start"]


# ───────────────────────── JSON round-trip + resume ────────────────────────


def test_graph_json_round_trip():
    g = _gated_graph()
    d = g.to_dict()
    g2 = DecisionGraph.from_dict(d)
    assert g2.to_dict() == d
    # And it still walks identically.
    v1 = DecisionRun(g, variables={"unlocked": True}).start()
    v2 = DecisionRun(g2, variables={"unlocked": True}).start()
    assert [c.label for c in v1.choices] == [c.label for c in v2.choices]


def test_from_dict_accepts_map_node_shape():
    d = {
        "start": "a",
        "nodes": {
            "a": {"transitions": [{"to": "b", "kind": "auto"}]},
            "b": {"kind": "end"},
        },
    }
    g = DecisionGraph.from_dict(d)
    assert validate_graph(g) == []
    assert DecisionRun(g).start().node_id == "b"


def test_run_state_resume():
    run = DecisionRun(_branch_graph())
    run.start()
    state = run.to_state()
    resumed = DecisionRun.from_state(_branch_graph(), state)
    after = resumed.choose("right")
    assert after.node_id == "right"
    assert resumed.variables["path"] == "R"


def test_list_var_default_not_shared_across_runs():
    # graph.vars has a list default; one run's append must not leak into the
    # graph or a second independent run (deep-copy in DecisionRun.__init__).
    g = DecisionGraph(
        start="a",
        vars={"seen": []},
        nodes={
            "a": Node("a", enter_set=[Mutation("seen", "append", "x")], kind="end"),
        },
    )
    r1 = DecisionRun(g)
    r1.start()
    assert r1.variables["seen"] == ["x"]
    assert g.vars["seen"] == []  # graph default untouched
    r2 = DecisionRun(g)
    r2.start()
    assert r2.variables["seen"] == ["x"]  # not ["x", "x"]


def test_walk_is_deterministic():
    # Same graph + same picks -> identical history twice (no randomness anywhere).
    def walk():
        r = DecisionRun(_gated_graph(), variables={"unlocked": True})
        r.start()
        r.choose("secret")
        return r.history
    assert walk() == walk()
