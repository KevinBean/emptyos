"""Build the v6 threshold-silhouette and matching vacancy shots."""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def args_after_separator() -> list[str]:
    try:
        index = sys.argv.index("--")
    except ValueError:
        return []
    return sys.argv[index + 1 :]


def descendants(root: bpy.types.Object) -> list[bpy.types.Object]:
    found = []
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        found.append(obj)
        stack.extend(obj.children)
    return found


def look_at(obj: bpy.types.Object, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def hide_tree(root_name: str) -> None:
    root = bpy.data.objects.get(root_name)
    if root is None:
        return
    root.hide_render = True
    for obj in descendants(root):
        obj.hide_render = True


def rain_render_meshes() -> list[bpy.types.Object]:
    keep = []
    # The opening carries human presence through framing and machine response.
    # No visible character asset is permitted unless identity, wardrobe, and
    # performance have been art-directed as one coherent design.
    allowed = set()
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or not obj.name.startswith("GEO-rain_"):
            continue
        obj.hide_render = obj.name not in allowed
        if not obj.hide_render:
            keep.append(obj)
    return keep


def silhouette_material(meshes: list[bpy.types.Object]) -> bpy.types.Material:
    material = bpy.data.materials.get("V6 threshold silhouette")
    if material is None:
        material = bpy.data.materials.new("V6 threshold silhouette")
    material.use_nodes = True
    tree = material.node_tree
    if tree is None:
        raise RuntimeError("silhouette node tree unavailable")
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    facing = tree.nodes.new("ShaderNodeLayerWeight")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.014, 0.072, 0.088, 1.0)
    ramp.color_ramp.elements[1].position = 0.46
    ramp.color_ramp.elements[1].color = (0.0004, 0.0012, 0.0018, 1.0)
    emission.inputs["Strength"].default_value = 0.46
    tree.links.new(facing.outputs["Facing"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], emission.inputs["Color"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])

    for obj in meshes:
        obj.data.materials.clear()
        obj.data.materials.append(material)
    return material


def place_character(root: bpy.types.Object, frame_end: int) -> None:
    if root.animation_data:
        root.animation_data_clear()
    # Keep the performer anonymous: this is a cropped shoulder/back-of-head
    # trace, not a complete character design. The recognizable ponytail and
    # accessories are hidden above, while the larger scale pushes the head
    # beyond the left edge of frame.
    root.location = (-1.72, -5.15, -0.06)
    root.scale = (1.72, 1.72, 1.72)
    for frame, rotation in ((1, 2.73), (frame_end // 2, 2.755), (frame_end, 2.735)):
        root.rotation_euler = (0.0, 0.0, rotation)
        root.keyframe_insert(data_path="rotation_euler", frame=frame)
    # Blender 5 layered Actions no longer expose the legacy action.fcurves
    # collection. Newly inserted keys already use Bezier interpolation.


def configure_camera(root: bpy.types.Object) -> bpy.types.Object:
    scene = bpy.context.scene
    camera = bpy.data.objects.get("Lookdev camera")
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Lookdev camera missing")
    if camera.animation_data:
        camera.animation_data_clear()
    camera.location = (-0.9, -8.45, 2.78)
    camera.data.lens = 54.0
    look_at(camera, Vector((0.05, 1.15, 1.34)))

    focus = bpy.data.objects.get("V6 silhouette focus")
    if focus is None:
        focus = bpy.data.objects.new("V6 silhouette focus", None)
        scene.collection.objects.link(focus)
    focus.parent = root
    focus.location = (0.0, 0.0, 1.22)
    camera.data.dof.use_dof = True
    camera.data.dof.focus_object = bpy.data.objects.get("Focus target") or focus
    camera.data.dof.aperture_fstop = 1.9
    scene.camera = camera
    return camera


def make_fade_plane(camera: bpy.types.Object, frame_end: int) -> None:
    mesh = bpy.data.meshes.new("V6 camera fade mesh")
    mesh.from_pydata(
        [(-2.0, -1.2, 0.0), (2.0, -1.2, 0.0), (2.0, 1.2, 0.0), (-2.0, 1.2, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    plane = bpy.data.objects.new("V6 authored fade to zero", mesh)
    bpy.context.scene.collection.objects.link(plane)
    plane.parent = camera
    plane.location = (0.0, 0.0, -0.35)

    material = bpy.data.materials.new("V6 black withdrawal")
    material.use_nodes = True
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    mix = tree.nodes.new("ShaderNodeMixShader")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    black = tree.nodes.new("ShaderNodeEmission")
    black.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    black.inputs["Strength"].default_value = 0.0
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    tree.links.new(black.outputs["Emission"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    plane.data.materials.append(material)

    factor = mix.inputs[0]
    for frame, value in ((1, 0.0), (226, 0.0), (238, 0.22), (frame_end - 4, 0.82), (frame_end, 1.0)):
        factor.default_value = value
        factor.keyframe_insert(data_path="default_value", frame=frame)


def dim_world(frame_end: int) -> None:
    scene = bpy.context.scene
    for obj in scene.objects:
        if obj.type != "LIGHT":
            continue
        if obj.data.animation_data:
            obj.data.animation_data_clear()
        base = float(obj.data.energy)
        for frame, scale in ((1, 0.90), (18, 0.78), (80, 0.74), (175, 0.65), (225, 0.38), (242, 0.14), (frame_end, 0.0)):
            obj.data.energy = base * scale
            obj.data.keyframe_insert(data_path="energy", frame=frame)


def configure_render(output_dir: Path, frame_end: int) -> None:
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = frame_end
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = str(output_dir / "frame-")


def render(mode: str, output_dir: Path, frames: tuple[int, int, int]) -> None:
    scene = bpy.context.scene
    if mode == "preview":
        preview = output_dir / "previews"
        preview.mkdir(parents=True, exist_ok=True)
        for frame in frames:
            scene.frame_set(frame)
            scene.render.filepath = str(preview / f"frame-{frame:04d}.png")
            bpy.ops.render.render(write_still=True)
        return
    if mode != "final":
        raise RuntimeError(f"unknown render mode {mode}")
    output_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_dir / "frame-")
    bpy.ops.render.render(animation=True)


def main() -> None:
    args = args_after_separator()
    if len(args) != 4:
        raise SystemExit("expected after --: opening|vacancy preview|final OUTPUT_BLEND OUTPUT_DIR")
    variant, mode, output_blend_arg, output_dir_arg = args
    if variant not in {"opening", "vacancy"}:
        raise SystemExit(f"unknown variant {variant}")

    output_blend = Path(output_blend_arg).resolve()
    output_dir = Path(output_dir_arg).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    hide_tree("Residual multimeter")
    hide_tree("Open threshold cabinet")

    root = bpy.data.objects.get("Finished human performance root")
    if root is None:
        raise RuntimeError("Finished human performance root missing")
    frame_end = 96 if variant == "opening" else 250
    place_character(root, frame_end)
    camera = configure_camera(root)

    if variant == "opening":
        # Reject the imported character as a visible design, including helper
        # meshes that do not follow the GEO-rain_* naming convention.
        root.hide_render = True
        for obj in descendants(root):
            obj.hide_render = True
    else:
        root.hide_render = True
        for obj in descendants(root):
            obj.hide_render = True
        dim_world(frame_end)
        make_fade_plane(camera, frame_end)

    configure_render(output_dir, frame_end)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    render(mode, output_dir, (1, frame_end // 2, frame_end))
    print({"variant": variant, "mode": mode, "blend": str(output_blend), "frames": frame_end})


if __name__ == "__main__":
    main()

