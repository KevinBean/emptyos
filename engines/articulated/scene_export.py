"""Scene export — per-object URDFs + a `scene.json` placement manifest.

URDF format expresses one robot (single root link) per file. To represent a
multi-object scene (e.g. "a lamp on a table"), we write one URDF per
ArticulatedObject and a sidecar `scene.json` that names each object's URDF
file plus its placement (xyz/rpy) in the scene frame.

The viewer (apps/personal/robot-modeller/pages/urdf-viewer.js) parses `scene.json`,
fetches each URDF, parses it with the existing single-object loader, and
applies the placement as a parent transform. Joints inside each object
remain articulable; objects in the scene do not articulate against each
other (no inter-object joints).

Sidecar shape:
    {
      "scene_name": "study_desk",
      "object_count": 2,
      "objects": [
        {"name": "table", "urdf": "table.urdf", "xyz": [0,0,0], "rpy": [0,0,0],
         "part_count": 5, "joint_count": 4},
        {"name": "desk_lamp", "urdf": "desk_lamp.urdf", "xyz": [0.25, 0.15, 0.78],
         "rpy": [0,0,0.5], "part_count": 6, "joint_count": 3}
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from .gltf_export import export_glb
from .types import Placement, Scene
from .urdf_export import export_urdf


def export_scene(scene: Scene, out_dir: str | Path,
                 *, texture_base_url: str | None = None) -> dict:
    """Write per-object URDFs + `scene.json` under `out_dir`.

    `out_dir` is created if missing. Existing URDFs with the same names are
    overwritten — caller's responsibility to allocate a fresh dir per record.

    Returns the scene manifest dict (also written to `scene.json`). Raises
    ValueError on scene-level invariant failures, propagates exceptions from
    `export_urdf` per-object (single-root check, etc.).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    scene_errs = scene.validate()
    if scene_errs:
        raise ValueError(f"Scene invariants failed: {'; '.join(scene_errs)}")

    objects_manifest: list[dict] = []
    for obj in scene.objects:
        urdf_filename = f"{obj.name}.urdf"
        glb_filename = f"{obj.name}.glb"
        urdf_path = out / urdf_filename
        glb_path = out / glb_filename
        # export_urdf raises ValueError if the object has more than one root
        # part — propagate so the caller (compile_scene_source) can surface
        # which object failed without us catching + re-raising here.
        export_urdf(obj, urdf_path)
        # Tier 3 M1: also export glTF visual. Failure is non-fatal —
        # URDF still works, viewer falls back. We track per-object glb
        # status in the manifest so callers can detect.
        glb_ok = True
        try:
            export_glb(obj, glb_path, texture_base_url=texture_base_url)
        except Exception:
            glb_ok = False
        placement = scene.placements.get(obj.name, Placement())
        entry = {
            "name": obj.name,
            "urdf": urdf_filename,
            "xyz": list(placement.xyz),
            "rpy": list(placement.rpy),
            "part_count": len(obj.parts),
            "joint_count": len(obj.joints),
            "urdf_size_bytes": urdf_path.stat().st_size,
        }
        if glb_ok:
            entry["glb"] = glb_filename
            entry["glb_size_bytes"] = glb_path.stat().st_size
        objects_manifest.append(entry)

    manifest = {
        "scene_name": scene.name,
        "object_count": len(scene.objects),
        "objects": objects_manifest,
    }
    scene_json_path = out / "scene.json"
    scene_json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
