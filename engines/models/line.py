"""Line / cable electrical parameters — TRALIN-style output.

Output of engines/lines/ for both overhead (Carson) and cable
(Pollaczek/Wedepohl, Phase C). Consumed by sim (branch impedance)
and em (network coupling).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LineParameters(BaseModel):
    """Per-unit-length series + shunt matrices (per-phase basis).

    All matrices stored as nested lists for serialization; convert to
    numpy at the call site. Frequency in Hz; matrices at that frequency.
    """

    frequency_hz: float = Field(..., gt=0)
    n_phases: int = Field(..., ge=1)
    r: list[list[float]] = Field(..., description="Resistance matrix, Ω/m")
    l: list[list[float]] = Field(..., description="Inductance matrix, H/m")
    g: list[list[float]] | None = Field(None, description="Conductance matrix, S/m")
    c: list[list[float]] = Field(..., description="Capacitance matrix, F/m")
    method: str = Field("carson", description="Computation method: carson | pollaczek | …")
    metadata: dict = Field(default_factory=dict)
