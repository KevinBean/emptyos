"""Hand-written reference model — a 2-DOF articulated desk lamp.

This is what an LLM-written `model.py` should look like. The agent loop
(`apps/personal/robot-modeller/harness.py`) reads this file as a few-shot example
when prompting the model. Smoke test in `tests/test_smoke.py` also imports it.

Parts:
    base    — heavy weighted base (cylinder)
    arm     — vertical pole (cylinder), revolute joint to base
    head    — lamp head (box), revolute joint to top of arm
"""

from __future__ import annotations

import math

from engines.articulated import (
    ArticulatedObject,
    Box,
    Cylinder,
    Fixed,
    Material,
    Origin,
    Part,
    Revolute,
    Sphere,
)


def build_object_model() -> ArticulatedObject:
    """Build and return the desk lamp model. The agent loop calls this entry point."""
    matte_black = Material(name="matte_black", rgba=(0.15, 0.15, 0.15, 1.0))
    brass = Material(name="brass", rgba=(0.72, 0.55, 0.20, 1.0))

    model = ArticulatedObject(name="desk_lamp", materials=[matte_black, brass])

    # Heavy disc base, centered at origin
    model.add_part(Part(
        name="base",
        geometry=Cylinder(radius=0.08, length=0.02),
        origin=Origin(xyz=(0, 0, 0.01)),  # bottom at z=0
        material=matte_black,
    ))

    # Vertical arm, mounted on top of the base
    model.add_part(Part(
        name="arm",
        geometry=Cylinder(radius=0.012, length=0.30),
        origin=Origin(xyz=(0, 0, 0.15)),  # center of arm above its joint
        material=brass,
    ))

    # Lamp head — small box, mounted at top of arm
    model.add_part(Part(
        name="head",
        geometry=Box(size=(0.08, 0.05, 0.05)),
        origin=Origin(xyz=(0.04, 0, 0)),
        material=matte_black,
    ))

    # Base-to-arm: revolute around vertical axis (panning)
    model.add_joint(Revolute(
        name="base_pan",
        parent="base",
        child="arm",
        axis=(0, 0, 1),
        origin=Origin(xyz=(0, 0, 0.02)),  # at top of base
        lower=-math.pi, upper=math.pi,
    ))

    # Arm-to-head: revolute around lateral axis (tilting)
    model.add_joint(Revolute(
        name="head_tilt",
        parent="arm",
        child="head",
        axis=(0, 1, 0),
        origin=Origin(xyz=(0, 0, 0.30)),  # at top of arm
        lower=-math.pi / 2, upper=math.pi / 2,
    ))

    return model


if __name__ == "__main__":
    from engines.articulated import TestContext, export_urdf

    model = build_object_model()
    ctx = TestContext(model)
    ctx.run_baseline()
    print("Report:", ctx.report())
    if ctx.ok:
        out = export_urdf(model, "desk_lamp.urdf")
        print(f"URDF written: {out}")
    else:
        print("Compile errors:")
        for e in ctx.errors:
            print(f"  [{e.code}] {e.message} (at {e.where})")
