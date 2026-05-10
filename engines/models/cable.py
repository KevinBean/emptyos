"""Cable records and ampacity I/O — IEC 60287 contract surface.

Translated from the 69-field schema in
KevinBean/Cable-reticulation-tool, restricted to the fields that
Phase A of `engines/thermal/` actually consumes / produces.
Extend as later phases land.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .geometry import CableGeometry


# IEC 60287 installation types — Phase A covers buried/duct/air;
# pipe-type and riser stubbed in Phase B+.
InstallationType = Literal[
    "direct_buried",
    "in_duct",
    "in_air",
    "in_pipe_type",
    "riser",
]

BondingType = Literal[
    "solidly_bonded",   # both ends grounded — circulating sheath currents
    "single_point",     # one end grounded — induced sheath voltage, no circulating I
    "cross_bonded",     # transposed every 1/3 — equivalent to single-point at terminations
]

ConductorMaterial = Literal["Cu", "Al"]
SheathMaterial = Literal["Cu", "Al", "Pb", "PbAlloy", "Steel", "None"]
InsulationMaterial = Literal["XLPE", "EPR", "PVC", "Paper", "PILC", "Other"]


class CableLibraryEntry(BaseModel):
    """One row from a manufacturer catalogue (e.g. Nexans library)."""

    id: str
    manufacturer: str | None = None
    family: str | None = None
    rated_voltage_kv: float | None = None
    n_conductors: int = 1
    conductor_material: ConductorMaterial = "Cu"
    conductor_csa_mm2: float = Field(..., gt=0, description="Nominal cross-section, mm²")
    conductor_class: Literal[1, 2, 5] = Field(
        2,
        description=(
            "IEC 60228 conductor class — 1 (solid), 2 (stranded round, the default "
            "for ~95% of MV/HV power cables), 5 (flexible). Used when "
            "`conductor_dc_resistance_20c_ohm_per_km` is not supplied to look up "
            "the standard's max R20 from the IEC 60228 table."
        ),
    )
    conductor_dc_resistance_20c_ohm_per_km: float | None = None
    insulation_material: InsulationMaterial = "XLPE"
    insulation_max_temp_c: float = 90.0
    sheath_material: SheathMaterial = "Cu"
    geometry: CableGeometry | None = None
    metadata: dict = Field(default_factory=dict)


class CableRecord(BaseModel):
    """A cable in a project — links to library entry + applies overrides."""

    id: str
    library_id: str | None = Field(None, description="CableLibraryEntry.id, if any")
    label: str | None = None
    length_m: float | None = Field(None, gt=0)
    n_circuits: int = Field(1, ge=1, description="Cables in parallel")
    installation: InstallationType = "direct_buried"
    bonding: BondingType = "single_point"
    burial_depth_m: float = Field(1.0, gt=0)
    spacing_mode: Literal["touching", "trefoil", "flat", "custom"] = "trefoil"
    spacing_m: float | None = Field(
        None, gt=0, description="Centre-to-centre spacing for flat/custom"
    )
    duct_internal_diameter_m: float | None = Field(None, gt=0)
    duct_material: Literal["PVC", "HDPE", "Steel", "Concrete", "Other"] | None = None
    grouped_cables: int = Field(1, ge=1, description="Total in installation group (derating)")
    soil_thermal_resistivity_kmw: float | None = Field(
        None, gt=0, description="Override project default ρ_soil (K·m/W)"
    )
    ambient_temperature_c: float | None = Field(
        None, description="Override project default ambient (°C)"
    )
    overrides: dict = Field(
        default_factory=dict,
        description="Free-form per-cable field overrides resolved by the override chain.",
    )
    metadata: dict = Field(default_factory=dict)


class AmpacityInput(BaseModel):
    """Engine input — assembled from a CableRecord + library + project defaults."""

    cable: CableLibraryEntry
    installation: InstallationType
    bonding: BondingType = "single_point"
    burial_depth_m: float = 1.0
    spacing_mode: Literal["touching", "trefoil", "flat", "custom"] = "trefoil"
    spacing_m: float | None = None
    duct_internal_diameter_m: float | None = None
    duct_external_diameter_m: float | None = None
    duct_material: Literal["PVC", "HDPE", "Steel", "Concrete", "Earthenware", "FibreCement", "Other"] | None = None
    backfill_thermal_resistivity_kmw: float | None = Field(
        None, gt=0,
        description="ρ_T of controlled backfill (concrete / CBS) around the duct bank, K·m/W. Triggers Donazzi correction.",
    )
    backfill_width_m: float | None = Field(None, gt=0, description="Backfill envelope longer side (m)")
    backfill_height_m: float | None = Field(None, gt=0, description="Backfill envelope shorter side (m)")
    grouped_cables: int = 1
    soil_thermal_resistivity_kmw: float = 1.0
    ambient_temperature_c: float = 20.0
    conductor_max_temp_c: float = 90.0
    frequency_hz: float = 50.0
    sheath_loss_factor_lambda1: float | None = Field(
        None, description="If known from external calc; otherwise computed."
    )
    armour_loss_factor_lambda2: float | None = None
    solar_absorption_coefficient: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Solar absorptivity σ of the outer jacket (0..1). Typical: 0.4 "
            "for new black HDPE, 0.6 for weathered. Only applied to in_air "
            "installations; ignored for buried/duct."
        ),
    )
    solar_radiation_w_per_m2: float = Field(
        0.0,
        ge=0.0,
        description=(
            "Direct solar radiation intensity H, W/m². IEC 60287-3-1 "
            "default 1000 W/m² for tropical/desert; 700 for temperate. "
            "Only applied to in_air installations."
        ),
    )
    include_eddy_for_solid_bonding: bool = Field(
        True,
        description=(
            "Per CIGRE Guidance Point 6, eddy-current losses are always "
            "included in λ1 even for solid bonding (with the GP 31 F-factor "
            "reduction). IEC 60287-1-1 historically permitted neglecting "
            "the eddy term for solid bonding with round-stranded Cu/Al "
            "conductors. Set False to reproduce the IEC-strict baseline "
            "(e.g. CIGRE TB 880 Case 0)."
        ),
    )
    in_air_mounting_zeg: tuple[float, float, float] | None = Field(
        None,
        description=(
            "Override IEC 60287-2-2 Table 2 mounting constants (Z, E, g) for "
            "in-air installations. Auto-selected from spacing_mode + "
            "grouped_cables when None. Use to specify non-standard mountings "
            "such as covered trough (Z=0.97, E=0.80, g=0.23 ≈ TB 880 0-5)."
        ),
    )
    metadata: dict = Field(default_factory=dict)


class AmpacityResult(BaseModel):
    """Engine output — IEC 60287 forward (I→θ) or inverse (θ→I) result."""

    ampacity_a: float = Field(..., description="Continuous current rating, A")
    conductor_temperature_c: float | None = Field(
        None, description="Conductor temperature at the rated current, °C"
    )
    losses: dict = Field(
        default_factory=dict,
        description="Per-component W/m losses: conductor, dielectric, sheath, armour",
    )
    thermal_resistances: dict = Field(
        default_factory=dict,
        description="T1, T2, T3, T4 K·m/W per IEC 60287",
    )
    derating_factors: dict = Field(default_factory=dict)
    method: str = "iec60287"
    method_version: str = "0.1.0"
    notes: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
