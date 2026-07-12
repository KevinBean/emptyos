"""Import a bare-overhead-conductor catalogue into the EmptyOS conductor library.

One-shot CLI, sibling of ``import_nexans_library.py``: parses the
``conductor-database-extended.js`` file from the upstream conductor_selection
tool (Prysmian AU BOHC 2022 V3 catalogue — AAC / AAAC / ACSR / ACAR), maps
each entry to the ``engines/overhead_line`` ``Conductor`` shape, and writes
``{vault}/30_Resources/Electrical-Engineering/conductors/library.json``. The overhead-line app loads
that file at setup via ``register_conductors()`` — absent file = bundled
6-conductor catalogue only, byte-identical behaviour.

Usage (from emptyos repo root):
    python scripts/import_prysmian_conductors.py --src path/to/conductor-database-extended.js

The source path can also be set in `emptyos.toml`:
    [apps.overhead-line]
    conductor_source = "path/to/conductor-database-extended.js"

Extended fields the engine shape can't hold (GMR, reactance, AC resistance,
manufacturer ampacity ratings) are preserved under each entry's `metadata`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.overhead_line.conductors import Conductor  # noqa: E402

# Reuse the battle-tested JS-array parsing from the Nexans importer.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_nexans_library import _find_array_block, _split_top_level_objects  # noqa: E402

ARRAY_NAME = "CONDUCTOR_DATABASE_EXTENDED"
_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def _scalar(entry: str, key: str):
    """First `<key>:` value — number, quoted string, or null → None."""
    m = re.search(rf"\b{re.escape(key)}\s*:\s*('[^']*'|\"[^\"]*\"|null|{_NUM})", entry)
    if not m:
        return None
    raw = m.group(1)
    if raw == "null":
        return None
    if raw[0] in "'\"":
        return raw.strip("'\"")
    return float(raw)


def parse_entry(entry: str) -> dict | None:
    name = _scalar(entry, "name")
    kind = _scalar(entry, "type")
    dia = _scalar(entry, "diameter_mm")
    area = _scalar(entry, "area_mm2")
    if not name or not isinstance(dia, float) or not isinstance(area, float):
        return None
    area_al = _scalar(entry, "alum_area_mm2")
    area_steel = _scalar(entry, "steel_area_mm2")
    if isinstance(area_al, float) and isinstance(area_steel, float):
        area_total = area_al + area_steel
    else:
        area_al = area_al if isinstance(area_al, float) else area
        area_total = area
    area_total = max(area_total, area_al)
    alpha = _scalar(entry, "alpha_per_C")
    # The base DB stores alpha ×10⁻⁶ inconsistently; normalise to per-°C.
    if isinstance(alpha, float) and alpha > 1e-3:
        alpha = alpha * 1e-6

    metadata: dict = {"source": "Prysmian AU BOHC 2022 V3"}
    for k_src, k_out in (
        ("gmr_mm", "gmr_mm"),
        ("inductive_reactance_ohm_per_km", "x_ohm_km"),
        ("ac_resistance_75C", "r_ac_75c_ohm_km"),
        ("equiv_area_copper_mm2", "equiv_area_copper_mm2"),
    ):
        v = _scalar(entry, k_src)
        if v is not None:
            metadata[k_out] = v
    amp = {m.group(1): float(m.group(2))
           for m in re.finditer(rf"\b(ampacity_[a-z_]+)\s*:\s*({_NUM})", entry)}
    if amp:
        metadata["ampacity_a"] = amp

    return {
        "code": str(name),
        "kind": str(kind or "ACSR"),
        "stranding": str(_scalar(entry, "stranding") or ""),
        "area_al_mm2": area_al,
        "area_total_mm2": area_total,
        "diameter_mm": dia,
        "r_dc_20c_ohm_km": _scalar(entry, "dc_resistance_20C"),
        "mass_kg_km": _scalar(entry, "weight_kg_per_km"),
        "rts_kn": _scalar(entry, "uts_kN"),
        "e_final_gpa": _scalar(entry, "modulus_GPa") or 70.0,
        "alpha_per_c": alpha or 19.3e-6,
        "metadata": metadata,
    }


def parse_all(src: str) -> tuple[list[dict], list[str]]:
    block = _find_array_block(src, ARRAY_NAME)
    if block is None:
        return [], [f"missing array {ARRAY_NAME}"]
    entries: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()
    for o in _split_top_level_objects(block):
        row = parse_entry(o)
        if row is None:
            continue
        key = row["code"].lower()
        if key in seen:
            notes.append(f"duplicate codeword skipped: {row['code']}")
            continue
        try:
            Conductor(**{k: v for k, v in row.items() if k != "metadata"})
        except (TypeError, ValueError) as e:
            notes.append(f"validation failed for {row['code']}: {e}")
            continue
        if None in (row["r_dc_20c_ohm_km"], row["mass_kg_km"], row["rts_kn"]):
            notes.append(f"incomplete row skipped: {row['code']}")
            continue
        seen.add(key)
        entries.append(row)
    notes.append(f"{ARRAY_NAME}: {len(entries)} entries")
    return entries, notes


def _load_toml() -> dict:
    cfg = Path("emptyos.toml")
    if not cfg.exists():
        return {}
    with cfg.open("rb") as f:
        return tomllib.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Import bare-overhead-conductor catalogue")
    ap.add_argument("--src", default=None,
                    help="Path to conductor-database-extended.js. Falls back to "
                         "[apps.overhead-line].conductor_source in emptyos.toml.")
    ap.add_argument("--out", default=None,
                    help="Output JSON path. Defaults to {vault}/30_Resources/Electrical-Engineering/conductors/library.json")
    ap.add_argument("--dry-run", action="store_true", help="Parse + report; don't write")
    args = ap.parse_args()

    toml = _load_toml()
    src_arg = args.src or toml.get("apps", {}).get("overhead-line", {}).get("conductor_source")
    if not src_arg:
        print("ERROR: pass --src or set [apps.overhead-line].conductor_source in emptyos.toml",
              file=sys.stderr)
        return 2
    src_path = Path(src_arg)
    if not src_path.exists():
        print(f"ERROR: source not found: {src_path}", file=sys.stderr)
        return 2

    entries, notes = parse_all(src_path.read_text(encoding="utf-8"))
    print(f"parsed {len(entries)} entries")
    for n in notes:
        print(f"  {n}")
    if not entries:
        print("ERROR: no entries parsed; aborting", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\n--dry-run; not writing")
        return 0

    if args.out:
        out_path = Path(args.out)
    else:
        vault = toml.get("notes", {}).get("path")
        if not vault:
            print("ERROR: no vault path in emptyos.toml; pass --out", file=sys.stderr)
            return 2
        out_path = Path(vault) / "30_Resources" / "conductors" / "library.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "_meta": {
            "source": "Prysmian AU BOHC 2022 V3 bare overhead conductor catalogue",
            "imported_by": "scripts/import_prysmian_conductors.py",
            "n_entries": len(entries),
            "notes": notes,
        },
        "entries": entries,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {len(entries)} entries -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
