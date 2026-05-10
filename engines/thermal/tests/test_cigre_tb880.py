"""CIGRE TB 880 regression — Decision Gate 2 for cable Phase A→B.

Each fixture in `engines/thermal/validation/cigre_tb880/cases/` runs
through `compute_ampacity` and is asserted against the brochure value
within tolerance (default ±0.5 A per CIGRE WG B1.56 reproducibility).

If the cases/ directory is empty, the parametrize collapses to a
single skipped test so pytest is green but the gate is visibly open.
"""

from __future__ import annotations

import pytest

from engines.models import AmpacityInput
from engines.thermal.iec60287 import compute_ampacity
from engines.thermal.validation.cigre_tb880 import load_cases


_cases = load_cases()


@pytest.mark.parametrize("case", _cases or [None], ids=lambda c: getattr(c, "id", "no-fixtures"))
def test_cigre_tb880_case(case):
    if case is None:
        pytest.skip("CIGRE TB 880 fixtures not yet ported — see cases/PLACEHOLDER.md")

    inp = AmpacityInput(**case.input)
    result = compute_ampacity(inp)

    expected_i = case.expected["ampacity_a"]
    tolerance = case.expected.get("tolerance_a", 0.5)
    actual_i = result.ampacity_a

    assert abs(actual_i - expected_i) <= tolerance, (
        f"{case.id} ({case.title}): expected {expected_i:.2f} ± {tolerance:.2f} A, "
        f"got {actual_i:.2f} A (Δ={actual_i - expected_i:+.2f} A)"
    )

    # Optional T1/T4 asserts when the fixture supplies them
    for k in ("T1", "T2", "T3", "T4"):
        if k in case.expected and k in result.thermal_resistances:
            exp = case.expected[k]
            tol = case.expected.get(f"tolerance_{k}", 0.05 * abs(exp) + 1e-3)
            got = result.thermal_resistances[k]
            assert abs(got - exp) <= tol, f"{case.id}: {k} expected {exp}±{tol}, got {got}"
