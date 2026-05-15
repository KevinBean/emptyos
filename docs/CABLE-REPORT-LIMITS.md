# Cable rating report — known limits

The cable rating report (`POST /cables/api/rating/report`) is designed for
section-by-section parity with a CYMCAP General Summary Report. This doc
enumerates the CYMCAP fields EmptyOS **does not yet compute**, so the
reader knows whether a blank cell in the exported report is "didn't
apply here" (e.g. armour loss on an unarmoured cable) or "we can't
compute this yet."

These are roadmap candidates, not blockers for the report-export feature.
Each line says *why* we don't have it and *what would need to change* to
get it.

## θ_s — sheath surface temperature
- **Status:** not computed.
- **Why:** the engine returns conductor temperature only (`AmpacityResult.conductor_temperature_c`).
  CYMCAP solves the full radial network including the conductor–sheath
  step.
- **To get it:** post-process `result.thermal_resistances` and `losses`
  to back-calculate θ_s = θ_c − I²·R_ac·T₁ inside the IEC 60287 forward
  step.

## Δθ_int — mutual heating temperature rise
- **Status:** lumped into the grouping derate factor; not decomposed
  per cable.
- **Why:** today the engine returns one scalar derate. CYMCAP shows the
  spatial Δθ contribution each adjacent cable adds to each victim cable.
- **To get it:** replace the scalar grouping derate with the per-cable
  superposition sum (IEC 60287-2-1 §2.2.6.1) and surface the resulting
  matrix.

## Backfill rectangle (size + thermal resistivity)
- **Status:** uniform soil ρ_T only.
- **Why:** the engine takes one `soil_thermal_resistivity_kmw` scalar.
- **To get it:** wire IEC 60287-2-1 §2.2.7 (backfill correction with
  shape and ρ_T inputs) OR do a small 2D FEM patch on the backfill zone.

## DLF / cyclic daily loading
- **Status:** steady-state ampacity only.
- **Why:** IEC 60853 (transient + cyclic) isn't implemented in the engine.
- **To get it:** add a cyclic-rating path. Significant engine work — IEC
  60853 is its own corpus.

## Sheath voltage phasor angle
- **Status:** magnitude only.
- **Why:** `_compute_sheath_voltage` returns `U_v` scalar plus a regime
  label. The underlying `standing_voltage` engine call could return a
  complex value but doesn't today.
- **To get it:** extend `standing_voltage` to return `{re, im}` and pass
  the angle through `_compute_sheath_voltage` → response.

## Capacitance, water-barrier ρ_T, multi-layer jacket detail
- **Status:** library schema doesn't carry these as distinct fields.
- **Why:** `CableLibraryEntry` + `CableGeometry` were sized for the
  fields the IEC 60287 analytic core needs. CYMCAP's library carries
  the cable-design intent in more depth (multi-layer jacket, water
  barrier ρ_T, insulation ε_r and tan δ for capacitance).
- **To get it:** extend `engines/models/cable.py:CableGeometry` with
  the missing fields, populate library entries, and surface in the
  report's "Cable construction" section.

## What we *do* compute (recap)

Everything in the gap-analysis table of the implementation plan that is
marked ◐ ("engine has it, not surfaced") is now surfaced by
`api_rating_calculate` and rendered by the report. Specifically:

- T₁, T₂, T₃, T₄ — full thermal resistance breakdown
- W_c, W_d, W_s, W_a, W_t — per-loss-source and total
- λ₁, λ₂ — sheath and armour loss factors
- θ_c — conductor temperature at rated current
- R_dc, R_ac — DC and AC conductor resistance
- U_v — induced standing voltage magnitude (when geometry allows)
- Cable-construction summary lifted from the library entry
- Full installation context (bonding, spacing, depth, ambient, soil ρ_T,
  duct, grouping, frequency, target θ_max)
- Provenance: method id, version, inputs hash, timestamp
