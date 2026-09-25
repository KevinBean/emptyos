"""Apply the vault-side repairs from the KB/app alignment audit.

This migration is intentionally explicit:
- every modified existing note is backed up before writing
- new case notes are created only when the target path does not exist
- frontmatter fields are changed without reserializing unrelated YAML

Run from the EmptyOS repository root after reviewing the mappings below.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


def _vault_root() -> Path:
    """Resolve the mounted vault path from machine config (never hardcode it)."""
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "emptyos.toml"
    if not config_path.exists():
        config_path = repo_root / "emptyos.example.toml"
    with config_path.open("rb") as fh:
        config = tomllib.load(fh)
    path = config.get("notes", {}).get("path", "")
    if not path:
        raise SystemExit("notes.path is not configured in emptyos.toml")
    return Path(path)


VAULT = _vault_root()
STAMP = datetime.now().strftime("%Y-%m-%d-%H%M")
BACKUP_ROOT = VAULT / "99_Attachments" / "temp-backup" / "kb-app-alignment" / STAMP


def _split_note(text: str) -> tuple[list[str], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("note has no frontmatter")
    marker = normalized.find("\n---\n", 4)
    if marker < 0:
        raise ValueError("note has unterminated frontmatter")
    frontmatter = normalized[4:marker].splitlines()
    body = normalized[marker + 5 :]
    return frontmatter, body


def _join_note(frontmatter: list[str], body: str) -> str:
    return "---\n" + "\n".join(frontmatter).rstrip() + "\n---\n\n" + body.lstrip("\n")


def _field_span(lines: list[str], field: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^{re.escape(field)}\s*:")
    for index, line in enumerate(lines):
        if not pattern.match(line):
            continue
        end = index + 1
        while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip()):
            end += 1
        return index, end
    return None


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _field_lines(field: str, value: Any) -> list[str]:
    if isinstance(value, list):
        rows = [f"{field}:"]
        rows.extend(f"  - {_yaml_scalar(item)}" for item in value)
        return rows
    return [f"{field}: {_yaml_scalar(value)}"]


def _set_field(lines: list[str], field: str, value: Any) -> list[str]:
    replacement = _field_lines(field, value)
    span = _field_span(lines, field)
    if span:
        start, end = span
        return lines[:start] + replacement + lines[end:]
    return lines + replacement


def _remove_field(lines: list[str], field: str) -> list[str]:
    span = _field_span(lines, field)
    if not span:
        return lines
    start, end = span
    return lines[:start] + lines[end:]


def _remove_kb_tag(lines: list[str]) -> list[str]:
    span = _field_span(lines, "tags")
    if not span:
        span = _field_span(lines, "tag")
    if not span:
        return lines
    start, end = span
    block = lines[start:end]
    if len(block) == 1 and "[" in block[0]:
        prefix, raw = block[0].split(":", 1)
        tags = [part.strip() for part in raw.strip().strip("[]").split(",") if part.strip()]
        tags = [tag for tag in tags if tag.strip("'\"") != "kb"]
        replacement = [f"{prefix}: [{', '.join(tags)}]"] if tags else []
        return lines[:start] + replacement + lines[end:]
    replacement = [block[0]]
    replacement.extend(
        line for line in block[1:]
        if line.strip().removeprefix("-").strip().strip("'\"") != "kb"
    )
    if len(replacement) == 1:
        replacement = []
    return lines[:start] + replacement + lines[end:]


def _backup(path: Path) -> None:
    rel = path.relative_to(VAULT)
    destination = BACKUP_ROOT / rel.parent / f"{path.stem}-{STAMP}{path.suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(path.read_bytes())


def update_note(
    rel_path: str,
    *,
    set_fields: dict[str, Any] | None = None,
    remove_fields: list[str] | None = None,
    remove_kb_tag: bool = False,
    replacements: list[tuple[str, str]] | None = None,
) -> None:
    path = VAULT / rel_path
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_note(text)
    for field in remove_fields or []:
        frontmatter = _remove_field(frontmatter, field)
    if remove_kb_tag:
        frontmatter = _remove_kb_tag(frontmatter)
    for field, value in (set_fields or {}).items():
        frontmatter = _set_field(frontmatter, field, value)
    for old, new in replacements or []:
        if old not in body:
            raise ValueError(f"{rel_path}: replacement source not found: {old[:80]!r}")
        body = body.replace(old, new)
    new_text = _join_note(frontmatter, body)
    if new_text == text.replace("\r\n", "\n"):
        return
    _backup(path)
    path.write_text(new_text, encoding="utf-8")


def create_note(rel_path: str, content: str) -> None:
    path = VAULT / rel_path
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing note: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def rename_note(old_rel: str, new_rel: str) -> None:
    old_path = VAULT / old_rel
    new_path = VAULT / new_rel
    if new_path.exists():
        raise FileExistsError(f"refusing to overwrite existing note: {new_path}")
    _backup(old_path)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)


def main() -> None:
    # Canonical kind repairs.
    kind_updates = {
        "30_Resources/cdegs-extracted/AutogridPro-digest.md": "lesson",
        "30_Resources/cdegs-extracted/AutoGroundDesign-digest.md": "lesson",
        "30_Resources/cdegs-extracted/_index.md": "moc",
        "30_Resources/KB/emptyos-architecture/calculator-framework.md": "concept",
        "30_Resources/KB/power-systems/standards/as-nzs-1429-2-insulation-thickness-table.md": "reference",
        "30_Resources/KB/power-systems/standards/_fulltext/iec-60229-2007-fulltext.md": "reference",
        "30_Resources/EmptyOS/kb/notes/signal-to-noise-theory-of-social-perception.md": "concept",
        "30_Resources/EmptyOS/kb/notes/snr-social-perception.md": "concept",
        "30_Resources/EmptyOS/kb/notes/social-cognition-through-a-buddhist-predictive-lens.md": "concept",
        "30_Resources/EmptyOS/kb/notes/underground-power-cables/conductor.md": "concept",
        "30_Resources/EmptyOS/kb/sources/ieee-493-2007-2-3.md": "clause",
    }
    for path, kind in kind_updates.items():
        update_note(path, set_fields={"kind": kind, "updated": "2026-06-04"})

    # Dev logs are not KB notes.
    for path in [
        "10_Projects/emptyos/log/2026-04-29.md",
        "10_Projects/emptyos/log/2026-05-04.md",
        "10_Projects/emptyos/log/2026-05-15.md",
        "10_Projects/emptyos/log/2026-05-16.md",
        "10_Projects/emptyos/log/2026-05-23.md",
        "10_Projects/emptyos/log/2026-05-25.md",
        "10_Projects/emptyos/log/2026-05-29.md",
        "10_Projects/emptyos/log/2026-05-30.md",
        "10_Projects/emptyos/log/2026-06-03.md",
        "10_Projects/cable-current-rating/log/2026-05-30.md",
    ]:
        update_note(path, remove_kb_tag=True)

    # Resolve the real duplicate slug while preserving both notes.
    rename_note(
        "30_Resources/EmptyOS/kb/sources/neher-mcgrath-revisited-2021.md",
        "30_Resources/EmptyOS/kb/sources/neher-mcgrath-duct-bank-limitations.md",
    )
    update_note(
        "30_Resources/EmptyOS/kb/sources/neher-mcgrath-duct-bank-limitations.md",
        set_fields={"updated": "2026-06-04"},
    )
    update_note(
        "30_Resources/EmptyOS/kb/notes/neher-mcgrath-revisited-2021.md",
        set_fields={
            "related": [
                "neher-mcgrath-foundation",
                "neher-mcgrath-duct-bank-limitations",
                "iec-60287-method-1-iteration",
                "iec-60287-2-1-2015-4-2",
                "iec-60287-2-1-2015-4-3",
                "cigre-tb-963-fem-cable-rating",
                "cigre-tb-640-input-uncertainty",
            ],
            "updated": "2026-06-04",
        },
    )

    # Stale implementation paths.
    update_note(
        "30_Resources/KB/power-systems/standards/ieee-998-egm.md",
        set_fields={
            "implemented_in": [
                "method: lightning.shielding.ieee998_egm",
                "apps/extension/engineering/lightning/ieee998.py",
            ],
            "updated": "2026-06-04",
        },
    )
    update_note(
        "30_Resources/KB/power-systems/standards/rolling-sphere-method.md",
        set_fields={
            "implemented_in": [
                "method: lightning.shielding.rolling_sphere",
                "apps/extension/engineering/lightning/rolling_sphere.py",
            ],
            "updated": "2026-06-04",
        },
    )
    update_note(
        "30_Resources/KB/cable-thermal/formulas/hdd-bore-geometry.md",
        set_fields={
            "implemented_in": ["apps/personal/cable-hdd/hdd.py::calculate_hdd"],
            "updated": "2026-06-04",
        },
    )
    update_note(
        "30_Resources/EmptyOS/kb/notes/tb-880.md",
        set_fields={
            "implemented_in": [
                "engines/thermal",
                "apps/extension/engineering/cable_network",
            ],
            "updated": "2026-06-04",
        },
    )

    # Missing implementation links.
    implementation_updates = {
        "30_Resources/KB/power-systems/formulas/grid-resistance-hand-formulas.md": [
            "engines/earthing/ieee80.py::sverak_grid_resistance",
            "engines/earthing/schwarz.py::schwarz_grid_resistance",
            "apps/extension/engineering/earthing/grid_resistance.py",
        ],
        "30_Resources/KB/power-systems/formulas/iec-61914-cleat-force.md": [
            "apps/personal/cable/pages/index.html",
            "apps/personal/cable-thrust/thrust.py::sc_force",
        ],
        "30_Resources/KB/power-systems/formulas/peak-factor-kappa.md": [
            "apps/personal/cable/pages/index.html",
        ],
        "30_Resources/KB/cable-thermal/formulas/cable-flexural-rigidity-EI.md": [
            "apps/personal/cable-thrust/thrust.py::I_solid_disc",
            "apps/personal/cable-thrust/thrust.py::I_annulus",
            "apps/personal/cable-thrust/thrust.py::sensitivity_table",
        ],
        "30_Resources/KB/cable-thermal/formulas/thermomechanical-thrust-rigid-cable.md": [
            "apps/personal/cable-thrust/thrust.py::thrust_components",
        ],
        "30_Resources/EmptyOS/kb/notes/emf-efield-charge-models.md": [
            "engines/em/biot_savart/efield_2d.py",
            "apps/extension/engineering/interference",
        ],
        "30_Resources/EmptyOS/kb/notes/lambda2-armour-loss-factor.md": [
            "engines/thermal/em_engine/armour_losses.py",
            "apps/extension/engineering/cable_network",
        ],
        "30_Resources/EmptyOS/kb/notes/sheath-standing-voltage.md": [
            "engines/thermal/iec60287/sheath_voltage.py::standing_voltage",
            "apps/extension/engineering/cable_network",
        ],
    }
    for path, refs in implementation_updates.items():
        update_note(path, set_fields={"implemented_in": refs, "updated": "2026-06-04"})

    # Create real case anchors for formulas that previously pointed to prose or tests.
    cases = {
        "30_Resources/KB/power-systems/cases/ieee-80-annex-b-grid-resistance.md": """
