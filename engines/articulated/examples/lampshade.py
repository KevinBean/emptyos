"""Hand-written reference model — a lampshade demonstrating Mesh geometry.

Shows the Mesh primitive in use: a CadQuery callable builds a lofted profile
(wide bottom rim narrowing to a smaller top rim), wrapped in a Mesh, attached
to a brass stem with a fixed joint. The engine triangulates the loft at
export time and writes both a sibling .stl (URDF reference) and embedded
triangles in the glb.

Compare with desk_lamp.py — that one uses only inline primitives (Box,
Cylinder, Sphere). This one swaps the head for an organic lofted shade.
"""

from __future__ import annotations

import math

from engines.articulated import (
    ArticulatedObject,
    Cylinder,
    Fixed,
    Material,
    Mesh,
    Origin,
    Part,
    PbrMaterial,
)


def _build_shade_shape():
    """Lofted lampshade — circular rim of 0.15m at the bottom, narrowing to
    0.08m at the top over a height of 0.18m. Returns a CadQuery solid."""
    import cadquery as cq

    return (
        cq.Workplane("XY")
        .circle(0.15)
        .workplane(offset=0.18)
        .circle(0.08)
        .loft(combine=True)
    )


def build_object_model() -> ArticulatedObject:
    """Build a brass-stem lamp with a lofted fabric shade. Agent-loop entry point."""
    brass = PbrMaterial(name="brass")
    linen = PbrMaterial(name="linen")

    model = ArticulatedObject(name="lampshade_lamp")

    # Heavy disc base
    model.add_part(Part(
        name="base",
        geometry=Cylinder(radius=0.08, length=0.02),
        origin=Origin(xyz=(0, 0, 0.01)),
        material=brass,
    ))

    # Vertical stem
    model.add_part(Part(
        name="stem",
        geometry=Cylinder(radius=0.012, length=0.30),
        origin=Origin(xyz=(0, 0, 0.15)),
        material=brass,
    ))

    # Lofted lampshade — Mesh geometry. The CadQuery callable runs once at
    # export time; the resulting shape is cached on the Mesh instance and
    # reused by both the URDF exporter (writes shade.stl) and the glTF
    # exporter (embeds tessellated triangles).
    model.add_part(Part(
        name="shade",
        geometry=Mesh(build=_build_shade_shape, tolerance=0.003),
        # Origin places the shade's local origin (its bottom rim centre) at
        # the top of the stem in the part frame. The shade's local +Z is up.
        origin=Origin(xyz=(0, 0, 0)),
        material=linen,
    ))

    # Stem rises out of the base
    model.add_joint(Fixed(
        name="base_to_stem",
        parent="base",
        child="stem",
        origin=Origin(xyz=(0, 0, 0.02)),
    ))

    # Shade sits at the top of the stem
    model.add_joint(Fixed(
        name="stem_to_shade",
        parent="stem",
        child="shade",
        origin=Origin(xyz=(0, 0, 0.30)),
    ))

    return model


if __name__ == "__main__":
    from engines.articulated import TestContext, export_urdf, export_glb

    model = build_object_model()
    ctx = TestContext(model)
    ctx.run_baseline()
    print("Report:", ctx.report())
    if ctx.ok:
        out = export_urdf(model, "lampshade_lamp.urdf")
        print(f"URDF written: {out}")
        glb_out = export_glb(model, "lampshade_lamp.glb")
        print(f"glb written: {glb_out}")
    else:
        print("Compile errors:")
        for e in ctx.errors:
            print(f"  [{e.code}] {e.message} (at {e.where})")
