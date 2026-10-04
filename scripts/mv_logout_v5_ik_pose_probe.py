"""Render Rain IK-hand pose candidates for the Log Out v5 director cut."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Vector


def args_after_separator() -> list[str]:
    try:
        index = sys.argv.index("--")
    except ValueError:
        return []
    return sys.argv[index + 1 :]


def main() -> None:
    args = args_after_separator()
    if len(args) != 1:
        raise SystemExit("Expected one argument after --: OUTPUT_DIR")

    output_dir = Path(args[0]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    rig = bpy.data.objects.get("RIG-Rain")
    if rig is None:
        raise RuntimeError("RIG-Rain is required")

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.frame_set(1)

    left_hand = rig.pose.bones["IK-Hand.L"]
    right_hand = rig.pose.bones["IK-Hand.R"]
    base_left = left_hand.matrix.translation.copy()
    base_right = right_hand.matrix.translation.copy()

    # CloudRig's FK controls are constrained to its IK solution. Moving the
    # actual IK hands is the smallest reliable correction.
    for drop in (-0.15, -0.25, -0.35, -0.45, -0.55):
        left_hand.matrix.translation = base_left + Vector((-0.06, 0.0, drop))
        right_hand.matrix.translation = base_right + Vector((0.06, 0.0, drop))
        bpy.context.view_layer.update()
        slug = f"{drop:+.2f}".replace("+", "p").replace("-", "m").replace(".", "_")
        scene.render.filepath = str(output_dir / f"ik-hand-z-{slug}.png")
        bpy.ops.render.render(write_still=True)

    print(f"Rendered 5 IK pose candidates to {output_dir}")


if __name__ == "__main__":
    main()
