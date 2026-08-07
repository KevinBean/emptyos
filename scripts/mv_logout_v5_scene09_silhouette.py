"""Build the final art-directed Log Out v5 human bookend shot."""

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


def rain_meshes(rig: bpy.types.Object) -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.parent == rig and obj.name.startswith("GEO-rain_")
    ]


def make_silhouette_material(meshes: list[bpy.types.Object]) -> bpy.types.Material:
    material = bpy.data.materials.new("Log Out human silhouette")
    material.use_nodes = True
    material.diffuse_color = (0.008, 0.028, 0.036, 1.0)
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"

    tree = material.node_tree
    if tree is None:
        raise RuntimeError("Failed to create silhouette material node tree")
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    mix = tree.nodes.new("ShaderNodeMixShader")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = (0.008, 0.028, 0.036, 1.0)
    principled.inputs["Roughness"].default_value = 0.82
    principled.inputs["Metallic"].default_value = 0.0
    emission = principled.inputs.get("Emission Color") or principled.inputs.get("Emission")
    if emission is not None:
        emission.default_value = (0.006, 0.025, 0.034, 1.0)
    emission_strength = principled.inputs.get("Emission Strength")
    if emission_strength is not None:
        emission_strength.default_value = 0.14
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    tree.links.new(principled.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    visibility = mix.inputs[0]
    for frame, value in ((1, 1.0), (80, 1.0), (104, 0.78), (141, 0.0)):
        visibility.default_value = value
        visibility.keyframe_insert(data_path="default_value", frame=frame)

    for obj in meshes:
        for slot in obj.material_slots:
            slot.material = material
        if not obj.material_slots:
            obj.data.materials.append(material)
    return material


def correct_ik_pose(rig: bpy.types.Object) -> dict[str, tuple[float, float, float]]:
    scene = bpy.context.scene
    left = rig.pose.bones["IK-Hand.L"]
    right = rig.pose.bones["IK-Hand.R"]

    # CloudRig hand-local X maps to vertical world motion with opposite sign
    # on mirrored limbs. Keyframe the animatable local transform; pose.matrix
    # is only an evaluated-frame override and is lost during animation render.
    for frame in (scene.frame_start, 70, scene.frame_end):
        scene.frame_set(frame)
        left.location = Vector((0.45, -0.06, 0.0))
        right.location = Vector((-0.45, -0.06, 0.0))
        left.keyframe_insert(data_path="location", frame=frame, group=left.name)
        right.keyframe_insert(data_path="location", frame=frame, group=right.name)
    scene.frame_set(scene.frame_start)
    bpy.context.view_layer.update()
    return {
        "left_local": tuple(round(value, 6) for value in left.location),
        "right_local": tuple(round(value, 6) for value in right.location),
    }


def configure_render(output_dir: Path) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = str(output_dir / "frame-")


def render(mode: str, output_dir: Path) -> None:
    scene = bpy.context.scene
    if mode == "preview":
        preview_dir = output_dir / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        for frame in (scene.frame_start, (scene.frame_start + scene.frame_end) // 2, scene.frame_end):
            scene.frame_set(frame)
            scene.render.filepath = str(preview_dir / f"frame-{frame:04d}.png")
            bpy.ops.render.render(write_still=True)
        return
    if mode != "final":
        raise RuntimeError(f"Unknown mode: {mode}")
    output_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_dir / "frame-")
    bpy.ops.render.render(animation=True)


def main() -> None:
    args = args_after_separator()
    if len(args) != 3:
        raise SystemExit("Expected after --: preview|final OUTPUT_BLEND OUTPUT_DIR")
    mode, output_blend_arg, output_dir_arg = args
    output_blend = Path(output_blend_arg).resolve()
    output_dir = Path(output_dir_arg).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    rig = bpy.data.objects.get("RIG-Rain")
    if rig is None:
        raise RuntimeError("RIG-Rain is required")
    meshes = rain_meshes(rig)
    if len(meshes) < 10:
        raise RuntimeError(f"Expected complete Rain character, found {len(meshes)} render meshes")

    pose = correct_ik_pose(rig)
    material = make_silhouette_material(meshes)
    configure_render(output_dir)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    render(mode, output_dir)
    print(
        {
            "mode": mode,
            "output_blend": str(output_blend),
            "output_dir": str(output_dir),
            "rain_meshes": len(meshes),
            "material": material.name,
            "ik_pose": pose,
        }
    )


if __name__ == "__main__":
    main()
