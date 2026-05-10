"""scripts/import_nexans_library — parser tests against fixture snippets.

Doesn't depend on the actual Nexans JS being on disk — uses inline
fixtures shaped like real catalogue rows. Validates:
  - top-level scalar field extraction (simple + multi-line entries)
  - nested object-literal preservation (currentRatings, faultCurrent)
  - JS-object → Python conversion (unquoted keys, single quotes, trailing commas)
  - schema validation through CableLibraryEntry
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.import_nexans_library import (
    _extract_object_literal,
    _extract_scalar,
    _find_array_block,
    _js_obj_to_python,
    _split_top_level_objects,
    parse_entry,
)


SIMPLE_FIXTURE = """
const NEXANS_11KV_SINGLE_CORE_COPPER = [
    { crossSection: 16, resistance: 1.47, reactance: 0.161, currentCapacity: 116, productCode: 'XJHP15AA001' },
    { crossSection: 25, resistance: 0.927, reactance: 0.152, currentCapacity: 130, productCode: 'XJHP17AA002' }
];
"""

MULTILINE_FIXTURE = """
const NEXANS_22KV_SINGLE_CORE_COPPER = [
    {
        crossSection: 50,
        resistance: 0.387,
        reactance: 0.123,
        currentCapacity: 175,
        currentRatings: {
            inAir: { trefoilDuct: 200 },
            inGround: { flatTouch: 220 }
        },
        faultCurrent: { conductor: 7.1, screen: 3.4 },
        productCode: 'XJHP50AA001'
    }
];
"""


def test_find_array_block_extracts_inner():
    block = _find_array_block(SIMPLE_FIXTURE, "NEXANS_11KV_SINGLE_CORE_COPPER")
    assert block is not None
    assert "crossSection: 16" in block
    assert "XJHP17AA002" in block


def test_find_array_block_returns_none_for_missing():
    assert _find_array_block(SIMPLE_FIXTURE, "NEXANS_99KV_DOES_NOT_EXIST") is None


def test_split_top_level_objects_handles_simple_entries():
    block = _find_array_block(SIMPLE_FIXTURE, "NEXANS_11KV_SINGLE_CORE_COPPER")
    objs = _split_top_level_objects(block)
    assert len(objs) == 2
    assert "XJHP15AA001" in objs[0]
    assert "XJHP17AA002" in objs[1]


def test_split_top_level_objects_handles_nested_braces():
    block = _find_array_block(MULTILINE_FIXTURE, "NEXANS_22KV_SINGLE_CORE_COPPER")
    objs = _split_top_level_objects(block)
    assert len(objs) == 1  # nested currentRatings braces don't fool the splitter
    assert "currentRatings" in objs[0]
    assert "faultCurrent" in objs[0]


def test_extract_scalar_handles_strings_and_numbers():
    s = "{ crossSection: 50, productCode: 'XJHP50AA001' }"
    assert _extract_scalar(s, "crossSection") == "50"
    assert _extract_scalar(s, "productCode") == "XJHP50AA001"
    assert _extract_scalar(s, "absent") is None


def test_extract_object_literal_preserves_inner():
    s = "{ a: 1, currentRatings: { inAir: { x: 1 }, inGround: { y: 2 } }, b: 3 }"
    body = _extract_object_literal(s, "currentRatings")
    assert body is not None
    assert body.startswith("{") and body.endswith("}")
    assert "inAir" in body and "inGround" in body
    assert "b: 3" not in body  # boundary respected


def test_js_obj_to_python_handles_unquoted_keys_and_single_quotes():
    src = "{ inAir: { 'flat': 100, trefoil: 95, } }"
    out = _js_obj_to_python(src)
    assert out == {"inAir": {"flat": 100, "trefoil": 95}}


def test_parse_entry_simple():
    objs = _split_top_level_objects(
        _find_array_block(SIMPLE_FIXTURE, "NEXANS_11KV_SINGLE_CORE_COPPER")
    )
    row = parse_entry(objs[0], 11000, "6.35/11kV", "Cu", 1)
    assert row["id"] == "nexans_6.35-11kv_cu_1c_16"
    assert row["conductor_csa_mm2"] == 16.0
    assert row["conductor_dc_resistance_20c_ohm_per_km"] == 1.47
    assert row["metadata"]["x_ohm_per_km"] == 0.161
    assert row["metadata"]["base_ampacity_a"] == 116.0
    assert row["metadata"]["product_code"] == "XJHP15AA001"
    assert row["rated_voltage_kv"] == 11.0
    assert row["conductor_material"] == "Cu"


def test_parse_entry_multiline_preserves_nested_metadata():
    objs = _split_top_level_objects(
        _find_array_block(MULTILINE_FIXTURE, "NEXANS_22KV_SINGLE_CORE_COPPER")
    )
    row = parse_entry(objs[0], 22000, "12.7/22kV", "Cu", 1)
    md = row["metadata"]
    assert md["current_ratings"]["inAir"]["trefoilDuct"] == 200
    assert md["current_ratings"]["inGround"]["flatTouch"] == 220
    assert md["fault_current"]["conductor"] == 7.1
    assert md["fault_current"]["screen"] == 3.4


def test_parse_entry_validates_against_pydantic_schema():
    """parse_entry's output must validate as CableLibraryEntry."""
    from engines.models import CableLibraryEntry

    objs = _split_top_level_objects(
        _find_array_block(SIMPLE_FIXTURE, "NEXANS_11KV_SINGLE_CORE_COPPER")
    )
    row = parse_entry(objs[0], 11000, "6.35/11kV", "Cu", 1)
    entry = CableLibraryEntry(**row)
    assert entry.id == row["id"]
    assert entry.rated_voltage_kv == 11.0


def test_parse_entry_returns_none_when_missing_required_fields():
    bad = "{ resistance: 1.0 }"  # no crossSection, no productCode
    assert parse_entry(bad, 11000, "6.35/11kV", "Cu", 1) is None
