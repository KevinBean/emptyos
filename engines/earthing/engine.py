"""EarthingEngine — kernel-loaded entry point for earthing/grounding calcs.

Apps access via ``self.engine("earthing")``. Methods:

  fit_soil_two_layer(spacings, measured) -> dict
      Wenner sounding → (ρ₁, ρ₂, h) by direct grid search.

  predict_apparent_resistivity(rho1, rho2, h, spacings) -> list[float]
      Forward Sunde model — useful for plotting fit overlay.

  grid_resistance(rho_soil, total_length, area, depth) -> float
      Sverak Rg in homogeneous soil (IEEE 80 §14).

  tolerable_voltages(t_fault, rho_soil, ...) -> dict
      Touch + step potentials per IEEE 80 §8 Dalziel criterion.

  available() -> bool
      Always True (pure-Python).
"""

from __future__ import annotations

from emptyos.sdk import BaseEngine

from .decrement import decrement_factor, projection_factor
from .eg0 import risk_assessment as eg0_risk_assessment
from .ieee80 import (
    mesh_voltage,
    plate_grid_resistance,
    step_voltage,
    sverak_grid_resistance,
    tolerable_step_voltage,
    tolerable_touch_voltage,
)
from .resap import (
    apparent_resistivity_two_layer,
    fit_two_layer_grid_search,
)
from .schwarz import (
    schwarz_grid_resistance,
    two_layer_effective_resistivity,
)
from .split_factor import estimate_split_factor


