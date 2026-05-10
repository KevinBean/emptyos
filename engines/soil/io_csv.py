"""CSV measurement import + JSON result export.

CSV schema (default — Wenner):

    active, spacing_m, apparent_resistance_ohm, apparent_resistivity_ohm_m, comment
    1,      2.0,       15.1197,                 190.0,                       ""
    1,      4.0,       7.2813,                  183.0,                       ""

- `active` (1/0) is the include-in-inversion flag.
- Either `apparent_resistance_ohm` OR `apparent_resistivity_ohm_m` may be empty;
  the loader computes the missing one from the geometric factor.
- `comment` is free text, echoed in JSON output.

For non-Wenner arrays use the long-form schema (specify electrode positions
explicitly), TODO. For now only Wenner CSVs round-trip.
"""

from __future__ import annotations
import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .geometry import ElectrodeArray
from .inverse import InversionResult, Measurement


_FIELDS = (
    "active",
    "spacing_m",
    "apparent_resistance_ohm",
    "apparent_resistivity_ohm_m",
    "comment",
)


def load_wenner_csv(path: str | Path) -> list[Measurement]:
    """Load a Wenner-array CSV and return Measurement objects."""
    rows: list[Measurement] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = set(_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing required CSV columns: {sorted(missing)}")
        for i, row in enumerate(reader, start=2):
            try:
                a = float(row["spacing_m"])
            except (TypeError, ValueError):
                raise ValueError(f"{path}: row {i}: spacing_m must be a positive number")
            if a <= 0:
                raise ValueError(f"{path}: row {i}: spacing_m must be > 0")
            arr = ElectrodeArray("wenner", (a,))
            kg = arr.geometric_factor()

            r_str = (row.get("apparent_resistance_ohm") or "").strip()
            rho_str = (row.get("apparent_resistivity_ohm_m") or "").strip()
            if not r_str and not rho_str:
                raise ValueError(
                    f"{path}: row {i}: must supply apparent_resistance_ohm or apparent_resistivity_ohm_m"
                )
            if rho_str:
                rho_a = float(rho_str)
            else:
                rho_a = kg * float(r_str)
            if rho_a <= 0:
                raise ValueError(f"{path}: row {i}: apparent resistivity must be > 0")

            active_str = (row.get("active") or "1").strip().lower()
            active = active_str not in {"0", "false", "no", ""}

            rows.append(Measurement(
                array=arr,
                apparent_resistivity=rho_a,
                active=active,
                comment=row.get("comment", "") or "",
            ))
    return rows


def measurements_to_csv(path: str | Path, measurements: Iterable[Measurement]) -> None:
    """Write measurements to CSV in the same schema `load_wenner_csv` reads."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        for m in measurements:
            if m.array.kind != "wenner":
                raise NotImplementedError(
                    f"CSV export only supports Wenner arrays for now; got {m.array.kind!r}"
                )
            (a,) = m.array.spacings
            kg = m.array.geometric_factor()
            r = m.apparent_resistivity / kg
            writer.writerow({
                "active": 1 if m.active else 0,
                "spacing_m": a,
                "apparent_resistance_ohm": f"{r:.6g}",
                "apparent_resistivity_ohm_m": f"{m.apparent_resistivity:.6g}",
                "comment": m.comment,
            })


def result_to_json(
    result: InversionResult,
    *,
    measurements: list[Measurement] | None = None,
    site: str = "",
) -> dict:
    """Serialise an InversionResult to a JSON-friendly dict.

    If `measurements` is supplied, includes a per-point measured-vs-calc table.
    """
    layers = []
    rhos = result.soil_model.resistivities
    hs = list(result.soil_model.thicknesses) + [None]
    depth = 0.0
    for i, (rho, h) in enumerate(zip(rhos, hs), start=1):
        layers.append({
            "layer": i,
            "rho_ohm_m": rho,
            "thickness_m": h,
            "top_depth_m": depth,
        })
        if h is not None:
            depth += h

    interfaces = []
    for i, (k, c) in enumerate(zip(result.reflection_coefficients, result.contrast_ratios), start=1):
        interfaces.append({"between": [i, i + 1], "K": k, "contrast_ratio": c})

    out: dict = {
        "site": site,
        "soil_model": {"layers": layers},
        "interfaces": interfaces,
        "rms_error_pct": result.rms_error_pct,
        "average_discrepancy_pct": result.average_discrepancy_pct,
        "convergence": {
            "iterations": result.iterations,
            "reason": result.convergence_reason,
        },
        "warnings": list(result.warnings),
    }

    if result.jacobian is not None:
        out["jacobian"] = {
            "condition_number": result.jacobian.condition_number,
            "singular_values": list(result.jacobian.singular_values),
            "is_well_conditioned": result.jacobian.is_well_conditioned,
            "unresolved_directions": [
                {
                    "singular_value": d.singular_value,
                    "description": d.description,
                    "components": [{"name": n, "weight": w} for n, w in d.components],
                }
                for d in result.jacobian.unresolved_directions
            ],
        }

    if measurements is not None:
        active = [m for m in measurements if m.active]
        if len(active) == len(result.per_point_discrepancy_pct):
            out["per_point"] = []
            for m, disc in zip(active, result.per_point_discrepancy_pct):
                row: dict = {
                    "rho_meas": m.apparent_resistivity,
                    "discrepancy_pct": disc,
                    "comment": m.comment,
                }
                if m.array.kind == "wenner":
                    row["a_m"] = m.array.spacings[0]
                out["per_point"].append(row)

    return out


def save_result_json(path: str | Path, result: InversionResult, **kwargs) -> None:
    Path(path).write_text(
        json.dumps(result_to_json(result, **kwargs), indent=2),
        encoding="utf-8",
    )
