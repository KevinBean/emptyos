"""Conformance suite — manifest-declared regression cases per method.

Each method on the calculator framework can declare a list of conformance
cases: a frozen input + expected scalar outputs + tolerances. The runner
exercises every case against every claimed method, asserting that headline
numbers stay within declared tolerances and that bool or string anchors match.

This is **not** a replacement for granular Python unit tests (which validate
internal physics, intermediate values, KCL closure, edge cases). Conformance
is the *contract* — "this method claims to reproduce RT-07 within ±2% on
central EPR" — that's load-bearing for engineering deliverables and surfaces
on `/system` so users can see what each calculator is validated against.

Manifest shape:

    [[provides.conformance.solve]]
    case_id = "rt07"
    label = "RT-07 230 kV East Central (CDEGS reference)"
    inputs_fn = "_load_rt07_payload"   # async method on the app, no args, returns payload
    expected_fn = "_rt07_expected"     # async method, no args, returns {field: number | bool | str}
    methods = ["analytic", "emtp"]     # methods that MUST pass this case
    tolerances = { default_pct = 5.0, central_epr_v_pct = 2.0 }
    references = ["[[rt07-230kv-east-central]]"]

Each case at endpoint `solve` is run against every listed method. A numeric
anchor is compared against the result using either the field-specific
`<field>_pct` tolerance or `default_pct`; a bool or string anchor must match
exactly; any other anchor, an empty expected mapping, or a result that is not
a mapping fails with a stated reason rather than being skipped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from emptyos.sdk.decorators import web_route

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


@dataclass
class ConformanceCase:
    """One regression case declared in the manifest."""

    endpoint: str
    case_id: str
    label: str
    inputs_fn: str             # name of async BaseApp method returning payload
    expected_fn: str           # name of async BaseApp method returning {field: number | bool | str}
    methods: list[str]         # method ids that must pass this case
    tolerances: dict[str, float]
    references: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def tolerance_for(self, field_name: str) -> float:
        """Pct tolerance for a specific output field. Falls back to default."""
        per_field = self.tolerances.get(f"{field_name}_pct")
        if per_field is not None:
            return float(per_field)
        return float(self.tolerances.get("default_pct", 5.0))


class ConformanceRegistry:
    """Per-app registry of conformance cases, organized by endpoint."""

    def __init__(self):
        self._by_endpoint: dict[str, dict[str, ConformanceCase]] = {}

    @classmethod
    def from_manifest(cls, manifest_provides: dict) -> ConformanceRegistry:
        reg = cls()
        section = manifest_provides.get("conformance") or {}
        if not isinstance(section, dict):
            return reg
        for endpoint, items in section.items():
            if not isinstance(items, list):
                continue
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if not raw.get("case_id") or not raw.get("inputs_fn") or not raw.get("expected_fn"):
                    continue
                case = ConformanceCase(
                    endpoint=endpoint,
                    case_id=raw["case_id"],
                    label=raw.get("label") or raw["case_id"],
                    inputs_fn=raw["inputs_fn"],
                    expected_fn=raw["expected_fn"],
                    methods=list(raw.get("methods") or []),
                    tolerances=dict(raw.get("tolerances") or {}),
                    references=list(raw.get("references") or []),
                    raw=dict(raw),
                )
                reg._by_endpoint.setdefault(endpoint, {})[case.case_id] = case
        return reg

    def endpoints(self) -> list[str]:
        return list(self._by_endpoint.keys())

    def list(self, endpoint: str) -> list[ConformanceCase]:
        return list((self._by_endpoint.get(endpoint) or {}).values())

    def get(self, endpoint: str, case_id: str) -> ConformanceCase | None:
        return (self._by_endpoint.get(endpoint) or {}).get(case_id)


# ── Runner ────────────────────────────────────────────────────────────


async def run_case(
    app: BaseApp,
    case: ConformanceCase,
    method_id: str | None = None,
) -> dict:
    """Run one conformance case against one method (or all listed methods).

    Returns a dict with a per-method breakdown:

        {
          "case_id": "...", "endpoint": "...", "label": "...",
          "expected": {field: value, ...},
          "methods": {
              "<method_id>": {
                  "passed": bool, "result": {...}, "diffs": [...],
                  "runtime_s": float, "error": str | None,
              }, ...
          },
          "passed": bool,   # overall — all methods passed
        }
    """
    inputs_loader = getattr(app, case.inputs_fn, None)
    if inputs_loader is None:
        raise RuntimeError(f"app missing inputs_fn '{case.inputs_fn}'")
    expected_loader = getattr(app, case.expected_fn, None)
    if expected_loader is None:
        raise RuntimeError(f"app missing expected_fn '{case.expected_fn}'")

    payload = await inputs_loader()
    expected = await expected_loader()
    if not isinstance(expected, dict):
        # A broken loader is an error the scan must report, like a missing one —
        # not a quiet per-method "failed" with no diffs for a consumer to show.
        raise RuntimeError(
            f"expected_fn '{case.expected_fn}' returned {type(expected).__name__}, "
            f"not a mapping of expected values"
        )

    methods_to_run = [method_id] if method_id else list(case.methods)
    if not methods_to_run:
        methods_to_run = [m["id"] for m in app.list_methods(case.endpoint)]

    breakdown: dict[str, dict] = {}
    overall_pass = True
    for mid in methods_to_run:
        try:
            spec = app.method_registry.resolve(case.endpoint, mid)
        except (ValueError, AttributeError):
            spec = None
        if spec is None or spec.id != mid:
            breakdown[mid] = {"passed": False, "error": f"method '{mid}' not found"}
            overall_pass = False
            continue
        ok, reason = spec.is_available(app)
        if not ok:
            breakdown[mid] = {"passed": False, "error": reason, "skipped": True}
            # Skipped methods don't fail the case (e.g. opendss unavailable
            # locally — manifest still claims it should pass when available).
            continue
        t0 = time.monotonic()
        try:
            result = await spec.run(app, payload)
        except Exception as e:  # noqa: BLE001
            breakdown[mid] = {
                "passed": False, "error": str(e),
                "runtime_s": round(time.monotonic() - t0, 3),
            }
            overall_pass = False
            continue
        runtime_s = round(time.monotonic() - t0, 3)
        diffs, method_pass = _compare_result(result, expected, case)
        breakdown[mid] = {
            "passed": method_pass,
            "result": result if isinstance(result, dict) else {"value": str(result)},
            "diffs": diffs,
            "runtime_s": runtime_s,
            "error": None,
        }
        if not method_pass:
            overall_pass = False

    return {
        "case_id": case.case_id,
        "endpoint": case.endpoint,
        "label": case.label,
        "references": list(case.references),
        "expected": expected,
        "methods": breakdown,
        "passed": overall_pass,
    }


def _row(field_name, expected, got, passed: bool, *, rel_pct=None,
         tolerance_pct=None, reason: str = "") -> dict:
    """One diff row. ``reason`` is present only when it explains a failure."""
    row = {"field": field_name, "expected": expected, "got": got,
           "rel_pct": rel_pct, "tolerance_pct": tolerance_pct, "passed": passed}
    if reason:
        row["reason"] = reason
    return row


def _compare_result(result: Any, expected: dict, case: ConformanceCase) -> tuple[list[dict], bool]:
    """Compare each expected field against the result; return diffs + overall pass.

    A number compares by relative error against the field's tolerance. A bool or
    a string compares by exact equality — neither has a tolerance — and a bool is
    only ever matched by a bool, because ``True == 1`` in Python.

    Anything else an expected mapping carries (``None``, a list, a dict) cannot
    be compared, so that field FAILS and says why. Such fields used to be
    skipped: a case could declare an expectation that was never evaluated, and
    a case whose expected values were all non-numeric passed with nothing
    compared at all. An empty expected mapping fails for the same reason.
    """
    # Each early return carries a reason row. An empty diff list with a failing
    # verdict gives every consumer — the drift scan, the gate, the panel —
    # nothing to report, which is the silence this function exists to prevent.
    if not isinstance(expected, dict):
        return [_row(None, None, None, False, reason=(
            f"the expected values are a {type(expected).__name__}, not a mapping"))], False
    if not isinstance(result, dict):
        return [_row(None, None, None, False, reason=(
            f"the method returned a {type(result).__name__}, not a mapping of results"))], False
    if not expected:
        return [_row(None, None, None, False, reason=(
            "the case declares no expected values, so nothing would be compared"))], False
    diffs: list[dict] = []
    for field_name, exp_val in expected.items():
        got = result.get(field_name)
        if isinstance(exp_val, (bool, str)):
            same_kind = isinstance(got, bool) if isinstance(exp_val, bool) else isinstance(got, str)
            passed = same_kind and got == exp_val
            diffs.append(_row(field_name, exp_val, got, passed, reason="" if passed else (
                "field missing in result" if field_name not in result
                else "exact match required")))
        elif not isinstance(exp_val, (int, float)):
            diffs.append(_row(field_name, exp_val, got, False, reason=(
                "the expected value is not a number, bool or string, so it cannot be compared")))
        elif not isinstance(got, (int, float)) or isinstance(got, bool):
            diffs.append(_row(field_name, exp_val, None, False,
                              tolerance_pct=case.tolerance_for(field_name),
                              reason="field missing in result"))
        else:
            if exp_val == 0:
                rel_pct = 0.0 if got == 0 else float("inf")
            else:
                rel_pct = abs(got - exp_val) / abs(exp_val) * 100
            tol = case.tolerance_for(field_name)
            diffs.append(_row(field_name, exp_val, got, rel_pct <= tol,
                              rel_pct=round(rel_pct, 4), tolerance_pct=tol))
    return diffs, all(d["passed"] for d in diffs)


# ── HTTP surface mixin ────────────────────────────────────────────────


class CalculatorRoutesMixin:
    """The generic calculator-framework HTTP surface every calculator app shares.

    Inherit alongside BaseApp to expose the routes the shared frontend
    helpers expect, generic over **every** endpoint in the app's method +
    conformance registries (single- and multi-endpoint apps alike):

        GET  /api/methods            → {"<endpoint>": [methods...], ...} — the
                                       per-endpoint map ``EOS_UI.methodPicker``
                                       indexes by its ``endpoint`` option; for
                                       single-endpoint apps also carries the
                                       ``endpoint`` + ``methods`` convenience keys
        GET  /api/conformance        → {"cases": [...]} (each row carries
                                       ``endpoint``; ``?endpoint=`` narrows)
        POST /api/conformance/run    → {"results": [...]} — ``{}`` runs every
                                       case at every endpoint (the shared
                                       panel's POST); ``endpoint`` / ``case_id``
                                       / ``method_id`` narrow.
        GET  /api/saved              → {"saved": [...]} — apps using
                                       ``self.save_calculation()`` get this for
                                       free; override for a bespoke shape.

    Extracted 2026-06-10 from nine hand-rolled near-identical sets across the
    engineering apps (cable-stress, cable-network, earthing, interference,
    lightning, overhead-line, power-study, short-circuit, soil); the
    ``/api/saved`` pair joined 2026-08-27 from the third byte-identical copy.
    Use via inheritance: ``class MyApp(CalculatorRoutesMixin, BaseApp): ...``
    — the handlers register through the MRO walk in
    ``BaseApp._get_decorated``; an app-local method of the same name still
    wins when a bespoke shape is needed.
    """

    @web_route("GET", "/api/methods")
    async def api_list_methods(self, request) -> dict:
        reg = self.method_registry
        eps = reg.endpoints()
        out: dict = {ep: self.list_methods(ep) for ep in eps}
        if len(eps) == 1:
            # Single-endpoint convenience keys (the historical tagged shape).
            out["endpoint"] = eps[0]
            out["methods"] = out[eps[0]]
        return out

    @web_route("GET", "/api/conformance")
    async def api_list_conformance(self, request) -> dict:
        wanted = request.query_params.get("endpoint")
        reg = self.conformance_registry
        eps = [wanted] if wanted else reg.endpoints()
        cases: list[dict] = []
        for ep in eps:
            for c in self.list_conformance(ep):
                cases.append({**c, "endpoint": ep})
        return {"cases": cases}

    @web_route("POST", "/api/conformance/run")
    async def api_run_conformance(self, request) -> dict:
        body = await self.safe_json(request)
        endpoint = body.get("endpoint")
        case_id = body.get("case_id")
        method_id = body.get("method_id")
        reg = self.conformance_registry
        # A case_id is unique across endpoints — locate its endpoint when unset.
        if case_id and not endpoint:
            endpoint = next((ep for ep in reg.endpoints() if reg.get(ep, case_id)), None)
            if endpoint is None:
                return {"error": f"unknown conformance case '{case_id}'"}
        eps = [endpoint] if endpoint else reg.endpoints()
        results: list[dict] = []
        try:
            for ep in eps:
                out = await self.run_conformance(ep, case_id=case_id, method_id=method_id)
                results += out if isinstance(out, list) else [out]
        except ValueError as e:
            return {"error": str(e)}
        return {"results": results}

    @web_route("GET", "/api/saved")
    async def api_saved(self, request) -> dict:
        """Generic saved-calculations list for apps using ``self.save_calculation()``.

        Returns ``{"saved": [...]}`` — empty for apps that don't save via that
        convention (harmless; ``list_calculations()`` reads an app-scoped vault
        glob that's simply empty). An app needing a different shape (e.g.
        sc-force's ``{"bays": [...]}`` over its own domain store) defines its
        own ``api_saved``/``list_saved`` directly on the app class — ordinary
        Python method resolution makes that win over this mixin method, no
        override ceremony needed.

        Extracted 2026-08-27 from the third byte-identical copy (busbar-rating,
        ct-vt-sizing, relay-coordination).
        """
        return {"saved": await self.list_saved()}

    async def list_saved(self) -> list[dict]:
        return self.list_calculations()

    async def run_method_endpoint(self, endpoint: str, request, *,
                                  label: str | None = None) -> dict:
        """Dispatch a POST body through one method-registry endpoint.

        The body of every calculator's per-endpoint compute route: read the
        payload, resolve the requested method (or the default), run it, and
        return the standard ``{ok, result, method, provenance}`` envelope —
        with a caller error surfaced in-band as ``{"error": ...}`` rather
        than becoming a 500.

        Apps keep their own thin ``@web_route`` per endpoint, because the URL
        (``/api/rect``, ``/api/knee-point``, …) IS the app's public contract
        and should stay visible in the app::

            @web_route("POST", "/api/lighting")
            async def api_lighting(self, request) -> dict:
                return await self.run_method_endpoint("lighting", request)

        ``label`` overrides the noun in the unexpected-exception message when
        the endpoint id reads poorly to a user.

        Extracted 2026-08-28 from **fourteen byte-identical route bodies**
        across six calculator apps (busbar-rating, ct-vt-sizing,
        relay-coordination, transformer-rating, battery-sizing, aux-services)
        — normalising for the endpoint name and the error noun left exactly
        one distinct implementation.
        """
        body = await self.safe_json(request)
        try:
            spec = self.resolve_method(endpoint, body.get("method"))
            # Hand the method fn the INPUTS, not the envelope. `method` is this
            # function's own routing key and is fully consumed by the line
            # above, so passing it on makes it indistinguishable from a field
            # the caller supplied.
            #
            # A calculator that refuses undeclared inputs -- the right posture,
            # since a misspelled field must not be silently ignored -- then
            # refuses every request that names a method, which is exactly the
            # request that needed the registry. cable-bonding is the only one
            # of the seven consumers strict enough to have been bitten so far;
            # the next one inherits the fix instead of rediscovering it.
            #
            # Verified safe across all seven: none reads `method` from its
            # payload, because the mixin is what reads it.
            result = await spec.run(self, {k: v for k, v in body.items()
                                           if k != "method"})
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001 — surfaced in-band, never a 500
            return {"error": f"{label or endpoint} failed: {e}"}
        return {"ok": True, "result": result, "method": spec.id,
                "provenance": self.last_compute_provenance(endpoint)}