---
kind: case
domain: power-systems
topic: substation-grounding
tags:
  - kb
  - power-systems
  - case
  - earthing
title: "IEEE 80 Annex B — 70 m grid resistance"
references:
  - "IEEE Std 80-2013 Annex B"
verified_by:
  - "engines/earthing/tests/test_ieee80.py::TestSverakRg.test_canonical_70x70_grid"
related:
  - grid-resistance-hand-formulas
created: 2026-06-04
updated: 2026-06-04
---

# IEEE 80 Annex B — 70 m grid resistance

For a 70 m × 70 m grid with total buried conductor length 2940 m, burial depth
0.5 m, and uniform soil resistivity 400 Ω·m, the Sverak equation gives:

```text
R_g = 2.6520 Ω
```

The regression gate accepts the IEEE 80 worked-example band of 2.5–3.0 Ω.
""",
        "30_Resources/KB/power-systems/cases/ieee-80-decrement-factor-table-10.md": """
---
kind: case
domain: power-systems
topic: fault-current-asymmetry
tags:
  - kb
  - power-systems
  - case
  - earthing
title: "IEEE 80 Table 10 — decrement factor"
references:
  - "IEEE Std 80-2013 Table 10 and Equation 84"
verified_by:
  - "engines/earthing/tests/test_decrement.py::TestDecrementFactor.test_eq_84_60hz_value_matches_table_10"
