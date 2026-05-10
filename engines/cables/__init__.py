"""engines/cables — cable-specific physics that doesn't fit elsewhere.

`engines/lines/` covers electrical R/L/C parameters (Carson + Pollaczek).
`engines/thermal/` covers ampacity / temperature.
`engines/cable-mech/` (planned) covers pulling tension / drum / sidewall.

This package is the home for the rest:
    electrical_stress  — coaxial E-field at conductor & insulation surfaces
                         under nominal + impulse voltages (closed-form).
    insulation         — (Phase B) dielectric strength curves, paper/oil etc.
    sheath_voltage     — (Phase B) IEEE 575 induced sheath voltage in
                         cross-bonded systems.
"""

from .electrical_stress import (
    nominal_field_strength,
    impulse_field_strength,
    StressResult,
)

__all__ = ["nominal_field_strength", "impulse_field_strength", "StressResult"]
