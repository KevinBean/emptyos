"""CIGRE TB 880 case loader.

Each case is a JSON file in `cases/` next to this module:

    {
      "id": "case-1",
      "title": "Single-core 132 kV XLPE buried, trefoil, single-point bonded",
      "source": "CIGRE TB 880, Annex A.1",
      "input": { ... AmpacityInput-shaped ... },
      "expected": {
        "ampacity_a": 829.0,
        "tolerance_a": 0.5,
        "T1": 0.456,
        "T4": 1.234
      },
      "notes": "..."
    }

Cases will be ported from the JS tool's CIGRE-TB-880 fixtures during
Phase A. Until ported, `load_cases()` returns whatever is present in
the cases/ folder; the pytest harness skips missing cases gracefully.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).parent / "cases"


@dataclass
class Case:
    id: str
    title: str
    source: str
    input: dict[str, Any]
    expected: dict[str, Any]
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Case":
        return cls(
            id=d["id"],
            title=d.get("title", d["id"]),
            source=d.get("source", "CIGRE TB 880"),
            input=d["input"],
            expected=d["expected"],
            notes=d.get("notes", ""),
        )


def load_cases() -> list[Case]:
    """Load every *.json fixture in cases/ as a Case."""
    if not CASES_DIR.exists():
        return []
    out: list[Case] = []
    for p in sorted(CASES_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(Case.from_dict(d))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[cigre-tb880] skipping malformed case {p.name}: {e}")
    return out