related:
  - decrement-factor-and-design-current
created: 2026-06-04
updated: 2026-06-04
---

# IEEE 80 Table 10 — decrement factor

For `X/R = 20`, fault duration `t_f = 0.10 s`, and `60 Hz`, Equation 84 gives:

```text
T_a = 0.05305 s
D_f = 1.23219
```

This matches the Table 10 value of approximately 1.232.
""",
        "30_Resources/KB/power-systems/cases/iec-60949-cu-35mm2-adiabatic-case.md": """
---
kind: case
domain: power-systems
topic: cable-screen-short-circuit
tags:
  - kb
  - power-systems
  - case
  - cable-screen
title: "IEC 60949 — 35 mm² copper screen adiabatic case"
references:
  - "IEC 60949 adiabatic short-circuit current equation"
verified_by:
  - "engines/cables/tests/test_short_circuit_withstand.py::TestAdiabatic.test_kb_worked_example_cu_35mm2"
related:
  - iec-60949-screen-adiabatic-sizing
created: 2026-06-04
updated: 2026-06-04
---

# IEC 60949 — 35 mm² copper screen adiabatic case

For a 35 mm² copper screen, one-second fault, initial temperature 80 °C, and
final permissible temperature 250 °C:

```text
I_AD = 5.1998 kA
```

