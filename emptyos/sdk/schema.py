"""Calculator framework — typed I/O via dataclass metadata.

Wraps `dataclasses.field` with EmptyOS-specific metadata (units, ranges,
help text) so:

  - existing dataclass models migrate by ADDING metadata, not rewriting;
  - validators flag out-of-range values at API boundaries;
  - UIs render labels / units / tooltips from the dataclass alone;
  - cross-app introspection works via `to_jsonschema(cls)`.

Convention: keep `schema_field` purely additive over `dataclasses.field`.
Anything that doesn't need calculator metadata stays a plain `field()`
(or no field() at all) — no forced migration.

Usage:

    from dataclasses import dataclass
    from emptyos.sdk import schema_field

    @dataclass
    class Conductor:
        role: str = schema_field(help="phase | sheath | sky_wire | ...")
        y_m: float = schema_field(unit="m", help="Horizontal position")
        z_m: float = schema_field(unit="m", help="Vertical (negative = buried)")
        outer_radius_m: float = schema_field(unit="m", range=(1e-4, 0.2))
        rel_permeability: float = schema_field(default=1.0, range=(1.0, 100.0))
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import field as dc_field
from typing import Any

# Metadata keys we tuck under `field.metadata` — namespaced so we never clash
# with other libraries that use the same dict.
_KEY_UNIT = "eos_unit"
_KEY_RANGE = "eos_range"
_KEY_HELP = "eos_help"


def schema_field(
    *,
    unit: str = "",
    range: tuple[float | None, float | None] | None = None,
    default: Any = ...,
    default_factory: Any = ...,
    help: str = "",
    **kwargs: Any,
) -> Any:
    """Wrap `dataclasses.field` with calculator metadata.

    Args:
        unit: free-form unit symbol — "V", "A", "Ω", "m", "Hz", "Ω·m", etc.
              Rendered verbatim in UI; not converted.
        range: (min, max) inclusive; either bound can be None for unbounded.
        default: dataclass default; mutually exclusive with default_factory.
        default_factory: zero-arg callable producing default; for mutable defaults.
        help: one-line description, rendered in tooltips and JSON-Schema desc.

    Any extra kwargs pass through to `dataclasses.field()`.
    """
    metadata = dict(kwargs.pop("metadata", {}))
    metadata[_KEY_UNIT] = unit
    metadata[_KEY_RANGE] = range
    metadata[_KEY_HELP] = help
    if default is not ... and default_factory is not ...:
        raise ValueError("schema_field: pass at most one of default / default_factory")
    if default_factory is not ...:
        return dc_field(default_factory=default_factory, metadata=metadata, **kwargs)
    if default is not ...:
        return dc_field(default=default, metadata=metadata, **kwargs)
    return dc_field(metadata=metadata, **kwargs)


def field_unit(f: dataclasses.Field) -> str:
    return str(f.metadata.get(_KEY_UNIT) or "")


def field_range(f: dataclasses.Field) -> tuple[float | None, float | None] | None:
    r = f.metadata.get(_KEY_RANGE)
    if r is None:
        return None
    if not isinstance(r, (tuple, list)) or len(r) != 2:
        return None
    return (r[0], r[1])


def field_help(f: dataclasses.Field) -> str:
    return str(f.metadata.get(_KEY_HELP) or "")


# ── Validation ───────────────────────────────────────────────────────


def validate(instance: Any) -> list[str]:
    """Walk dataclass fields, return human-readable validation errors.

    Empty list means valid. Recurses into nested dataclasses and into lists
    of dataclasses (typical for power-systems Network → Terminal → Subsection
    → Conductor structure). Skips fields without `eos_range` metadata.
    """
    errors: list[str] = []
    _walk(instance, "", errors)
    return errors


def _walk(obj: Any, prefix: str, errors: list[str]) -> None:
    if obj is None:
        return
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name, None)
            path = f"{prefix}.{f.name}" if prefix else f.name
            r = field_range(f)
            if r is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                lo, hi = r
                if lo is not None and value < lo:
                    errors.append(f"{path} = {value} below minimum {lo}")
                if hi is not None and value > hi:
                    errors.append(f"{path} = {value} above maximum {hi}")
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    errors.append(f"{path} = {value} is not finite")
            # Recurse — nested dataclasses or list[dataclass]
            if dataclasses.is_dataclass(value) and not isinstance(value, type):
                _walk(value, path, errors)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    _walk(item, f"{path}[{i}]", errors)


# ── JSON-Schema-shaped introspection ─────────────────────────────────


_PRIM_MAP = {
    int: "integer",
    float: "number",
    bool: "boolean",
    str: "string",
}
# String-form annotations (from `from __future__ import annotations`)
_PRIM_NAME_MAP = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "str": "string",
    "complex": "string",  # serialized via _json_default
}


def to_jsonschema(cls: type) -> dict:
    """Introspect a dataclass into a JSON-Schema-shaped dict.

    Lossy in the `Optional[X] | Y | None` direction — we don't try to
    reconstruct full union/generic typing. Goal is to give the UI enough
    to render labels + units + ranges, not full schema validation.
    """
    if not dataclasses.is_dataclass(cls) or isinstance(cls, type) is False:
        # accept both class and instance forms; coerce
        if dataclasses.is_dataclass(cls):
            cls = type(cls)
    if not (dataclasses.is_dataclass(cls) and isinstance(cls, type)):
        return {"type": "object"}

    properties: dict[str, dict] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        prop: dict[str, Any] = {}
        ann = f.type
        if isinstance(ann, str):
            # PEP 563 string-form annotation (from __future__ import annotations)
            prop["type"] = _PRIM_NAME_MAP.get(ann, "string")
        elif ann in _PRIM_MAP:
            prop["type"] = _PRIM_MAP[ann]
        elif dataclasses.is_dataclass(ann) and isinstance(ann, type):
            prop = to_jsonschema(ann)
        else:
            prop["type"] = "string"  # fallback for complex/list/union
        unit = field_unit(f)
        if unit:
            prop["x-eos-unit"] = unit
        rng = field_range(f)
        if rng:
            lo, hi = rng
            if lo is not None:
                prop["minimum"] = lo
            if hi is not None:
                prop["maximum"] = hi
        h = field_help(f)
        if h:
            prop["description"] = h
        properties[f.name] = prop
        # Required = no default and no default_factory
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    out: dict = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


# ── Stable input hashing for cache keys + provenance ─────────────────


def inputs_hash(payload: Any) -> str:
    """Deterministic, short hash of any JSON-serializable payload.

    Used as a cache key + provenance fingerprint. Stable across runs as long
    as the JSON encoding is stable (sort_keys=True). Returns first 16 hex
    chars of sha256 — collision probability negligible for ~10^9 unique
    inputs per app, plenty for a calculator.
    """
    try:
        canonical = json.dumps(payload, sort_keys=True, default=_json_default)
    except (TypeError, ValueError):
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _json_default(o: Any) -> Any:
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, complex):
        return {"_re": o.real, "_im": o.imag}
    return repr(o)
