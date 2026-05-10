"""Reference-table loaders — IEC 60228 conductor R20 + soil ρ_T.

Indexed lookups against the JSON tables under ``engines/thermal/data/``.
Loaders are cached on first call so repeated lookups don't re-parse JSON.

Why JSON not Python dicts: the tables are human-edited reference data,
not code. Engineers can patch a value (e.g. add a new soil type, fix a
typo against the latest IEC revision) without touching the engine — and
the Python side validates shape on load rather than trusting whatever
it imports.

Public API:
    iec60228_max_r20(csa_mm2, material, conductor_class=2)
        Returns max DC resistance (Ω/km) at 20°C, or None if not in table.
    soil_thermal_resistivity(soil_type, *, prefer_range=None)
        Returns ρ_T (K·m/W) for a named soil; ``prefer_range="upper"``
        gives the conservative end (higher ρ_T → lower ampacity).
    soil_types() / backfill_materials()
        List of available keys for UI pickers.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

_DATA_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def _iec60228() -> dict:
    with open(_DATA_DIR / "iec60228.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _soil() -> dict:
    with open(_DATA_DIR / "soil_resistivity.json", encoding="utf-8") as f:
        return json.load(f)


def iec60228_max_r20(
    csa_mm2: float,
    material: str = "Cu",
    conductor_class: int | str = 2,
) -> float | None:
    """Maximum DC resistance at 20 °C from IEC 60228, Ω/km.

    Returns ``None`` when the (csa, material, class) tuple isn't in the
    table — caller should fall back to the bare ρ20 / A formula. CSA
    matching is exact; non-standard sizes (e.g. 240.5 mm²) won't resolve
    and should be rounded to the nearest IEC-listed size by the caller.

    `conductor_class`: 1 (solid), 2 (stranded — most common), 5 (flexible).
    Class 6 (highly flexible) is rarely used in power applications and
    isn't shipped in the table.
    """
    cls_key = str(conductor_class)
    classes = _iec60228()["classes"]
    if cls_key not in classes:
        return None
    by_material = classes[cls_key].get(material)
    if by_material is None:
        return None
    # JSON keys are strings; CSAs are stored as the canonical "1.5" / "240" / "0.5"
    # form. Normalise the lookup so 240 and 240.0 both hit "240".
    csa_key = str(int(csa_mm2)) if float(csa_mm2).is_integer() else str(csa_mm2)
    return by_material.get(csa_key)


def soil_thermal_resistivity(
    soil_type: str,
    *,
    prefer_range: Literal["lower", "typical", "upper"] = "typical",
) -> float | None:
    """ρ_T (K·m/W) for a named soil or backfill material.

    ``prefer_range`` selects which end of the published range to return:
    ``"lower"`` gives the optimistic end (lower ρ_T → higher ampacity,
    appropriate for benchmark studies), ``"upper"`` gives the conservative
    end (design margin), ``"typical"`` gives the mid-range value.

    Returns ``None`` for unknown keys; caller can ``soil_types()`` /
    ``backfill_materials()`` to discover valid names.
    """
    table = _soil()
    entry = table["soil_types"].get(soil_type) or table["backfill_materials"].get(soil_type)
    if entry is None:
        return None
    if prefer_range == "typical":
        return entry.get("typical")
    rng = entry.get("range")
    if not rng:
        return entry.get("typical")
    return rng[0] if prefer_range == "lower" else rng[1]


def soil_types() -> list[str]:
    """List of soil-type keys for UI dropdowns."""
    return list(_soil()["soil_types"].keys())


def backfill_materials() -> list[str]:
    """List of backfill-material keys for UI dropdowns."""
    return list(_soil()["backfill_materials"].keys())


def iec_60287_default_soil_resistivity() -> float:
    """The IEC 60287 default ρ_T (K·m/W) — 1.0 for moist temperate soil.

    Use as a fallback when no project-specific value is available. Lower
    than this is the design-conservative move only when the site has a
    measured value; otherwise the standard's default is the right
    starting point.
    """
    return _soil()["iec_60287_default"]