The engine regression requires 5.20 kA within 0.01 kA.
""",
        "30_Resources/KB/power-systems/cases/iec-61914-cleat-force-example.md": """
---
kind: case
domain: power-systems
topic: cable-cleat-short-circuit
tags:
  - kb
  - power-systems
  - case
  - cleating
title: "IEC 61914 — cleat force and IEC 60909 peak-current example"
references:
  - "IEC 61914 cable-cleat force calculation"
  - "IEC 60909-0 peak short-circuit current equation"
verified_by:
  - "tests/personal/test_cable.py::TestCleatForceCalculator.test_iec_61914_coefficient_uses_spacing_in_mm"
related:
  - iec-61914-cleat-force
  - peak-factor-kappa
created: 2026-06-04
updated: 2026-06-04
---

# IEC 61914 — cleat force and IEC 60909 peak-current example

For `I_k = 29 kA`, `X/R = 10`, three-phase trefoil coefficient `c = 0.17`, and
centre spacing `S = 300 mm`:

```text
κ = 1.02 + 0.98·exp(-3/10) = 1.74600
i_p = κ·√2·I_k = 71.607 kA
F_t = 0.17·i_p²/S = 2.90565 kN/m
2.5·F_t = 7.26412 kN/m
```

The `0.17` coefficient produces kN/m when spacing is supplied in millimetres.
Equivalently, the same numerical expression produces N/m when spacing is in
metres.
""",
        "30_Resources/KB/power-systems/cases/resap-rs-tut1-f09-wenner-inversion.md": """
---
kind: case
domain: power-systems
topic: soil-modelling
tags:
  - kb
  - power-systems
  - case
  - wenner
title: "RESAP RS_TUT1.F09 — Wenner inversion"
references:
  - "RESAP RS_TUT1.F09 tutorial reference"
verified_by:
  - "engines/soil/tests/test_inverse_reference.py"
related:
  - wenner-soundings-inversion
created: 2026-06-04
updated: 2026-06-04
---

# RESAP RS_TUT1.F09 — Wenner inversion

The reference Wenner measurements are 190, 183, 147, 118, and 107 Ω·m at
spacings 2, 4, 8, 16, and 32 m. The reported two-layer solution is:

```text
ρ₁ = 190.0 Ω·m
ρ₂ = 105.5163 Ω·m
h₁ = 4.733190 m
RMS error = 1.8882 %
```

The EmptyOS inversion must converge in the same basin and match or improve the
reported RMS error.
""",
        "30_Resources/KB/power-systems/cases/sim-textbook-circuit-regressions.md": """
---
kind: case
domain: power-systems
topic: simulation
tags:
  - kb
  - power-systems
  - case
  - emtp
title: "EMTP textbook circuit regression cases"
references:
  - "Closed-form RC, RL, and mutual-inductance circuit solutions"
verified_by:
  - "engines/sim/tests/test_textbook.py"
related:
  - dommel-companion-model
  - nodal-analysis-time-stepping
created: 2026-06-04
updated: 2026-06-04
---

# EMTP textbook circuit regression cases

