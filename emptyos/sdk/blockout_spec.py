"""Declarative blockout scene spec — schema, validation, and the authoring prompt.

The security shape matters more than the schema does. An LLM writes a *scene
spec*, never Python. `blockout_depth_probe.py --spec` then INTERPRETS the
validated spec by dispatching on typed values into primitives we wrote — it
never generates, formats, or execs a line of model output. So there is no
injection surface to harden: the model's text is data that indexes into code,
and every field is range-checked before it reaches Blender.

This is deliberately *not* `blender.run_script()` with model-authored bpy.
That call spawns a headless subprocess, which isolates the running Blender
instance from the local RPC port — it does **not** sandbox the filesystem, the
network, or the Python API. It is a process boundary, not a trust boundary.

Object names are generated (`obj_0`, `obj_1`, …), never taken from the model,
so even a well-formed spec cannot smuggle a string into a name lookup.

Pure stdlib, and it must stay that way: this is imported from **inside
Blender's bundled Python** by `scripts/blockout_depth_probe.py`, which has no
EmptyOS dependencies installed. Importing anything from `emptyos.kernel` here
would break the renderer, not just this module.

It lives in the SDK rather than beside the probe because it has two consumers in
two different trees — the probe (running under Blender) and `music-studio`'s
`blockout.py` (running in the daemon). The app previously reached into
`scripts/` with a `sys.path` insert to get at it; one canonical home removes
that.
"""

from __future__ import annotations

import math
from typing import Any

# Bounds are generous enough for any blockout and tight enough that a
# hallucinated 1e9 coordinate becomes an error instead of a black frame.
MAX_OBJECTS = 200
MAX_COORD = 1000.0
MAX_SCALE = 500.0
MAX_FAR = 10_000.0
MIN_CAMERA_KEYS, MAX_CAMERA_KEYS = 2, 12
OBJECT_TYPES = ("box", "cylinder")


SPEC_SYSTEM = """You lay out rough 3D blockouts for camera-controlled video generation.

You are NOT designing a beautiful scene. You are placing grey boxes so that a
depth render describes the space and the camera move. Material, lighting,
texture and detail are supplied later by an image model — never by you.

Return ONE JSON object, no prose, no markdown fence:

{
  "name": "short_slug",
  "near": <float>, "far": <float>,
  "objects": [
    {"type": "box", "loc": [x, y, z], "scale": [sx, sy, sz]},
    {"type": "cylinder", "loc": [x, y, z], "radius": <float>, "depth": <float>}
  ],
  "camera": [
    {"t": 0.0, "loc": [x, y, z], "rot_deg": [rx, ry, rz]},
    {"t": 1.0, "loc": [x, y, z], "rot_deg": [rx, ry, rz]}
  ]
}

Axes: +X right, +Y forward (away from camera), +Z up. A camera with
rot_deg [90, 0, 0] looks horizontally along +Y; lower rx tilts it downward.
`scale` is the box half-extent, so a 10x2x6 wall is scale [5, 1, 3].
`t` runs 0..1 across the shot and must start at 0.0 and end at 1.0.
`near`/`far` bracket the depth range in metres — set them to just contain the
geometry the camera actually sees.

The failure that matters, and the only one you must design against:

- THE CAMERA MOVE MUST END SOMEWHERE WORTH LOOKING AT. Two of three first
  attempts failed this way — a dolly that ends against a blank wall, or an
  orbit whose only background sits on one side and swings out of frame. Walk
  the move in your head at t=0, t=0.5 and t=1.0 and confirm each has layered
  depth: something near, something mid, something far.
- Do NOT place all background geometry on one side if the camera rotates.
- Do NOT let any frame be filled by a single flat surface at one distance.
- Do NOT add detail, small props, or more than ~40 objects. Blockout only.
- Do NOT emit fields other than those above; unknown fields are rejected.
"""


