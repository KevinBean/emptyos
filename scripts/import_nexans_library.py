"""Import Nexans HV cable catalogue into the EmptyOS cable library.

One-shot CLI: parses a `nexans-cable-data.js` file (from the upstream
Cable-reticulation-tool source), maps each entry to the
`CableLibraryEntry` schema, and writes
`{vault}/30_Resources/cables/library.json` (or any path supplied via
--out). The output file is what `apps/cables/read_library()` reads.

Usage (from emptyos repo root):
    python scripts/import_nexans_library.py --src path/to/nexans-cable-data.js

The source path can also be set in `emptyos.toml`:
    [apps.cables]
    nexans_source = "path/to/nexans-cable-data.js"

Vault output path comes from `emptyos.toml` `[notes] path = ...`.
Override with `--out path/to/library.json` if you want it elsewhere
(e.g. for a demo bundle).

Source data: Nexans Olex OLC12641 (2023). Nested catalogue rows
(currentRatings, faultCurrent) are preserved under `metadata`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

# Make the in-tree pydantic models importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.models import CableLibraryEntry  # noqa: E402


# Top-level array name → (rated_voltage_v, label, material, n_conductors)
ARRAYS = {
    "NEXANS_11KV_SINGLE_CORE_COPPER":   (11000, "6.35/11kV",  "Cu", 1),
    "NEXANS_11KV_SINGLE_CORE_ALUMINUM": (11000, "6.35/11kV",  "Al", 1),
    "NEXANS_11KV_THREE_CORE_COPPER":    (11000, "6.35/11kV",  "Cu", 3),
    "NEXANS_11KV_THREE_CORE_ALUMINUM":  (11000, "6.35/11kV",  "Al", 3),
    "NEXANS_22KV_SINGLE_CORE_COPPER":   (22000, "12.7/22kV",  "Cu", 1),
    "NEXANS_22KV_SINGLE_CORE_ALUMINUM": (22000, "12.7/22kV",  "Al", 1),
    "NEXANS_33KV_SINGLE_CORE_COPPER":   (33000, "19/33kV",    "Cu", 1),
    "NEXANS_33KV_SINGLE_CORE_ALUMINUM": (33000, "19/33kV",    "Al", 1),
}


def _find_array_block(src: str, name: str) -> str | None:
    """Extract the [...] body of `const <name> = [ ... ];`."""
    m = re.search(rf"const\s+{re.escape(name)}\s*=\s*\[", src)
    if not m:
        return None
    i = m.end()
    depth = 1
    start = i
    while i < len(src) and depth > 0:
        ch = src[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return src[start : i - 1]


def _split_top_level_objects(block: str) -> list[str]:
    """Split a JS object-array body into individual object strings.

    Handles nested braces. String literals (which could contain stray
    braces) are skipped over.
    """
    out: list[str] = []
    depth = 0
    in_str: str | None = None
    start = -1
    i = 0
    while i < len(block):
        ch = block[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(block[start : i + 1])
        i += 1
    return out


_NUM = r"[-+]?\d+(?:\.\d+)?"


def _extract_scalar(entry: str, key: str) -> str | None:
    """Find the first `<key>:` at any depth — assumes the key is unique
    within the entry (true for crossSection / resistance / reactance /
    currentCapacity / productCode in Nexans catalogue rows)."""
    m = re.search(
        rf"\b{re.escape(key)}\s*:\s*('[^']*'|\"[^\"]*\"|{_NUM})", entry
    )
    if not m:
        return None
    return m.group(1).strip("'\"")


def _extract_object_literal(entry: str, key: str) -> str | None:
    """Find a nested object value `<key>: { ... }` and return its body
    as raw JS. Used for currentRatings / faultCurrent."""
    m = re.search(rf"\b{re.escape(key)}\s*:\s*\{{", entry)
    if not m:
        return None
    i = m.end() - 1  # at the opening brace
    depth = 0
    in_str: str | None = None
    while i < len(entry):
        ch = entry[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return entry[m.end() - 1 : i + 1]
        i += 1
    return None


def _js_obj_to_python(s: str):
    """Best-effort JS-object-literal → Python value. Quotes unquoted keys
    and converts single-quoted strings to double-quoted, then JSON-loads."""
    # Unquoted keys: word characters followed by colon.
    s = re.sub(r"([{,]\s*)([A-Za-z_][\w]*)\s*:", r'\1"\2":', s)
    # Single-quoted strings → double-quoted (no escaping needed for the
    # Nexans data — no embedded apostrophes).
    s = re.sub(r"'([^']*)'", r'"\1"', s)
    # Trailing commas (JSON disallows).
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def parse_entry(
    entry: str, voltage_v: int, voltage_label: str, material: str, n_conductors: int
) -> dict | None:
    csa_raw = _extract_scalar(entry, "crossSection")
    resistance_raw = _extract_scalar(entry, "resistance")
    reactance_raw = _extract_scalar(entry, "reactance")
    capacity_raw = _extract_scalar(entry, "currentCapacity")
    product = _extract_scalar(entry, "productCode")
    if csa_raw is None or product is None:
        return None
    csa = float(csa_raw)
    resistance = float(resistance_raw) if resistance_raw is not None else None
    reactance = float(reactance_raw) if reactance_raw is not None else None
    capacity = float(capacity_raw) if capacity_raw is not None else None

    metadata: dict = {
        "product_code": product,
        "x_ohm_per_km": reactance,
        "base_ampacity_a": capacity,
        "voltage_label": voltage_label,
        "source": "Nexans OLC12641 (2023)",
    }
    cr = _extract_object_literal(entry, "currentRatings")
    if cr:
        try:
            metadata["current_ratings"] = _js_obj_to_python(cr)
        except json.JSONDecodeError:
            pass
    fc = _extract_object_literal(entry, "faultCurrent")
    if fc:
        try:
            metadata["fault_current"] = _js_obj_to_python(fc)
        except json.JSONDecodeError:
            pass

    cable_id = (
        f"nexans_{voltage_label.lower().replace('/', '-')}_"
        f"{material.lower()}_{n_conductors}c_{int(csa) if csa.is_integer() else csa}"
    )
    return {
        "id": cable_id,
        "manufacturer": "Nexans",
        "family": f"{voltage_label} XLPE {material} {n_conductors}-core",
        "rated_voltage_kv": voltage_v / 1000.0,
        "n_conductors": n_conductors,
        "conductor_material": material,
        "conductor_csa_mm2": csa,
        "conductor_dc_resistance_20c_ohm_per_km": resistance,
        "insulation_material": "XLPE",
        "insulation_max_temp_c": 90.0,
        "sheath_material": "Cu",
        "metadata": metadata,
    }


def parse_all(src: str) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    notes: list[str] = []
    for array_name, (voltage_v, label, material, cores) in ARRAYS.items():
        block = _find_array_block(src, array_name)
        if block is None:
            notes.append(f"missing array {array_name}")
            continue
        objs = _split_top_level_objects(block)
        if not objs:
            notes.append(f"{array_name}: 0 entries parsed")
            continue
        n_added = 0
        for o in objs:
            row = parse_entry(o, voltage_v, label, material, cores)
            if row is None:
                continue
            try:
                CableLibraryEntry(**row)  # validate
            except Exception as e:
                notes.append(f"{array_name}: validation failed for {row.get('id')}: {e}")
                continue
            entries.append(row)
            n_added += 1
        notes.append(f"{array_name}: {n_added} entries")
    return entries, notes


def _load_toml() -> dict:
    cfg = Path("emptyos.toml")
    if not cfg.exists():
        return {}
    with cfg.open("rb") as f:
        return tomllib.load(f)


def resolve_vault_path() -> Path | None:
    notes = _load_toml().get("notes", {})
    vault = notes.get("path")
    return Path(vault) if vault else None


def resolve_source_path() -> str | None:
    apps_cables = _load_toml().get("apps", {}).get("cables", {})
    return apps_cables.get("nexans_source")


def main() -> int:
    ap = argparse.ArgumentParser(description="Import Nexans cable catalogue")
    ap.add_argument(
        "--src",
        default=None,
        help="Path to nexans-cable-data.js. Falls back to "
             "[apps.cables].nexans_source in emptyos.toml.",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON path. Defaults to {vault}/30_Resources/cables/library.json",
    )
    ap.add_argument("--dry-run", action="store_true", help="Parse + report; don't write")
    args = ap.parse_args()

    src_arg = args.src or resolve_source_path()
    if not src_arg:
        print(
            "ERROR: pass --src or set [apps.cables].nexans_source in emptyos.toml",
            file=sys.stderr,
        )
        return 2
    src_path = Path(src_arg)
    if not src_path.exists():
        print(f"ERROR: source not found: {src_path}", file=sys.stderr)
        return 2
    src = src_path.read_text(encoding="utf-8")

    entries, notes = parse_all(src)
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
        vault = resolve_vault_path()
        if not vault:
            print("ERROR: no vault path in emptyos.toml; pass --out", file=sys.stderr)
            return 2
        out_path = vault / "30_Resources" / "cables" / "library.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_meta": {
            "source": "Nexans OLC12641 High Voltage Cable Catalogue (2023)",
            "imported_by": "scripts/import_nexans_library.py",
            "n_entries": len(entries),
            "notes": notes,
        },
        "entries": entries,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {len(entries)} entries -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