This case family is the simulator correctness floor: resistive divider, RC
frequency response, RL magnitude and phase, mutual inductance, and switching
transients are checked against closed-form solutions in
`engines/sim/tests/test_textbook.py`.
""",
        "30_Resources/KB/power-systems/cases/iec-60909-radial-11kv-case.md": """
---
kind: case
domain: power-systems
topic: short-circuit
tags:
  - kb
  - power-systems
  - case
  - short-circuit
title: "IEC 60909 — 11 kV radial two-bus case"
references:
  - "IEC 60909 simplified Thevenin equivalent-voltage method"
verified_by:
  - "engines/reticulation/tests/test_shortcircuit.py::test_radial_two_bus_infinite_source"
related:
  - iec-60909-short-circuit-radial
created: 2026-06-04
updated: 2026-06-04
---

# IEC 60909 — 11 kV radial two-bus case

An 11 kV infinite-bus source feeds one radial segment with `R = 1.0 Ω`,
`X = 0.5 Ω`, and maximum voltage factor `c = 1.10`.

```text
|Z_thev| = 1.11803 Ω
I''k = 6.2484 kA
S''k = 119.048 MVA
```

The regression test checks the path impedance, fault current, and fault MVA.
""",
        "30_Resources/KB/cable-thermal/cases/tb-889-cable-thrust-worked-example.md": """
---
kind: case
domain: cable-thermal
topic: thrust-cleating
tags:
  - kb
  - cable-thermal
  - case
  - thrust
title: "TB 889 — cable thrust and EI sensitivity worked example"
references:
  - "CIGRE TB 889 §4.6.1.1"
  - "CIGRE TB 669 ch. 9"
verified_by:
  - "tests/personal/test_cable_thrust.py"
related:
  - thermomechanical-thrust-rigid-cable
  - cable-flexural-rigidity-EI
created: 2026-06-04
updated: 2026-06-04
---

# TB 889 — cable thrust and EI sensitivity worked example

For the default 2500 mm² copper Milliken conductor and aluminium sheath inputs:

```text
F_a1 = 1687.5 kgf
F_a2 = 506.3 kgf
F_a3 = 707.9 kgf
F_a4 = 267.3 kgf
F_a = 3168.9 kgf
```

At a 1200 mm cleat span and safety factor 2.0, the lower EI bound fails while
the upper EI bound passes. The required verdict is therefore `EI-BOUND`.
""",
        "30_Resources/KB/cable-thermal/cases/cable-pulling-capstan-worked-example.md": """
---
kind: case
domain: cable-thermal
topic: cable-installation
tags:
  - kb
  - cable-thermal
  - case
  - pulling
title: "Cable pulling — straight and capstan bend worked example"
references:
  - "CIGRE TB 889 cable pulling"
  - "IEEE 1185-2019 capstan equation"
verified_by:
  - "apps/extension/engineering/cable-pulling/pulling.py::calculate_pull"
related:
  - cable-pulling-tension
created: 2026-06-04
updated: 2026-06-04
---

# Cable pulling — straight and capstan bend worked example

A 10.2 kg/m cable is pulled through a 100 m straight section and a 90° bend of
4 m radius with friction coefficient 0.2:

```text
straight exit tension = 2001.2 N
bend exit tension = 2739.9 N
sidewall pressure = 685.0 N/m
```

The example passes a 4 m minimum bend radius and the default tension and
sidewall-pressure limits.
""",
        "30_Resources/KB/cable-thermal/cases/hdd-road-crossing-worked-example.md": """
---
kind: case
domain: cable-thermal
topic: hdd-installation
tags:
  - kb
  - cable-thermal
  - case
  - hdd
title: "HDD road crossing — footprint worked example"
references:
  - "CIGRE TB 889 HDD design checks"
verified_by:
  - "apps/personal/cable-hdd/hdd.py::calculate_hdd"
related:
  - hdd-bore-geometry
created: 2026-06-04
updated: 2026-06-04
---

# HDD road crossing — footprint worked example

For a 20 m road, 4 m crossing depth, 1 m trench depth, and 12° entry angle:

```text
setback = 14.1 m
HDD length = 48.2 m
pit depth = 1.39 m
footprint = 17.5 m × 90.9 m
```

