"""IEC 60287 — steady-state cable ampacity.

Decomposition follows the standard's structure:
    conductor_losses     — AC resistance, skin/proximity (60287-1-1)
    dielectric_losses    — capacitive losses (60287-1-1 §4.2)
    sheath_losses        — λ1 induced + circulating (60287-1-1 §4.3)
    sheath_voltage       — induced standing voltage U [V] (60287-1-1 Annex C)
    armour_losses        — λ2 (Phase B)
    thermal_resistances  — T1..T4 (60287-2-1)
    installation_types   — burial/duct/air-specific T4 + corrections (60287-2-1, 60287-2-2)

The orchestration function is in `core.py`. Each module is pure
Python; numpy not required at Phase A.

Reference: IEC 60287-1-1:2014, IEC 60287-2-1:2015, IEC 60287-2-2:2019.
Algorithm cross-checked against KevinBean/Cable-reticulation-tool
(JS implementation, CIGRE TB 880 validated).
"""

from .core import compute_ampacity, compute_conductor_temperature
from .sheath_voltage import (
    induced_field_per_metre,
    standing_voltage,
    standing_voltage_cross_bonded,
    standing_voltage_single_point,
)

__all__ = [
    "compute_ampacity",
    "compute_conductor_temperature",
    "induced_field_per_metre",
    "standing_voltage",
    "standing_voltage_cross_bonded",
    "standing_voltage_single_point",
]
