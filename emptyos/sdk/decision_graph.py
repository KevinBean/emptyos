"""Decision graph — a pure, authored branching-state model + a runtime that walks it.

This is the irreducible core of the "Narrative Canvas" concept generalised away
from fiction: a graph of nodes where **choice** transitions fork the path on a
reader/learner decision, **auto** transitions route automatically on variable
state (the "condition node"), and **mutations** carry variables along whichever
path is taken (the "set node"). ``DecisionRun`` is the "play mode" — it walks
the graph reactively to the accumulated variable state.

Latent consumers (CLAUDE.md rule 9 — this stays a pure module, unblessed as SDK,
until the *second* concrete one lands): branching tours (the `condition` field
already promised in `.claude/rules/tour-steps.md`), adaptive Learn paths, and
`apps/company/` scenario dialogue trees.

Pure logic only — no kernel, no I/O, no randomness (``random``/``Date.now`` are
banned in this codebase for resume-determinism, and a decision walk must be
reproducible anyway). Safe by construction: conditions are evaluated through a
tiny comparator, never ``eval``. JSON round-trips via ``to_dict`` / ``from_dict``
so a graph can be authored as data and stored in the vault or ``data/``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

VarValue = str | int | float | bool | list

# Comparators a condition leaf may use. All evaluated by hand — no eval().
_CMP_OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "nin", "truthy", "falsy"}
# Mutation operators a transition / node-enter may apply to a variable.
_MUT_OPS = {"set", "inc", "dec", "toggle", "append", "remove", "del"}

# Walk safety: a graph with a cycle of auto-transitions whose guards never
# settle would otherwise loop forever. This caps a single auto-advance burst.
_MAX_AUTO_STEPS = 1000


class DecisionGraphError(Exception):
    """Structural misuse — bad pick, malformed graph, walk past the end."""


# ─────────────────────────────── conditions ───────────────────────────────


@dataclass
class Condition:
    """A boolean gate over the run's variables.

    A *leaf* sets ``var`` + ``op`` (+ ``value`` for binary ops). A *group* sets
    exactly one of ``all`` / ``any`` / ``not_``. Groups nest arbitrarily.
    """

    var: str | None = None
    op: str = "truthy"
    value: Any = None
    all: list["Condition"] | None = None
    any: list["Condition"] | None = None
    not_: "Condition" | None = None

    def evaluate(self, variables: dict[str, Any]) -> bool:
        if self.all is not None:
            return all(c.evaluate(variables) for c in self.all)
        if self.any is not None:
            return any(c.evaluate(variables) for c in self.any)
        if self.not_ is not None:
            return not self.not_.evaluate(variables)
        if self.var is None:
            return True  # empty condition == always-eligible
        cur = variables.get(self.var)
        op = self.op
        if op == "truthy":
            return bool(cur)
        if op == "falsy":
            return not bool(cur)
        if op == "==":
            return cur == self.value
        if op == "!=":
            return cur != self.value
        if op == "in":
            try:
                return cur in self.value
            except TypeError:
                return False
        if op == "nin":
            try:
                return cur not in self.value
            except TypeError:
                return False
        # Ordered comparisons: only meaningful when both sides are comparable.
        if op in {"<", "<=", ">", ">="}:
            try:
                if op == "<":
                    return cur < self.value
                if op == "<=":
                    return cur <= self.value
                if op == ">":
                    return cur > self.value
                return cur >= self.value
            except TypeError:
                return False
        raise DecisionGraphError(f"unknown condition op: {op!r}")

    def to_dict(self) -> dict:
        if self.all is not None:
            return {"all": [c.to_dict() for c in self.all]}
        if self.any is not None:
            return {"any": [c.to_dict() for c in self.any]}
        if self.not_ is not None:
            return {"not": self.not_.to_dict()}
        d: dict[str, Any] = {"var": self.var, "op": self.op}
        if self.op not in {"truthy", "falsy"}:
            d["value"] = self.value
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "Condition | None":
        if not d:
            return None
        if "all" in d:
            return cls(all=[cls.from_dict(c) for c in d["all"]])  # type: ignore[misc]
        if "any" in d:
            return cls(any=[cls.from_dict(c) for c in d["any"]])  # type: ignore[misc]
        if "not" in d:
            return cls(not_=cls.from_dict(d["not"]))
        op = d.get("op", "truthy")
        if op not in _CMP_OPS:
            raise DecisionGraphError(f"unknown condition op: {op!r}")
        return cls(var=d.get("var"), op=op, value=d.get("value"))


# ─────────────────────────────── mutations ────────────────────────────────


@dataclass
class Mutation:
    """A single change to one variable, applied when a path is taken/entered."""

    var: str
    op: str = "set"
    value: Any = None

    def apply(self, variables: dict[str, Any]) -> None:
        op = self.op
        if op == "set":
            variables[self.var] = self.value
        elif op == "inc":
            variables[self.var] = (variables.get(self.var, 0) or 0) + (self.value if self.value is not None else 1)
        elif op == "dec":
            variables[self.var] = (variables.get(self.var, 0) or 0) - (self.value if self.value is not None else 1)
        elif op == "toggle":
            variables[self.var] = not bool(variables.get(self.var))
        elif op == "append":
            cur = variables.get(self.var)
            if not isinstance(cur, list):
                cur = [] if cur is None else [cur]
            cur.append(self.value)
            variables[self.var] = cur
        elif op == "remove":
            cur = variables.get(self.var)
            if isinstance(cur, list) and self.value in cur:
                cur.remove(self.value)
        elif op == "del":
            variables.pop(self.var, None)
        else:
            raise DecisionGraphError(f"unknown mutation op: {op!r}")

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"var": self.var, "op": self.op}
        if self.op not in {"toggle", "del"}:
            d["value"] = self.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Mutation":
        op = d.get("op", "set")
        if op not in _MUT_OPS:
            raise DecisionGraphError(f"unknown mutation op: {op!r}")
        return cls(var=d["var"], op=op, value=d.get("value"))


def _apply_all(mutations: list[Mutation], variables: dict[str, Any]) -> None:
    for m in mutations:
        m.apply(variables)


# ─────────────────────────── transitions / nodes ──────────────────────────


@dataclass
class Transition:
    """An edge out of a node.

    ``kind="choice"`` is reader-picked (a Narrative-Canvas choice branch);
    ``kind="auto"`` is engine-followed when its ``when`` passes (a condition
    branch). ``when`` gates eligibility; ``set`` mutates variables when taken.
    """

    to: str
    kind: str = "choice"
    label: str = ""
    when: Condition | None = None
    set: list[Mutation] = field(default_factory=list)

    def eligible(self, variables: dict[str, Any]) -> bool:
        return self.when is None or self.when.evaluate(variables)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"to": self.to, "kind": self.kind}
        if self.label:
            d["label"] = self.label
        if self.when is not None:
            d["when"] = self.when.to_dict()
        if self.set:
            d["set"] = [m.to_dict() for m in self.set]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        return cls(
            to=d["to"],
            kind=d.get("kind", "choice"),
            label=d.get("label", ""),
            when=Condition.from_dict(d.get("when")),
            set=[Mutation.from_dict(m) for m in d.get("set", [])],
        )


@dataclass
class Node:
    """A node in the graph.

    ``data`` is free-form (title/text/character/route/spotlight — whatever the
    consumer renders). A node with only ``auto`` transitions is a "condition
    node"; one with ``choice`` transitions is a "choice node"; ``enter_set``
    makes any node a "set node". ``kind="end"`` is terminal.
    """

    id: str
    kind: str = "content"  # "content" | "end"
    data: dict = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    enter_set: list[Mutation] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.data:
            d["data"] = self.data
        if self.transitions:
            d["transitions"] = [t.to_dict() for t in self.transitions]
        if self.enter_set:
            d["enter_set"] = [m.to_dict() for m in self.enter_set]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            kind=d.get("kind", "content"),
            data=d.get("data", {}) or {},
            transitions=[Transition.from_dict(t) for t in d.get("transitions", [])],
            enter_set=[Mutation.from_dict(m) for m in d.get("enter_set", [])],
        )


@dataclass
class DecisionGraph:
    """An authored branching graph. ``vars`` are initial variable defaults."""

    start: str
    nodes: dict[str, Node]
    vars: dict[str, VarValue] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "vars": dict(self.vars),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionGraph":
        raw_nodes = d.get("nodes", [])
        # Accept both list-of-nodes and {id: node} map shapes.
        if isinstance(raw_nodes, dict):
            nodes = {nid: Node.from_dict({**n, "id": n.get("id", nid)}) for nid, n in raw_nodes.items()}
        else:
            nodes = {}
            for n in raw_nodes:
                node = Node.from_dict(n)
                nodes[node.id] = node
        return cls(start=d["start"], nodes=nodes, vars=dict(d.get("vars", {})), meta=dict(d.get("meta", {})))


def validate_graph(graph: DecisionGraph) -> list[str]:
    """Return a list of structural errors (empty == valid).

    Hard errors only: no nodes, missing start, a transition pointing nowhere.
    Unreachable nodes are reported as ``warn:`` prefixed (soft) so a consumer
    can choose whether to treat them as fatal.
    """

    errors: list[str] = []
    if not graph.nodes:
        errors.append("graph has no nodes")
        return errors
    if graph.start not in graph.nodes:
        errors.append(f"start node {graph.start!r} not in nodes")
    for node in graph.nodes.values():
        for t in node.transitions:
            if t.to not in graph.nodes:
                errors.append(f"node {node.id!r} -> unknown target {t.to!r}")
            if t.kind not in {"choice", "auto"}:
                errors.append(f"node {node.id!r} transition has unknown kind {t.kind!r}")

    # Reachability (soft) — BFS from start over every declared edge.
    if graph.start in graph.nodes:
        seen = {graph.start}
        stack = [graph.start]
        while stack:
            cur = graph.nodes[stack.pop()]
            for t in cur.transitions:
                if t.to in graph.nodes and t.to not in seen:
                    seen.add(t.to)
                    stack.append(t.to)
        for nid in graph.nodes:
            if nid not in seen:
                errors.append(f"warn: node {nid!r} is unreachable from start")
    return errors


# ──────────────────────────────── runtime ─────────────────────────────────


@dataclass
class ChoiceView:
    """One eligible reader-pickable option, as surfaced by the runtime."""

    index: int
    to: str
    label: str


@dataclass
class View:
    """The runtime's snapshot a consumer renders: where we are + what's offered."""

    node_id: str
    kind: str
    data: dict
    choices: list[ChoiceView]
    status: str  # "running" | "ended"
    variables: dict


class DecisionRun:
    """Walks a :class:`DecisionGraph` — the "play mode" runtime.

    Construct, call :meth:`start` to position at the entry and auto-advance to
    the first decision point, then :meth:`choose` to take a reader option. The
    whole run state (``current`` / ``variables`` / ``history`` / ``status``) is
    plain data, so :meth:`to_state` / :meth:`from_state` make a run resumable.
    """

    def __init__(self, graph: DecisionGraph, variables: dict[str, Any] | None = None):
        self.graph = graph
        # Deep-copy so a list/dict-valued default var isn't shared by reference
        # across runs — an append/remove mutation must not corrupt graph.vars.
        self.variables: dict[str, Any] = copy.deepcopy({**graph.vars, **(variables or {})})
        self.current: str | None = None
        self.history: list[str] = []
        self.status: str = "pending"

    # -- internal ----------------------------------------------------------

    def _node(self, nid: str) -> Node:
        node = self.graph.nodes.get(nid)
        if node is None:
            raise DecisionGraphError(f"no such node: {nid!r}")
        return node

    def _enter(self, nid: str) -> None:
        node = self._node(nid)
        self.current = nid
        self.history.append(nid)
        _apply_all(node.enter_set, self.variables)

    def _eligible_choices(self, node: Node) -> list[Transition]:
        return [t for t in node.transitions if t.kind == "choice" and t.eligible(self.variables)]

    def _next_auto(self, node: Node) -> Transition | None:
        for t in node.transitions:
            if t.kind == "auto" and t.eligible(self.variables):
                return t
        return None

    def _settle(self) -> None:
        """Follow auto-transitions until a choice point, an end node, or a dead end."""
        steps = 0
        while True:
            node = self._node(self.current)  # type: ignore[arg-type]
            if node.kind == "end":
                self.status = "ended"
                return
            auto = self._next_auto(node)
            if auto is not None:
                steps += 1
                if steps > _MAX_AUTO_STEPS:
                    raise DecisionGraphError(
                        f"auto-transition walk exceeded {_MAX_AUTO_STEPS} steps "
                        f"(unsettling guard cycle near {self.current!r})"
                    )
                _apply_all(auto.set, self.variables)
                self._enter(auto.to)
                continue
            # No eligible auto edge. If there are eligible choices we wait for
            # the reader; otherwise this is a terminal content node.
            if self._eligible_choices(node):
                self.status = "running"
            else:
                self.status = "ended"
            return

    # -- public ------------------------------------------------------------

    def start(self) -> View:
        self._enter(self.graph.start)
        self._settle()
        return self.view()

    def view(self) -> View:
        if self.current is None:
            raise DecisionGraphError("run not started — call start() first")
        node = self._node(self.current)
        choices = [
            ChoiceView(index=i, to=t.to, label=t.label)
            for i, t in enumerate(self._eligible_choices(node))
        ]
        return View(
            node_id=node.id,
            kind=node.kind,
            data=dict(node.data),
            choices=choices,
            status=self.status,
            variables=dict(self.variables),
        )

    def choose(self, choice: int | str) -> View:
        """Take a reader option — by eligible-index or by target node id."""
        if self.status != "running":
            raise DecisionGraphError(f"cannot choose: run status is {self.status!r}")
        node = self._node(self.current)  # type: ignore[arg-type]
        eligible = self._eligible_choices(node)
        picked: Transition | None = None
        if isinstance(choice, int):
            if 0 <= choice < len(eligible):
                picked = eligible[choice]
        else:
            picked = next((t for t in eligible if t.to == choice), None)
        if picked is None:
            raise DecisionGraphError(f"no eligible choice {choice!r} at node {node.id!r}")
        _apply_all(picked.set, self.variables)
        self._enter(picked.to)
        self._settle()
        return self.view()

    def to_state(self) -> dict:
        return {
            "current": self.current,
            "variables": dict(self.variables),
            "history": list(self.history),
            "status": self.status,
        }

    @classmethod
    def from_state(cls, graph: DecisionGraph, state: dict) -> "DecisionRun":
        run = cls(graph)
        run.variables = dict(state.get("variables", {}))
        run.current = state.get("current")
        run.history = list(state.get("history", []))
        run.status = state.get("status", "pending")
        return run
