"""Soil models — RESAP output, consumed by em + thermal."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SoilLayer(BaseModel):
    rho: float = Field(..., gt=0, description="Resistivity, Ω·m")
    thickness: float | None = Field(
        None, gt=0, description="Layer thickness, m. None = bottom (semi-infinite)."
    )
    thermal_resistivity: float | None = Field(
        None, gt=0, description="Thermal resistivity, K·m/W (IEC 60287). Optional."
    )


class SoilModel(BaseModel):
    """N-layer horizontally stratified soil.

    `layers[0]` is the surface layer; the last layer is semi-infinite
    (its `thickness` is ignored).
    """

    layers: list[SoilLayer] = Field(..., min_length=1)
    ambient_temperature: float | None = Field(
        None, description="Ambient soil temperature at burial depth, °C. IEC 60287."
    )

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    @model_validator(mode="after")
    def _validate(self) -> "SoilModel":
        for layer in self.layers[:-1]:
            if layer.thickness is None:
                raise ValueError(
                    "Only the last (bottom) layer may have thickness=None."
                )
        return self

    @classmethod
    def uniform(cls, rho: float, thermal_resistivity: float | None = None,
                ambient_temperature: float | None = None) -> "SoilModel":
        return cls(
            layers=[SoilLayer(rho=rho, thermal_resistivity=thermal_resistivity)],
            ambient_temperature=ambient_temperature,
        )