def _num(v: Any) -> float | None:
    """Accept int/float only — a bool is an int in Python and must not pass."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if not math.isfinite(f) else f


def _vec3(v: Any, limit: float, label: str, errs: list[str]) -> None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        errs.append(f"{label}: expected 3 numbers, got {v!r}")
        return
    for i, c in enumerate(v):
        f = _num(c)
        if f is None:
            errs.append(f"{label}[{i}]: not a finite number ({c!r})")
        elif abs(f) > limit:
            errs.append(f"{label}[{i}]={f} exceeds ±{limit}")


def validate_spec(spec: Any) -> list[str]:
    """Return a list of human-readable errors. Empty list means the spec is safe.

    Strict by construction: unknown keys are errors, not ignored. A spec that
    silently drops a field the model thought it set is worse than a rejection,
    because the render then looks fine and means something else.
    """
    errs: list[str] = []
    if not isinstance(spec, dict):
        return [f"spec must be an object, got {type(spec).__name__}"]

    allowed = {"name", "near", "far", "objects", "camera"}
    for k in set(spec) - allowed:
        errs.append(f"unknown top-level field {k!r}")

    name = spec.get("name", "scene")
    if not isinstance(name, str) or not name:
        errs.append("name: must be a non-empty string")
    elif len(name) > 60:
        errs.append("name: longer than 60 characters")

    near, far = _num(spec.get("near")), _num(spec.get("far"))
    if near is None or far is None:
        errs.append("near/far: both must be finite numbers")
    else:
        if near <= 0:
            errs.append(f"near={near} must be > 0")
        if far <= near:
            errs.append(f"far={far} must be greater than near={near}")
        if far > MAX_FAR:
            errs.append(f"far={far} exceeds {MAX_FAR}")

    objects = spec.get("objects")
    if not isinstance(objects, list) or not objects:
        errs.append("objects: must be a non-empty list")
    elif len(objects) > MAX_OBJECTS:
        errs.append(f"objects: {len(objects)} exceeds the {MAX_OBJECTS} cap")
    else:
        for i, o in enumerate(objects):
            if not isinstance(o, dict):
                errs.append(f"objects[{i}]: not an object")
                continue
            t = o.get("type")
            if t not in OBJECT_TYPES:
                errs.append(f"objects[{i}].type={t!r} not in {OBJECT_TYPES}")
                continue
            keys = {"box": {"type", "loc", "scale"},
                    "cylinder": {"type", "loc", "radius", "depth"}}[t]
            for k in set(o) - keys:
                errs.append(f"objects[{i}]: unknown field {k!r} for type {t!r}")
            _vec3(o.get("loc"), MAX_COORD, f"objects[{i}].loc", errs)
            if t == "box":
                _vec3(o.get("scale"), MAX_SCALE, f"objects[{i}].scale", errs)
                sc = o.get("scale")
                if isinstance(sc, (list, tuple)) and len(sc) == 3:
                    for j, c in enumerate(sc):
                        f = _num(c)
                        if f is not None and f <= 0:
                            errs.append(f"objects[{i}].scale[{j}]={f} must be > 0")
            else:
                for f_name in ("radius", "depth"):
                    f = _num(o.get(f_name))
                    if f is None:
                        errs.append(f"objects[{i}].{f_name}: not a finite number")
                    elif not (0 < f <= MAX_SCALE):
                        errs.append(f"objects[{i}].{f_name}={f} outside (0, {MAX_SCALE}]")

    camera = spec.get("camera")
    if not isinstance(camera, list):
        errs.append("camera: must be a list of keyframes")
    elif not (MIN_CAMERA_KEYS <= len(camera) <= MAX_CAMERA_KEYS):
        errs.append(
            f"camera: {len(camera)} keys, expected "
            f"{MIN_CAMERA_KEYS}..{MAX_CAMERA_KEYS}"
        )
    else:
        prev_t = None
        for i, k in enumerate(camera):
            if not isinstance(k, dict):
                errs.append(f"camera[{i}]: not an object")
                continue
            for extra in set(k) - {"t", "loc", "rot_deg"}:
                errs.append(f"camera[{i}]: unknown field {extra!r}")
            t = _num(k.get("t"))
            if t is None or not (0.0 <= t <= 1.0):
                errs.append(f"camera[{i}].t={k.get('t')!r} must be a number in 0..1")
            else:
                if prev_t is not None and t <= prev_t:
                    errs.append(f"camera[{i}].t={t} must be greater than {prev_t}")
                prev_t = t
            _vec3(k.get("loc"), MAX_COORD, f"camera[{i}].loc", errs)
            _vec3(k.get("rot_deg"), 360.0, f"camera[{i}].rot_deg", errs)
        first, last = _num(camera[0].get("t")) if isinstance(camera[0], dict) else None, \
            _num(camera[-1].get("t")) if isinstance(camera[-1], dict) else None
        if first is not None and first != 0.0:
            errs.append(f"camera[0].t={first} must be exactly 0.0")
        if last is not None and last != 1.0:
            errs.append(f"camera[-1].t={last} must be exactly 1.0")

    return errs


def parse_spec(text: str) -> tuple[dict | None, list[str]]:
    """Parse a model reply into a validated spec.

    Tolerates a ```json fence because models add one regardless of instruction,
    but tolerates nothing else — the parsed result still faces validate_spec.
    """
    import json

    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None, ["no JSON object found in the reply"]
    try:
        spec = json.loads(s[start:end + 1])
    except Exception as exc:
        return None, [f"JSON parse failed: {exc}"]
    errs = validate_spec(spec)
    return (None, errs) if errs else (spec, [])
