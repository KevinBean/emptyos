"""Reference validation framework for computation-heavy apps.

Validates API responses against known reference values with configurable
tolerances. Any app can define reference cases as JSON files in tests/references/.

Tolerance types:
    exact:    {"value": true}              — must match exactly
    abs:      {"value": 0.42, "abs": 0.01} — within ±absolute
    rel:      {"value": 800, "rel": 0.05}  — within ±5%
    range:    {"range": [2, 15]}           — between min and max (inclusive)
    gt / lt:  {"gt": 0}                    — greater/less than
    not_null: {"not_null": true}           — must be present and non-null

Reference JSON structure:
    {
      "app": "cable",
      "endpoint": "/cable/api/rating/calculate",
      "cases": [
        {
          "name": "TB880 Case 0-1",
          "source": "CIGRE TB 880",
          "input": {...},
          "expected": {
            "thermal.t1_insulation": {"value": 0.4199, "abs": 0.001},
            ...
          }
        }
      ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FieldResult:
    """Result of validating one field against its expected value."""
    field: str
    passed: bool
    message: str
    actual: object = None
    expected: object = None


@dataclass
class CaseResult:
    """Result of validating one reference case."""
    name: str
    source: str
    passed: bool
    fields: list[FieldResult]

    @property
    def failures(self) -> list[FieldResult]:
        return [f for f in self.fields if not f.passed]


def _get_nested(data: dict, dot_path: str) -> object:
    """Resolve a dot-separated path like 'thermal.t1_insulation' into a value."""
    parts = dot_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


_MISSING = object()  # sentinel for missing fields


def validate_field(actual_value: object, spec: dict, field_name: str) -> FieldResult:
    """Validate a single field against its expected spec."""
    if actual_value is _MISSING:
        return FieldResult(field_name, False, "field not found in response")

    # not_null: must be present and non-null
    if spec.get("not_null"):
        ok = actual_value is not None
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected non-null, got {actual_value!r}",
                           actual_value)

    # gt / lt
    if "gt" in spec:
        ok = actual_value > spec["gt"]
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected > {spec['gt']}, got {actual_value}",
                           actual_value, f"> {spec['gt']}")
    if "lt" in spec:
        ok = actual_value < spec["lt"]
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected < {spec['lt']}, got {actual_value}",
                           actual_value, f"< {spec['lt']}")

    # range: [min, max]
    if "range" in spec:
        lo, hi = spec["range"]
        ok = lo <= actual_value <= hi
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected [{lo}, {hi}], got {actual_value}",
                           actual_value, f"[{lo}, {hi}]")

    # abs: value ± tolerance
    if "abs" in spec:
        expected = spec["value"]
        tol = spec["abs"]
        diff = abs(actual_value - expected)
        ok = diff <= tol
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected {expected} ±{tol}, got {actual_value} (off by {diff:.6g})",
                           actual_value, f"{expected} ±{tol}")

    # rel: value ± percentage
    if "rel" in spec:
        expected = spec["value"]
        tol_pct = spec["rel"]
        tol_abs = abs(expected * tol_pct)
        diff = abs(actual_value - expected)
        ok = diff <= tol_abs
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected {expected} ±{tol_pct*100}%, got {actual_value}",
                           actual_value, f"{expected} ±{tol_pct*100}%")

    # exact match (default when only "value" is present)
    if "value" in spec:
        expected = spec["value"]
        ok = actual_value == expected
        return FieldResult(field_name, ok,
                           "OK" if ok else f"expected {expected!r}, got {actual_value!r}",
                           actual_value, expected)

    return FieldResult(field_name, False, f"unknown spec format: {spec}")


def validate_response(response: dict, expected: dict) -> list[FieldResult]:
    """Validate all fields in expected against the response dict."""
    results = []
    for field_path, spec in expected.items():
        actual = _get_nested(response, field_path)
        results.append(validate_field(actual, spec, field_path))
    return results


def load_reference_file(path: str | Path) -> dict:
    """Load a reference JSON file."""
    with open(path) as f:
        return json.load(f)


def load_reference_cases(filename: str) -> list[dict]:
    """Load cases from a reference file in tests/references/."""
    ref_dir = Path(__file__).parent / "references"
    data = load_reference_file(ref_dir / filename)
    return data.get("cases", [])


def run_reference_case(case: dict, http_client, endpoint: str | None = None) -> CaseResult:
    """Run a single reference case against the API.

    Args:
        case: dict with 'name', 'source', 'input', 'expected'
        http_client: httpx client (from conftest)
        endpoint: API endpoint (overrides case-level endpoint)
    """
    ep = endpoint or case.get("endpoint", "")
    resp = http_client.post(ep, json=case.get("input", {}))
    if resp.status_code != 200:
        return CaseResult(
            name=case.get("name", "?"),
            source=case.get("source", ""),
            passed=False,
            fields=[FieldResult("_http", False, f"HTTP {resp.status_code}")],
        )

    data = resp.json()
    fields = validate_response(data, case.get("expected", {}))
    return CaseResult(
        name=case.get("name", "?"),
        source=case.get("source", ""),
        passed=all(f.passed for f in fields),
        fields=fields,
    )


def validate_references(path: str | Path, http_client) -> list[CaseResult]:
    """Load a reference file and run all cases. Returns list of CaseResults."""
    data = load_reference_file(path)
    endpoint = data.get("endpoint", "")
    results = []
    for case in data.get("cases", []):
        case_endpoint = case.get("endpoint", endpoint)
        result = run_reference_case(case, http_client, endpoint=case_endpoint)
        results.append(result)
    return results