The default 203 mm conduit ID also exceeds the 187.5 mm minimum for the
125 mm cable OD.
""",
        "30_Resources/KB/cable-thermal/cases/coaxial-cable-electric-stress-worked-example.md": """
---
kind: case
domain: cable-thermal
topic: cable-insulation
tags:
  - kb
  - cable-thermal
  - case
  - electric-stress
title: "Coaxial cable electric stress worked example"
references:
  - "Closed-form coaxial dielectric field equation"
verified_by:
  - "engines/cables/tests/test_electrical_stress.py::TestNominal.test_known_case"
related:
  - coaxial-cable-electric-stress
created: 2026-06-04
updated: 2026-06-04
---

# Coaxial cable electric stress worked example

For `U₀ = 12 kV`, conductor radius `r_c = 5 mm`, and insulation outer radius
`R = 15 mm`:

```text
E_max = 2.184574 kV/mm
E_min = 0.728191 kV/mm
```

At a 75 kV impulse, the corresponding values are 13.653588 and
4.551196 kV/mm.
""",
        "30_Resources/KB/cable-thermal/cases/sheath-standing-voltage-worked-example.md": """
---
kind: case
domain: cable-thermal
topic: sheath-bonding
tags:
  - kb
  - cable-thermal
  - case
  - sheath-bonding
title: "Sheath standing voltage — 132 kV trefoil worked example"
references:
  - "IEC 60287-1-1 Annex C"
verified_by:
  - "engines/thermal/tests/test_sheath_voltage.py"
related:
  - sheath-standing-voltage
created: 2026-06-04
updated: 2026-06-04
---

# Sheath standing voltage — 132 kV trefoil worked example

For an 800 A trefoil cable with 75.5 mm centre spacing, 33.5 mm sheath mean
radius, and 500 m single-point-bonded section:

```text
M = 1.62517e-7 H/m
E = 0.0408451 V/m
U_v = 20.4225 V
```

The regression suite also checks linear length scaling and cross-bonded
minor-section behavior.
""",
        "30_Resources/EmptyOS/kb/notes/tb-880-case-2.md": """
---
tags:
  - kb
kind: case
domain: cable-thermal
topic: cable-rating
title: "TB 880 Case 2 — 3-core submarine cable"
case_id: "2"
rating_A: 838.34
related:
  - tb-880
  - lambda2-armour-loss-factor
references:
  - "CIGRE TB 880:2022 Case 2"
verified_by:
  - "engines/thermal/em_engine/tests/test_tb880_case2.py"
created: 2026-06-04
updated: 2026-06-04
---

# TB 880 Case 2 — 3-core submarine cable

The armoured 18/30 kV, 3 × 630 mm² submarine-cable benchmark:

```text
λ₂ = 0.4207
I = 838.34 A
```

This is the primary published validation anchor for the three-core steel-wire
armour-loss implementation.
""",
        "30_Resources/EmptyOS/kb/notes/tb-880-case-8.md": """
---
tags:
  - kb
kind: case
domain: cable-thermal
topic: cable-rating
title: "TB 880 Case 8 — 220 kV 3-core submarine cable"
case_id: "8"
rating_A: 1134.82
related:
  - tb-880
  - lambda2-armour-loss-factor
references:
  - "CIGRE TB 880:2022 Case 8"
verified_by:
  - "engines/thermal/em_engine/tests/test_tb880_case8.py"
created: 2026-06-04
updated: 2026-06-04
---

# TB 880 Case 8 — 220 kV 3-core submarine cable

The 220 kV, 3 × 1000 mm² submarine-cable benchmark:

```text
λ₂ = 0.0
I = 1134.82 A
```

