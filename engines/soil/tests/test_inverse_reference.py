"""Ship-blocker integration test: invert the RS_TUT1.F09 measurements.

Starting from raw measurements (no prior knowledge of the converged model),
auto-estimate an initial guess and run LM. The result must:
  1. Land in the same basin as the F09's reported converged model
     (parameters within a few percent — equivalence-problem allows some drift)
  2. Achieve RMS at least as good as the F09 reports (1.8882 %); since LM is a
     more aggressive optimiser than RESAP's default Steepest Descent, our RMS
     is typically lower.
"""

import pytest
from engines.soil.geometry import ElectrodeArray
from engines.soil.inverse import InversionConfig, Measurement, invert


# RS_TUT1.F09 raw measurements (Wenner, metric)
MEASUREMENTS = [
    Measurement(ElectrodeArray("wenner", (2.0,)), 190.0),
    Measurement(ElectrodeArray("wenner", (4.0,)), 183.0),
    Measurement(ElectrodeArray("wenner", (8.0,)), 147.0),
    Measurement(ElectrodeArray("wenner", (16.0,)), 118.0),
    Measurement(ElectrodeArray("wenner", (32.0,)), 107.0),
]

# F09's reported converged model
F09_RHO_TOP = 190.0
F09_RHO_BOT = 105.5163
F09_H1 = 4.733190
F09_K = -0.28589
F09_CONTRAST = 0.55535
F09_RMS_PCT = 1.8882


@pytest.fixture(scope="module")
def reference_inversion():
    cfg = InversionConfig(n_layers=2)
    return invert(MEASUREMENTS, cfg)


def test_rms_at_least_as_good_as_reference(reference_inversion):
    """LM should match or beat RESAP's RMS — RESAP defaults to Steepest Descent
    which stops at the user's target_accuracy (2.5%); LM finds the true optimum.
    """
    rms = reference_inversion.rms_error_pct
    assert rms <= F09_RMS_PCT + 0.05, f"got RMS = {rms:.4f}% (reference {F09_RMS_PCT}%)"


def test_rho_top_in_same_basin(reference_inversion):
    """ρ₁ within ±5% of F09's 190 Ω·m."""
    rho_top = reference_inversion.soil_model.resistivities[0]
    assert rho_top == pytest.approx(F09_RHO_TOP, rel=0.05), f"got {rho_top:.4f}"


def test_rho_bottom_in_same_basin(reference_inversion):
    """ρ₂ within ±5% of F09's 105.52 Ω·m."""
    rho_bot = reference_inversion.soil_model.resistivities[1]
    assert rho_bot == pytest.approx(F09_RHO_BOT, rel=0.05), f"got {rho_bot:.4f}"


def test_thickness_in_same_basin(reference_inversion):
    """h₁ within ±5% of F09's 4.733 m."""
    h1 = reference_inversion.soil_model.thicknesses[0]
    assert h1 == pytest.approx(F09_H1, rel=0.05), f"got {h1:.6f}"


def test_reflection_coefficient_in_same_basin(reference_inversion):
    """K within ±0.03 of F09's -0.28589 (basin tolerance)."""
    k = reference_inversion.reflection_coefficients[0]
    assert k == pytest.approx(F09_K, abs=0.03), f"got {k:.5f}"


def test_contrast_ratio_in_same_basin(reference_inversion):
    """contrast within ±0.03 of F09's 0.55535 (basin tolerance)."""
    c = reference_inversion.contrast_ratios[0]
    assert c == pytest.approx(F09_CONTRAST, abs=0.03), f"got {c:.5f}"


def test_inversion_converged(reference_inversion):
    """Should report a successful convergence reason, not max-iter."""
    assert reference_inversion.convergence_reason != "max_iter_no_convergence"


def test_inversion_returns_provenance(reference_inversion):
    """Result carries reflection coefficients, contrasts, and per-point discrepancy."""
    r = reference_inversion
    assert len(r.reflection_coefficients) == 1
    assert len(r.contrast_ratios) == 1
    assert len(r.per_point_discrepancy_pct) == 5
    assert all(d >= 0 for d in r.per_point_discrepancy_pct)


def test_locking_skips_optimisation():
    """Locking all parameters should bypass the optimiser entirely."""
    from engines.soil.soil_model import SoilModel
    cfg = InversionConfig(
        n_layers=2,
        initial_model=SoilModel(resistivities=(190.0, 105.5163), thicknesses=(4.733190,)),
        locked_resistivities=(True, True),
        locked_thicknesses=(True,),
    )
    res = invert(MEASUREMENTS, cfg)
    assert res.iterations == 0
    assert res.convergence_reason == "all_parameters_locked"
    # Diagnostics still computed against the locked model
    assert res.rms_error_pct == pytest.approx(F09_RMS_PCT, abs=0.10)


def test_locking_one_param_constrains_optimiser():
    """Lock ρ_1 to a known-wrong value; optimiser must work around it."""
    from engines.soil.soil_model import SoilModel
    cfg = InversionConfig(
        n_layers=2,
        initial_model=SoilModel(resistivities=(200.0, 100.0), thicknesses=(5.0,)),
        locked_resistivities=(True, False),
    )
    res = invert(MEASUREMENTS, cfg)
    assert res.soil_model.resistivities[0] == pytest.approx(200.0)
