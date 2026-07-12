"""Product Tour — aggregates [[contributes.tour.step]] across all apps.

The tour is not a static onboarding overlay. It walks the user through real
pages: each step declares a route and a CSS selector to spotlight. The
orchestrator (eos-tour.js) reads /tour/api/steps, navigates via EOS.navigate,
and uses EOS_UI.spotlight() to highlight the target element.

Steps that need a capability (e.g. `requires = ["think"]`) are auto-rewritten
to point at /system?capability=<missing> when no provider is available, so the
tour never lands on a button that can't fire.

Steps may also carry `when` / `skip_when` conditions (the
`.claude/rules/tour-steps.md` "branching tours" graduation). The eligible set
is resolved by walking a `DecisionGraph` server-side — the first consumer of the
shared branching-state engine (`emptyos/sdk/decision_graph.py`). The engine does
the skip routing (gated auto-transitions over a router/step spine), so this grows
into reader-choice branch steps later without a rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.decision_graph import (
    Condition,
    DecisionGraph,
    DecisionGraphError,
    DecisionRun,
    Node,
    Transition,
)


def _show_condition(when: dict | None, skip_when: dict | None) -> Condition | None:
    """Combine a step's `when` (show-if) and `skip_when` (hide-if) into one gate.

    Returns None (always-show) when neither is set. A malformed condition is
    dropped rather than failing the whole tour — a bad manifest entry should
    never blank the walkthrough.
    """
    parts: list[Condition] = []
    if when:
        try:
            c = Condition.from_dict(when)
            if c is not None:
                parts.append(c)
        except DecisionGraphError:
            pass
    if skip_when:
        try:
            c = Condition.from_dict(skip_when)
            if c is not None:
                parts.append(Condition(not_=c))
        except DecisionGraphError:
            pass
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else Condition(all=parts)


def build_tour_graph(show_conditions: list[Condition | None]) -> DecisionGraph:
    """A router→step spine: step *i* is visited iff its show condition passes.

    Walking this graph yields the eligible step indices in order — the engine's
    gated auto-routing does the skipping, not a flat list comprehension, so the
    same tested walker every future consumer uses carries the tour too.
    """
    nodes: dict[str, Node] = {}
    n = len(show_conditions)
    for i, show in enumerate(show_conditions):
        nxt = f"r{i + 1}"
        trans = [Transition(to=f"s{i}", kind="auto", when=show)]
        if show is not None:
            # Fallthrough only needed when the step can be gated out.
            trans.append(Transition(to=nxt, kind="auto"))
        nodes[f"r{i}"] = Node(f"r{i}", transitions=trans)
        nodes[f"s{i}"] = Node(f"s{i}", data={"i": i}, transitions=[Transition(to=nxt, kind="auto")])
    nodes[f"r{n}"] = Node(f"r{n}", kind="end")
    return DecisionGraph(start="r0", nodes=nodes)


def shown_indices(graph: DecisionGraph, variables: dict) -> list[int]:
    """Walk the tour graph and return the visited step indices, in order."""
    run = DecisionRun(graph, variables=variables)
    run.start()
    return [int(h[1:]) for h in run.history if h[:1] == "s"]


class TourApp(BaseApp):
    """Aggregates tour-step contributions and tracks completion state."""

    async def setup(self):
        self._state_dir = Path(self.kernel.config.path).parent / "data" / "apps" / "tour"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._state_dir / "state.json"

    def _read_state(self) -> dict:
        if not self._state_path.exists():
            return {"dismissed": False, "completed_at": None, "last_step": None}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"dismissed": False, "completed_at": None, "last_step": None}

    def _write_state(self, state: dict) -> None:
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    async def _missing_capabilities(self, requires: list[str]) -> list[str]:
        """Return the subset of `requires` for which no provider is available."""
        if not requires:
            return []
        snapshot = await self.kernel.capabilities.status()
        missing = []
        for cap in requires:
            rows = snapshot.get(cap) or []
            if not any(r.get("available") for r in rows):
                missing.append(cap)
        return missing

    async def _tour_variables(self) -> dict:
        """Variable context that `when` / `skip_when` conditions evaluate against.

        Convention for manifest authors: `cap:<name>` (a provider is available)
        and `app:<id>` (the app is enabled). Both are booleans; an absent
        `app:<id>` reads falsy, so `{var = "app:cad", op = "truthy"}` gates a
        step on the cad app being installed.
        """
        ctx: dict = {}
        try:
            snapshot = await self.kernel.capabilities.status()
            for cap, rows in snapshot.items():
                ctx[f"cap:{cap}"] = any(r.get("available") for r in (rows or []))
        except Exception:
            pass
        try:
            for aid in self.kernel.apps.enabled_ids():
                ctx[f"app:{aid}"] = True
        except Exception:
            pass
        return ctx

    @web_route("GET", "/api/steps")
    async def api_steps(self, request):
        """Return the resolved, capability-filtered tour step list.

        Steps are ordered by (priority, id), then the eligible subset is resolved
        by walking a DecisionGraph against the live variable context (so
        `when`/`skip_when` conditions skip steps). Surviving steps that need a
        missing capability are rewritten to /system so users always land
        somewhere actionable.
        """
        entries = self.kernel.apps.get_contributions("tour", "step")
        # 1. Ordered records (carry private condition fields for the graph walk).
        records = []
        for e in entries:
            records.append(
                {
                    "id": e.get("id") or "",
                    "group": e.get("group") or "core",
                    "priority": int(e.get("priority") or 100),
                    "route": e.get("route") or "/",
                    "spotlight": e.get("spotlight") or "",
                    "title": e.get("title") or "",
                    "body": e.get("body") or "",
                    "requires": e.get("requires") or [],
                    "_app_id": e.get("_app_id"),
                    "_when": e.get("when"),
                    "_skip_when": e.get("skip_when"),
                }
            )
        records.sort(key=lambda s: (s["priority"], s["id"]))

        # 2. Resolve eligibility through the branching-state engine.
        show_conditions = [_show_condition(r["_when"], r["_skip_when"]) for r in records]
        graph = build_tour_graph(show_conditions)
        variables = await self._tour_variables()
        eligible = shown_indices(graph, variables)

        # 3. Emit (public fields only) + capability-missing rewrite.
        steps = []
        for i in eligible:
            r = records[i]
            requires = r["requires"]
            missing = await self._missing_capabilities(requires) if requires else []
            step = {
                "id": r["id"],
                "group": r["group"],
                "priority": r["priority"],
                "route": r["route"],
                "spotlight": r["spotlight"],
                "title": r["title"],
                "body": r["body"],
                "requires": requires,
                "missing": missing,
                "_app_id": r["_app_id"],
            }
            if missing:
                first = missing[0]
                step["route"] = f"/system?capability={first}"
                step["spotlight"] = f"#cap-{first}"
                step["body"] = (
                    f"This step needs the <b>{first}</b> capability, but no provider "
                    f"is set up yet. Get it running here, then continue the tour."
                )
            steps.append(step)
        return {"steps": steps, "state": self._read_state()}

    @web_route("POST", "/api/dismiss")
    async def api_dismiss(self, request):
        """Mark the tour as dismissed/completed so the first-run banner stops."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        import time

        state = self._read_state()
        state["dismissed"] = True
        if body.get("completed"):
            state["completed_at"] = time.time()
        self._write_state(state)
        if body.get("completed"):
            await self.emit("tour:completed", {"last_step": state.get("last_step")})
        return {"ok": True, "state": state}

    @web_route("POST", "/api/state")
    async def api_state_set(self, request):
        """Persist last-step (for resume across reloads — UI also uses localStorage)."""
        body = await request.json()
        state = self._read_state()
        if "last_step" in body:
            state["last_step"] = body["last_step"]
        self._write_state(state)
        if "last_step" in body:
            await self.emit("tour:step_advanced", {"step": body["last_step"]})
        return {"ok": True, "state": state}

    @web_route("GET", "/debug/steps")
    async def debug_steps(self, request):
        """Dev introspection — raw step contributions + their resolved form."""
        from starlette.responses import HTMLResponse

        steps_resp = await self.api_steps(request)
        rows = []
        for s in steps_resp["steps"]:
            rows.append(
                f"<tr><td>{s['priority']}</td><td><code>{s['id']}</code></td>"
                f"<td>{s['_app_id']}</td><td><code>{s['route']}</code></td>"
                f"<td><code>{s['spotlight']}</code></td>"
                f"<td>{', '.join(s['requires']) or '—'}</td>"
                f"<td>{', '.join(s['missing']) or '—'}</td></tr>"
            )
        html = (
            "<!doctype html><meta charset=utf-8><title>Tour debug</title>"
            "<link rel=stylesheet href=/static/theme.css>"
            "<style>body{font-family:system-ui;padding:24px;max-width:1100px;margin:auto}"
            "table{width:100%;border-collapse:collapse;font-size:13px}"
            "th,td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}"
            "th{color:var(--text-muted);font-weight:600;font-size:11px;text-transform:uppercase}"
            "code{font-family:var(--mono,Consolas);font-size:12px}</style>"
            f"<h1>Tour steps ({len(steps_resp['steps'])})</h1>"
            f"<p style='color:var(--text-muted)'>State: <code>{json.dumps(steps_resp['state'])}</code></p>"
            "<table><thead><tr><th>Pri</th><th>id</th><th>app</th><th>route</th><th>spotlight</th><th>requires</th><th>missing</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
        return HTMLResponse(html)