It checks the bonding regime where armour circulating loss is absent while the
rest of the armoured-cable model remains active.
""",
    }
    for path, content in cases.items():
        create_note(path, content)

    # Verification anchors for every formula flagged by the audit.
    verification_updates = {
        "30_Resources/KB/power-systems/formulas/biot-savart-magnetic-induction.md": ["emf-400kv-validation-case"],
        "30_Resources/KB/power-systems/formulas/bundle-equivalent-reduction.md": ["rt07-230kv-east-central"],
        "30_Resources/KB/power-systems/formulas/chain-impedance-recursion.md": ["rt07-230kv-east-central"],
        "30_Resources/KB/power-systems/formulas/decrement-factor-and-design-current.md": ["ieee-80-decrement-factor-table-10"],
        "30_Resources/KB/power-systems/formulas/gmr-with-permeability.md": ["rt07-230kv-east-central"],
        "30_Resources/KB/power-systems/formulas/grid-resistance-hand-formulas.md": ["ieee-80-annex-b-grid-resistance"],
        "30_Resources/KB/power-systems/formulas/iec-60949-screen-adiabatic-sizing.md": ["iec-60949-cu-35mm2-adiabatic-case"],
        "30_Resources/KB/power-systems/formulas/iec-61914-cleat-force.md": ["iec-61914-cleat-force-example"],
        "30_Resources/KB/power-systems/formulas/peak-factor-kappa.md": ["iec-61914-cleat-force-example"],
        "30_Resources/KB/power-systems/formulas/wenner-soundings-inversion.md": ["resap-rs-tut1-f09-wenner-inversion"],
        "30_Resources/KB/power-systems/formulas/dommel-companion-model.md": ["sim-textbook-circuit-regressions"],
        "30_Resources/KB/power-systems/formulas/nodal-analysis-time-stepping.md": ["sim-textbook-circuit-regressions"],
        "30_Resources/KB/cable-thermal/formulas/2d-fem-cable-thermal.md": ["tb-880-case-0"],
        "30_Resources/KB/cable-thermal/formulas/cable-flexural-rigidity-EI.md": ["tb-889-cable-thrust-worked-example"],
        "30_Resources/KB/cable-thermal/formulas/cable-pulling-tension.md": ["cable-pulling-capstan-worked-example"],
        "30_Resources/KB/cable-thermal/formulas/dielectric-loss-Wd.md": ["tb-880-case-0"],
        "30_Resources/KB/cable-thermal/formulas/hdd-bore-geometry.md": ["hdd-road-crossing-worked-example"],
        "30_Resources/KB/cable-thermal/formulas/lambda1-sheath-loss-factor.md": ["tb-880-case-0-1"],
        "30_Resources/KB/cable-thermal/formulas/t4-duct-decomposition.md": ["tb-880-case-0-2"],
        "30_Resources/KB/cable-thermal/formulas/t4-soil-direct-buried.md": ["tb-880-case-0"],
        "30_Resources/KB/cable-thermal/formulas/thermomechanical-thrust-rigid-cable.md": ["tb-889-cable-thrust-worked-example"],
        "30_Resources/EmptyOS/kb/notes/coaxial-cable-electric-stress.md": ["coaxial-cable-electric-stress-worked-example"],
        "30_Resources/EmptyOS/kb/notes/emf-efield-charge-models.md": ["emf-400kv-validation-case"],
        "30_Resources/EmptyOS/kb/notes/iec-60909-short-circuit-radial.md": ["iec-60909-radial-11kv-case"],
        "30_Resources/EmptyOS/kb/notes/lambda2-armour-loss-factor.md": ["tb-880-case-2", "tb-880-case-8"],
        "30_Resources/EmptyOS/kb/notes/sheath-standing-voltage.md": ["sheath-standing-voltage-worked-example"],
        "30_Resources/EmptyOS/kb/notes/iec-60287-method-1-iteration.md": ["tb-880-case-0"],
        "30_Resources/EmptyOS/kb/notes/r-ac-conductor-resistance-algorithm.md": ["tb-880-case-0"],
    }
    for path, refs in verification_updates.items():
        update_note(path, set_fields={"verified_against": refs, "updated": "2026-06-04"})

    # A case is evidence itself; it should not claim to be verified against a standard slug.
    update_note(
        "30_Resources/EmptyOS/kb/notes/case-temp-rise-abb-example-1.md",
        remove_fields=["verified_against"],
        set_fields={"updated": "2026-06-04"},
    )

    # Correct semantic mismatches in the highest-risk notes.
    update_note(
        "30_Resources/KB/power-systems/formulas/iec-61914-cleat-force.md",
        replacements=[
            ("| `S`    | Axial centre-to-centre spacing between conductors      | metres     |",
             "| `S`    | Axial centre-to-centre spacing between conductors      | mm         |"),
            ("F_t = 0.17 × 82² / 0.3\n    = 0.17 × 6724 / 0.3\n    = 3811 kN/m",
             "F_t = 0.17 × 82² / 300\n    = 0.17 × 6724 / 300\n    = 3.811 kN/m"),
            ("Required cleat rating: `2.5 × 3811 = 9528 kN/m` over 1 s.",
             "Required cleat rating: `2.5 × 3.811 = 9.528 kN/m` over 1 s."),
            ("This is far above off-the-shelf cleat ratings — which means in practice either (a) the conductor spacing is wider, (b) the conductor centres are not on the spacing used here (e.g. the formation factor differs), or (c) the 0.17 coefficient applies to a different conductor arrangement than the one in your design. Sanity-check the inputs and the published coefficient before sizing.",
             "This result is in the expected range for cable-cleat mechanical duty. Confirm the conductor arrangement and use the tested cleat classification rather than treating the analytical result as a product rating."),
            ("If a worked example gives < 500 kN/m at fault levels of tens of kA peak with sub-metre spacing, double-check the spacing dependence.",
             "If a worked example gives an implausibly large hundreds-or-thousands of kN/m result, check whether metres were supplied to a coefficient that expects millimetres."),
        ],
    )
    update_note(
        "30_Resources/KB/power-systems/formulas/peak-factor-kappa.md",
        replacements=[
            ("`κ` ranges from 1.0 (pure resistive system, no DC offset) up to ~2.0+ (highly inductive system, maximum DC offset surviving into the first peak).",
             "`κ` ranges from approximately 1.02 for a highly resistive system up to 2.0 as X/R approaches infinity."),
            ("| 0     | 1.0 (pure R) |\n| 1     | 1.5        |\n| 3     | 1.8        |\n| 5     | 1.9        |\n| 10    | 2.0        |\n| 20+   | 2.1–2.5    |",
             "| 0     | 1.02 (formula limit) |\n| 1     | 1.069      |\n| 3     | 1.381      |\n| 5     | 1.558      |\n| 10    | 1.746      |\n| 20    | 1.863      |\n| ∞     | 2.000      |"),
            ("| 132 kV overhead line       | 15–25       | 2.0–2.2     |\n| 132 kV cable (medium run)  | 5–12        | 1.8–2.0     |\n| 132 kV cable (short run)   | 3–8         | 1.7–1.9     |",
             "| 132 kV overhead line       | 15–25       | 1.82–1.89   |\n| 132 kV cable (medium run)  | 5–12        | 1.56–1.78   |\n| 132 kV cable (short run)   | 3–8         | 1.38–1.69   |"),
            ("`I_k = 29 kA RMS`, X/R = 10 → κ = 2.0:\n\n```\ni_p = 2.0 × √2 × 29 = 82 kA peak\n```\n\nThat 82 kA peak then feeds [[iec-61914-cleat-force]] to size cable cleats.",
             "`I_k = 29 kA RMS`, X/R = 10 → κ = 1.746:\n\n```\ni_p = 1.746 × √2 × 29 = 71.6 kA peak\n```\n\nThat 71.6 kA peak then feeds [[iec-61914-cleat-force]] to size cable cleats."),
        ],
    )
    update_note(
        "30_Resources/EmptyOS/kb/notes/lambda2-armour-loss-factor.md",
        replacements=[
            ("TB 880 Chapter 4 uses **unarmoured single-core** — λ₂ = 0 everywhere. There's no TB880 reference case for armoured cables; validation against Anders worked examples or CYMCAP is the next-best approach.",
             "TB 880 includes armoured three-core submarine-cable benchmarks. [[tb-880-case-2]] is the primary λ₂ validation anchor (`λ₂ = 0.4207`), while [[tb-880-case-8]] checks the bonding regime where λ₂ is zero. The implementation is covered by `engines/thermal/em_engine/tests/test_tb880_case2.py` and `test_tb880_case8.py`."),
        ],
    )
    update_note(
        "30_Resources/EmptyOS/kb/notes/coaxial-cable-electric-stress.md",
        replacements=[
            ("which has a minimum at `R/r_c = e ≈ 2.718`, the classic \"optimum\" coaxial\nproportion that minimises peak stress for a given outer radius.",
             "which has a maximum at `R/r_c = e ≈ 2.718`, the classic \"optimum\" coaxial\nproportion that minimises peak stress for a given outer radius."),
        ],
    )

    print(f"Vault KB/app alignment migration complete. Backups: {BACKUP_ROOT}")


if __name__ == "__main__":
    main()
