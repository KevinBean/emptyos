"""Coordinate-agnostic DWG/DXF entity reader.

Reads an AutoCAD DXF (ASCII/binary) or DWG file into a flat list of typed
entity dicts **in the file's native local coordinates** — no transform, no
georeferencing. Consumers (e.g. the geo-cad import path) pair this with
``emptyos.sdk.georef`` to project the coordinates to WGS84.

```python
ents = read_entities("survey.dwg", oda_converter="/path/ODAFileConverter")
# [{"kind": "polyline", "coords": [[0,0],[10,5]], "meta": {"closed": False, "layer": "0"}}, ...]
```

Each entity: ``{"kind", "coords": [[x, y], ...], "meta": {...}}`` where ``kind`` ∈
``{"line","polyline","point","circle","arc","text"}``. ``coords`` are the
defining points (for circle/arc the single centre — radius/angles live in
``meta`` so a consumer can approximate a ring).

Dependencies are optional and imported lazily (so the daemon boots without
them, per ``.claude/rules/environment.md``):
- ``.dxf`` → ``ezdxf`` (``pip install 'emptyos[dxf]'``).
- ``.dwg`` → ``ezdxf.addons.odafc`` + the external **ODA File Converter** (free).
  DWG is a closed binary format; ezdxf can only read it *via* the converter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _require_ezdxf():
    try:
        import ezdxf  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "ezdxf not installed — pip install 'emptyos[dxf]' or "
            "pip install 'ezdxf>=1.3' to read DXF/DWG files."
        ) from exc
    return ezdxf


def _open_dwg(path: Path, oda_converter: str | None):
    """Read a binary DWG via the ODA File Converter (through ezdxf's odafc addon)."""
    ezdxf = _require_ezdxf()
    try:
        from ezdxf.addons import odafc
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "ezdxf.addons.odafc unavailable — upgrade ezdxf to read DWG."
        ) from exc
    if oda_converter:
        # ezdxf reads the converter location from its options registry.
        try:
            ezdxf.options.set("odafc-addon", "win_exec_path", oda_converter)
            ezdxf.options.set("odafc-addon", "unix_exec_path", oda_converter)
        except Exception:  # pragma: no cover - older ezdxf option API
            pass
    try:
        return odafc.readfile(str(path))
    except Exception as exc:
        raise RuntimeError(
            f"could not convert DWG '{path.name}' — the ODA File Converter is "
            "required for binary .dwg (install it free from the ODA site and set "
            "[apps.<id>] oda_converter, or Save-As DXF in AutoCAD). "
            f"Underlying error: {exc}"
        ) from exc


def _map_entity(e, t: str) -> dict[str, Any] | None:
    """One DXF entity → coordinate-agnostic ``{kind, coords, meta}`` (or None)."""
    if t == "LINE":
        return {"kind": "line",
                "coords": [[e.dxf.start.x, e.dxf.start.y],
                           [e.dxf.end.x, e.dxf.end.y]],
                "meta": {}}
    if t == "LWPOLYLINE":
        pts = e.get_points(format="xy")
        return {"kind": "polyline",
                "coords": [[float(p[0]), float(p[1])] for p in pts],
                "meta": {"closed": bool(e.closed)}}
    if t == "POLYLINE" and getattr(e, "is_2d_polyline", False):
        return {"kind": "polyline",
                "coords": [[v.dxf.location.x, v.dxf.location.y] for v in e.vertices],
                "meta": {"closed": bool(e.is_closed)}}
    if t == "CIRCLE":
        return {"kind": "circle",
                "coords": [[e.dxf.center.x, e.dxf.center.y]],
                "meta": {"r": float(e.dxf.radius)}}
    if t == "ARC":
        return {"kind": "arc",
                "coords": [[e.dxf.center.x, e.dxf.center.y]],
                "meta": {"r": float(e.dxf.radius),
                         "start_angle": float(e.dxf.start_angle),
                         "end_angle": float(e.dxf.end_angle)}}
    if t == "POINT":
        return {"kind": "point",
                "coords": [[e.dxf.location.x, e.dxf.location.y]],
                "meta": {}}
    if t == "TEXT":
        return {"kind": "text",
                "coords": [[e.dxf.insert.x, e.dxf.insert.y]],
                "meta": {"text": e.dxf.text}}
    if t == "MTEXT":
        return {"kind": "text",
                "coords": [[e.dxf.insert.x, e.dxf.insert.y]],
                "meta": {"text": e.plain_text()}}
    return None


def read_entities(
    path: str | Path,
    *,
    oda_converter: str | None = None,
    layers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read a DXF/DWG file → list of ``{kind, coords, meta}`` in native coords.

    ``layers`` optionally restricts to those layer names. Entities ezdxf can't
    map (3D solids, splines, inserts, dimensions) are skipped — this reader is
    for the point/line/poly geometry that georeferences cleanly.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".dwg":
        doc = _open_dwg(p, oda_converter)
    elif suffix == ".dxf":
        ezdxf = _require_ezdxf()
        try:
            doc = ezdxf.readfile(str(p))
        except Exception as exc:
            raise RuntimeError(f"could not read DXF '{p.name}': {exc}") from exc
    else:
        raise ValueError(f"unsupported CAD format '{suffix}' (expected .dxf or .dwg)")

    want = set(layers) if layers else None
    out: list[dict[str, Any]] = []
    for e in doc.modelspace():
        layer = getattr(e.dxf, "layer", "0") or "0"
        if want is not None and layer not in want:
            continue
        try:
            ent = _map_entity(e, e.dxftype())
        except Exception:
            continue  # malformed entity — skip, don't abort the whole import
        if ent is None:
            continue
        ent["meta"]["layer"] = layer
        out.append(ent)
    return out


def layer_names(path: str | Path, *, oda_converter: str | None = None) -> list[str]:
    """List the layer names present in a DXF/DWG (for a UI layer picker)."""
    p = Path(path)
    if p.suffix.lower() == ".dwg":
        doc = _open_dwg(p, oda_converter)
    else:
        ezdxf = _require_ezdxf()
        doc = ezdxf.readfile(str(p))
    return sorted({layer.dxf.name for layer in doc.layers})