class EarthingEngine(BaseEngine):
    name = "earthing"

    async def init(self) -> None:
        return None

    async def available(self) -> bool:
        return True

    async def health_check(self) -> dict:
        return {
            "status": "ok",
            "available": True,
            "engine_version": self.manifest.raw.get("engine", {}).get("version", "?"),
            "phase": "A",
            "soil_models": ["homogeneous", "two_layer"],
            "rg_methods": ["sverak", "schwarz"],
            "voltage_curves": ["50kg", "70kg"],
        }

    # ── Soil model ──────────────────────────────────────────────────

    def fit_soil_two_layer(
        self,
        spacings_m: list[float],
        measured_rho_a_ohm_m: list[float],
        *,
        n_grid: int = 30,
        n_refine: int = 2,
    ) -> dict:
        return fit_two_layer_grid_search(
            spacings_m, measured_rho_a_ohm_m,
            n_grid=n_grid, n_refine=n_refine,
        )

    def predict_apparent_resistivity(
        self,
        rho1_ohm_m: float,
        rho2_ohm_m: float,
        h_layer1_m: float,
        spacings_m: list[float],
    ) -> list[float]:
        return apparent_resistivity_two_layer(
            rho1_ohm_m, rho2_ohm_m, h_layer1_m, spacings_m,
        )

    # ── Grid resistance ────────────────────────────────────────────

    def grid_resistance(
        self,
        rho_soil_ohm_m: float,
        grid_total_length_m: float,
        grid_area_m2: float,
        burial_depth_m: float,
    ) -> float:
        return sverak_grid_resistance(
            rho_soil_ohm_m, grid_total_length_m, grid_area_m2, burial_depth_m,
        )

    def plate_resistance(
        self, rho_soil_ohm_m: float, grid_area_m2: float,
    ) -> float:
        """IEEE 80 Eq. 55 — absolute lower-bound R_g for given soil + area."""
        return plate_grid_resistance(rho_soil_ohm_m, grid_area_m2)

    def schwarz_resistance(
        self,
        rho_soil_ohm_m: float,
        *,
        grid_total_length_m: float,
        grid_area_m2: float,
        grid_length_m: float,
        grid_width_m: float,
        burial_depth_m: float,
        conductor_diameter_m: float,
        n_rods: int = 0,
        rod_length_m: float = 0.0,
        rod_diameter_m: float = 0.016,
    ) -> dict:
        """Schwarz two-term grid+rod resistance, homogeneous soil.

        Returns the three component resistances (grid, rods, mutual) plus
        the combined R_g. Use this when the design has driven rods —
        Sverak's single-term form merges everything into L_T and loses
        the rod contribution breakdown.
        """
        return schwarz_grid_resistance(
            rho_soil_ohm_m,
            grid_total_length_m=grid_total_length_m,
            grid_area_m2=grid_area_m2,
            grid_length_m=grid_length_m,
            grid_width_m=grid_width_m,
            burial_depth_m=burial_depth_m,
            conductor_diameter_m=conductor_diameter_m,
            n_rods=n_rods,
            rod_length_m=rod_length_m,
            rod_diameter_m=rod_diameter_m,
        )

    def two_layer_effective_rho(
        self,
        rho_1_ohm_m: float,
        rho_2_ohm_m: float,
        h_layer1_m: float,
        grid_area_m2: float,
    ) -> float:
        """Equivalent uniform ρ for plugging into Sverak/Schwarz for 2-layer soil.

        IEEE 80 §14.4 Tagg/Burgsdorf simplified form. Useful when a RESAP
        sounding has fit ρ₁/ρ₂/h₁ — feed those plus the grid area in to
        get a single effective ρ for downstream Rg and tolerable-voltage
        calcs.
        """
        return two_layer_effective_resistivity(
            rho_1_ohm_m, rho_2_ohm_m, h_layer1_m, grid_area_m2,
        )

    # ── Mesh + step potentials inside the yard (IEEE 80 §16) ──────

    def mesh_voltage(
        self,
        rho_a_ohm_m: float,
        fault_current_a: float,
        *,
        grid_length_m: float,
        grid_width_m: float,
        grid_total_length_m: float,
        spacing_m: float,
        burial_depth_m: float,
        conductor_diameter_m: float,
        n_rods: int = 0,
        rod_length_m: float = 0.0,
        rods_on_perimeter: bool = False,
    ) -> dict:
        return mesh_voltage(
            rho_a_ohm_m, fault_current_a,
            grid_length_m=grid_length_m,
            grid_width_m=grid_width_m,
            grid_total_length_m=grid_total_length_m,
            spacing_m=spacing_m,
            burial_depth_m=burial_depth_m,
            conductor_diameter_m=conductor_diameter_m,
            n_rods=n_rods,
            rod_length_m=rod_length_m,
            rods_on_perimeter=rods_on_perimeter,
        )

    def step_voltage(
        self,
        rho_a_ohm_m: float,
        fault_current_a: float,
        *,
        grid_length_m: float,
        grid_width_m: float,
        grid_total_length_m: float,
        spacing_m: float,
        burial_depth_m: float,
        n_rods: int = 0,
        rod_length_m: float = 0.0,
    ) -> dict:
        return step_voltage(
            rho_a_ohm_m, fault_current_a,
            grid_length_m=grid_length_m,
            grid_width_m=grid_width_m,
            grid_total_length_m=grid_total_length_m,
            spacing_m=spacing_m,
            burial_depth_m=burial_depth_m,
            n_rods=n_rods,
            rod_length_m=rod_length_m,
        )

    # ── Annex C split factor ───────────────────────────────────────

    def estimate_split_factor(self, **kwargs) -> dict:
        """Mixed transmission + distribution Annex C estimator.

        Accepts keyword args ``n_transmission``, ``n_distribution``,
        ``z_span_transmission``, ``z_span_distribution``,
        ``r_tower_transmission``, ``r_tower_distribution``, ``r_grid``.
        """
        return estimate_split_factor(**kwargs)

    # ── Decrement & projection factors (IEEE 80 §15) ──────────────

    def decrement_factor(
        self,
        x_over_r: float,
        fault_duration_s: float,
        *,
        freq_hz: float = 50.0,
    ) -> dict:
        return decrement_factor(x_over_r, fault_duration_s, freq_hz=freq_hz)

    def projection_factor(
        self, present_3i0_a: float, future_3i0_a: float,
    ) -> dict:
        return projection_factor(present_3i0_a, future_3i0_a)

    # ── EG-0 / IEC 60479 probabilistic risk ───────────────────────

    def eg0_risk(
        self,
        touch_voltage_v: float,
        *,
        fault_duration_s: float,
        n_fault_per_year: float,
        n_exposure_per_year: float,
        exposure_duration_s: float,
        additional_resistance_ohm: float = 0.0,
    ) -> dict:
        return eg0_risk_assessment(
            touch_voltage_v,
            fault_duration_s=fault_duration_s,
            n_fault_per_year=n_fault_per_year,
            n_exposure_per_year=n_exposure_per_year,
            exposure_duration_s=exposure_duration_s,
            additional_resistance_ohm=additional_resistance_ohm,
        )

    # ── Tolerable voltages ─────────────────────────────────────────

    def tolerable_voltages(
        self,
        fault_duration_s: float,
        rho_soil_ohm_m: float,
        *,
        body_weight_kg: float = 50.0,
        rho_surface_ohm_m: float = 0.0,
        surface_layer_thickness_m: float = 0.0,
    ) -> dict:
        kwargs = dict(
            body_weight_kg=body_weight_kg,
            rho_surface_ohm_m=rho_surface_ohm_m,
            surface_layer_thickness_m=surface_layer_thickness_m,
        )
        return {
            "touch_v": tolerable_touch_voltage(fault_duration_s, rho_soil_ohm_m, **kwargs),
            "step_v": tolerable_step_voltage(fault_duration_s, rho_soil_ohm_m, **kwargs),
            "fault_duration_s": fault_duration_s,
            "body_weight_kg": body_weight_kg,
        }
