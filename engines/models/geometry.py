"""Geometric primitives shared across engines.

Cable cylindrical layers and bare conductor geometry. Kept minimal —
specialized geometry (mesh facets, BEM panels) lives in the consuming
engine, not here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConductorGeometry(BaseModel):
    """Bare overhead conductor cross-section (Carson)."""

    radius: float = Field(..., gt=0, description="Outer radius, m")
    gmr: float | None = Field(
        None, gt=0, description="Geometric mean radius, m. Defaults to 0.7788·radius."
    )
    bundle_count: int = Field(1, ge=1)
    bundle_spacing: float | None = Field(None, ge=0, description="Sub-conductor spacing, m")


class CableGeometry(BaseModel):
    """Concentric layered cable cross-section.

    All radii from cable axis, in metres. Layers populated as available;
    None means absent (e.g. armour-less cable → armour fields None).
    """

    conductor_diameter: float = Field(..., gt=0, description="Conductor diameter d_c, m")
    insulation_thickness: float = Field(..., gt=0, description="Insulation, t_i, m")
    inner_semicon_thickness: float = Field(0.0, ge=0, description="Inner semicon, m")
    outer_semicon_thickness: float = Field(0.0, ge=0, description="Outer semicon, m")
    sheath_thickness: float | None = Field(None, gt=0, description="Metallic sheath, m")
    sheath_inner_diameter: float | None = Field(None, gt=0, description="Sheath inner d, m")
    armour_thickness: float | None = Field(None, gt=0)
    armour_inner_diameter: float | None = Field(None, gt=0)
    overall_diameter: float = Field(..., gt=0, description="Outermost diameter D_e, m")

    @property
    def conductor_radius(self) -> float:
        return self.conductor_diameter / 2.0

    @property
    def overall_radius(self) -> float:
        return self.overall_diameter / 2.0

    @property
    def insulation_inner_diameter(self) -> float:
        return self.conductor_diameter + 2 * self.inner_semicon_thickness

    @property
    def insulation_outer_diameter(self) -> float:
        return self.insulation_inner_diameter + 2 * self.insulation_thickness
