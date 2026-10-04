"""Tests for emptyos.sdk.schema — calculator framework typed I/O.

Covers schema_field metadata, validate() with ranges, to_jsonschema() for
common cases, inputs_hash stability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from emptyos.sdk.schema import (
    field_help,
    field_range,
    field_unit,
    inputs_hash,
    schema_field,
    to_jsonschema,
    validate,
)


@dataclass
class Cond:
    role: str = schema_field(help="phase | sheath | …")
    y_m: float = schema_field(unit="m", help="Horizontal position")
    z_m: float = schema_field(unit="m")
    radius_m: float = schema_field(unit="m", range=(1e-4, 0.2))
    permeability: float = schema_field(default=1.0, range=(1.0, 100.0))


@dataclass
class Net:
    f_hz: float = schema_field(unit="Hz", range=(10.0, 1e6), default=50.0)
    conductors: list = schema_field(default_factory=list)


# ── schema_field metadata round-trip ─────────────────────────────────


def test_schema_field_carries_metadata_through_dataclass():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(Cond)}
    assert field_unit(fields["y_m"]) == "m"
    assert field_help(fields["role"]).startswith("phase")
    assert field_range(fields["radius_m"]) == (1e-4, 0.2)
    assert field_range(fields["y_m"]) is None
    # Default values still work
    assert Cond("phase", 0, 0, 0.01).permeability == 1.0


def test_schema_field_disallows_default_and_default_factory():
    with pytest.raises(ValueError, match="at most one"):
        @dataclass
        class Bad:
            x: list = schema_field(default=[], default_factory=list)


def test_schema_field_default_factory_works():
    @dataclass
    class Item:
        items: list = schema_field(default_factory=list, help="children")
    a, b = Item(), Item()
    a.items.append(1)
    assert b.items == []


# ── validate() with ranges ───────────────────────────────────────────


def test_validate_passes_when_in_range():
    c = Cond(role="phase", y_m=0, z_m=10, radius_m=0.015, permeability=1.0)
    assert validate(c) == []


def test_validate_flags_below_min():
    c = Cond(role="phase", y_m=0, z_m=10, radius_m=1e-6, permeability=1.0)
    errs = validate(c)
    assert any("below minimum" in e and "radius_m" in e for e in errs)


def test_validate_flags_above_max():
    c = Cond(role="phase", y_m=0, z_m=10, radius_m=0.5, permeability=200.0)
    errs = validate(c)
    assert any("radius_m" in e and "above maximum" in e for e in errs)
    assert any("permeability" in e and "above maximum" in e for e in errs)


def test_validate_flags_nan_inf():
    c = Cond(role="phase", y_m=0, z_m=10, radius_m=float("inf"), permeability=1.0)
    errs = validate(c)
    assert any("not finite" in e for e in errs)


def test_validate_recurses_into_nested_lists():
    n = Net(
        f_hz=50.0,
        conductors=[
            Cond("phase", 0, 0, 0.01),
            Cond("sheath", 0, 0, 5.0),  # radius too big
        ],
    )
    errs = validate(n)
    assert any("conductors[1].radius_m" in e for e in errs)


def test_validate_skips_fields_without_range():
    # role is a string with no range — must not be flagged
    c = Cond(role="anything", y_m=0, z_m=0, radius_m=0.01)
    assert validate(c) == []


# ── to_jsonschema() ──────────────────────────────────────────────────


def test_to_jsonschema_emits_units_and_ranges():
    schema = to_jsonschema(Cond)
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["y_m"]["x-eos-unit"] == "m"
    assert props["radius_m"]["minimum"] == 1e-4
    assert props["radius_m"]["maximum"] == 0.2
    assert props["role"]["description"].startswith("phase")
    # role and y_m have no defaults → required
    assert "role" in schema["required"]
    assert "y_m" in schema["required"]
    assert "permeability" not in schema["required"]


def test_to_jsonschema_handles_int_float_bool():
    @dataclass
    class M:
        i: int = schema_field()
        f: float = schema_field()
        b: bool = schema_field(default=False)
        s: str = schema_field(default="")
    schema = to_jsonschema(M)
    p = schema["properties"]
    assert p["i"]["type"] == "integer"
    assert p["f"]["type"] == "number"
    assert p["b"]["type"] == "boolean"
    assert p["s"]["type"] == "string"


# ── inputs_hash stability ────────────────────────────────────────────


def test_inputs_hash_stable_across_runs():
    p = {"a": 1, "b": "two", "nested": {"x": [1, 2, 3]}}
    h1 = inputs_hash(p)
    h2 = inputs_hash({"b": "two", "nested": {"x": [1, 2, 3]}, "a": 1})  # different order
    assert h1 == h2  # sort_keys=True makes order irrelevant
    assert len(h1) == 16


def test_inputs_hash_changes_with_value():
    a = inputs_hash({"x": 1.0})
    b = inputs_hash({"x": 1.000001})
    assert a != b


def test_inputs_hash_handles_complex_and_dataclass():
    c = Cond("phase", 0, 0, 0.01)
    h = inputs_hash({"network": c, "fault_at": complex(1.0, 2.0)})
    assert len(h) == 16  # works without raising
