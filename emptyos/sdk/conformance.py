"""Conformance suite — manifest-declared regression cases per method.

Each method on the calculator framework can declare a list of conformance
cases: a frozen input + expected scalar outputs + tolerances. The runner
exercises every case against every claimed method, asserting that headline
numbers stay within declared tolerances.

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
    expected_fn = "_rt07_expected"     # async method, no args, returns dict of expected scalars
    methods = ["analytic", "emtp"]     # methods that MUST pass this case
    tolerances = { default_pct = 5.0, central_epr_v_pct = 2.0 }
    references = ["[[rt07-230kv-east-central]]"]

Each case at endpoint `solve` is run against every listed method. Headline
scalars from the result are compared against `expected` using either the
field-specific `<field>_pct` tolerance or `default_pct`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.sdk.base_app import BaseApp


@dataclass
class ConformanceCase:
    """One regression case declared in the manifest."""

    endpoint: str
    case_id: str
    label: str
    inputs_fn: str             # name of async BaseApp method returning payload
    expected_fn: str           # name of async BaseApp method returning {field: number}
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


def _compare_result(result: Any, expected: dict, case: ConformanceCase) -> tuple[list[dict], bool]:
    """For each expected field, compute relative pct error vs result; collect
    diffs + overall pass flag based on per-field tolerances."""
    if not isinstance(result, dict):
        return [], False
    diffs: list[dict] = []
    all_passed = True
    for field_name, exp_val in expected.items():
        if not isinstance(exp_val, (int, float)) or isinstance(exp_val, bool):
            continue
        got = result.get(field_name)
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            diffs.append({
                "field": field_name, "expected": exp_val, "got": None,
                "rel_pct": None, "tolerance_pct": case.tolerance_for(field_name),
                "passed": False, "reason": "field missing in result",
            })
            all_passed = False
            continue
        if exp_val == 0:
            rel_pct = 0.0 if got == 0 else float("inf")
        else:
            rel_pct = abs(got - exp_val) / abs(exp_val) * 100
        tol = case.tolerance_for(field_name)
        passed = rel_pct <= tol
        if not passed:
            all_passed = False
        diffs.append({
            "field": field_name, "expected": exp_val, "got": got,
            "rel_pct": round(rel_pct, 4), "tolerance_pct": tol, "passed": passed,
        })
    return diffs, all_passed
