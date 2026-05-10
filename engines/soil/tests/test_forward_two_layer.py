"""Forward solver: two-layer test against the RS_TUT1.F09 reference case.

With the converged 2-layer model (ρ₁=190, ρ₂=105.5163, h₁=4.733190 m), the
forward solver must reproduce ρ_a values close to the field measurements at
each Wenner spacing. The reference file reports an RMS error of 1.89 % between
measured and computed values, so per-point agreement should be within a few %.
"""

import math
import pytest
from engines.soil.forward import forward_apparent_resistivity
from engines.soil.geometry import ElectrodeArray
from engines.soil.soil_model import SoilModel


REFERENCE_MODEL = SoilModel(
    resistivities=(190.0, 105.5163),
    thicknesses=(4.733190,),
)

# (Wenner spacing in m, measured ρ_a in Ω·m) from RS_TUT1.F09
MEASUREMENTS = [
    (2.0, 190.0),
    (4.0, 183.0),
    (8.0, 147.0),
    (16.0, 118.0),
    (32.0, 107.0),
]


@pytest.mark.parametrize("a,rho_meas", MEASUREMENTS)
def test_forward_per_point_close_to_measurement(a, rho_meas):
    """Per-point relative error should be within 5 % for the converged model.

    The reference's overall RMS is 1.89 % and worst per-point is 3.69 %, so 5 %
    leaves comfortable margin for filter quantisation differences.
    """
    arr = ElectrodeArray(kind="wenner", spacings=(a,))
    rho_calc = forward_apparent_resistivity(REFERENCE_MODEL, arr)
    rel_err = abs(rho_calc - rho_meas) / rho_meas
    assert rel_err < 0.05, f"a={a}: ρ_calc={rho_calc:.3f} vs meas={rho_meas} ({rel_err*100:.2f}%)"


def test_forward_rms_matches_reference_within_tolerance():
    """Aggregate RMS over all 5 measurements should be ≈ 1.89 % per RS_TUT1.F09."""
    sq = 0.0
    for a, rho_meas in MEASUREMENTS:
        arr = ElectrodeArray(kind="wenner", spacings=(a,))
        rho_calc = forward_apparent_resistivity(REFERENCE_MODEL, arr)
        sq += ((rho_calc - rho_meas) / rho_meas) ** 2
    rms_pct = math.sqrt(sq / len(MEASUREMENTS)) * 100.0
    # Reference reports 1.8882 %; allow ±0.5 % absolute for filter differences.
    assert rms_pct == pytest.approx(1.89, abs=0.5), f"RMS = {rms_pct:.4f} %"


def test_forward_high_precision_filter_agrees_with_default():
    """120-pt filter must agree with 61-pt to within ~0.1 % at every spacing."""
    from engines.soil.filters import j0_filter
    f61 = j0_filter("default")
    f120 = j0_filter("high")
    for a, _ in MEASUREMENTS:
        arr = ElectrodeArray(kind="wenner", spacings=(a,))
        r61 = forward_apparent_resistivity(REFERENCE_MODEL, arr, filt=f61)
        r120 = forward_apparent_resistivity(REFERENCE_MODEL, arr, filt=f120)
        rel = abs(r61 - r120) / r120
        assert rel < 1e-3, f"a={a}: 61pt={r61:.6f} 120pt={r120:.6f} (Δ={rel*100:.4f}%)"
