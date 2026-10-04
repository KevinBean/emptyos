"""Build a readable water surface with one off-screen impact and damped ripple."""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy


def args_after_separator() -> list[str]:
    try:
        index = sys.argv.index("--")
    except ValueError:
        return []
    return sys.argv[index + 1 :]


def hide_named_fragments() -> None:
    fragments = (
        "single physical water droplet",
        "animated physical ripple surface",
        "black water plane",
    )
    for obj in bpy.context.scene.objects:
        if any(fragment in obj.name.lower() for fragment in fragments):
            obj.hide_render = True


def ripple_height(x: float, y: float, seconds: float) -> float:
    if seconds <= 0.0:
        return 0.0
    cx, cy = -0.52, 0.18
    dx = x - cx
    dy = y - cy
    angle = math.atan2(dy, dx)
    radius = math.hypot(1.035 * dx, 0.965 * dy)
    radius += 0.018 * math.sin(3.0 * angle + 0.7) + 0.011 * math.sin(5.0 * angle)
    wavefront = 0.08 + 1.18 * seconds
    width = 0.12 + 0.025 * seconds
    damping = math.exp(-0.82 * seconds)
    primary = 0.061 * damping * math.exp(-((radius - wavefront) / width) ** 2)
    trough = -0.029 * damping * math.exp(-((radius - wavefront + 0.16) / (width * 1.15)) ** 2)
    secondary = 0.014 * damping * math.exp(-((radius - wavefront + 0.34) / (width * 1.45)) ** 2)
    dimple = -0.041 * math.exp(-5.0 * seconds) * math.exp(-(radius / 0.16) ** 2)
    return primary + trough + secondary + dimple


def build_surface() -> bpy.types.Object:
    columns = 181
    rows = 121
    width = 7.4
    depth = 5.1
    vertices = []
    faces = []
    for row in range(rows):
        y = -1.05 + depth * row / (rows - 1)
        for column in range(columns):
            x = -width / 2.0 + width * column / (columns - 1)
            vertices.append((x, y, 0.055))
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            faces.append((a, a + 1, a + columns + 1, a + columns))

    mesh = bpy.data.meshes.new("V6 damped ripple mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    surface = bpy.data.objects.new("V6 readable black water", mesh)
    bpy.context.scene.collection.objects.link(surface)
    surface.data.materials.append(make_water_material())

    surface.shape_key_add(name="Basis")
    samples = (1, 26, 30, 38, 50, 68, 99)
    keys = []
    for frame in samples:
        key = surface.shape_key_add(name=f"Ripple frame {frame:04d}")
        seconds = max(0.0, (frame - 26) / 24.0)
        elapsed = (frame - 1) / 24.0
        for index, base in enumerate(vertices):
            micro = 0.0045 * math.sin(2.15 * base[0] + 1.35 * base[1] + 1.1 * elapsed)
            micro += 0.0022 * math.sin(-1.15 * base[0] + 2.4 * base[1] + 0.72 * elapsed)
            key.data[index].co.z = base[2] + micro + ripple_height(base[0], base[1], seconds)
        keys.append(key)
    for key_index, key in enumerate(keys):
        for sample_index, frame in enumerate(samples):
            key.value = 1.0 if key_index == sample_index else 0.0
            key.keyframe_insert(data_path="value", frame=frame)
    # Blender 5 layered Actions use Bezier for these new keys by default.
    return surface


def make_water_material() -> bpy.types.Material:
    material = bpy.data.materials.new("V6 grazing-lit black water")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    bump = tree.nodes.new("ShaderNodeBump")
    layer = tree.nodes.new("ShaderNodeLayerWeight")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    mix = tree.nodes.new("ShaderNodeMixRGB")

    noise.noise_dimensions = "4D"
    noise.inputs["W"].default_value = 0.0
    noise.inputs["W"].keyframe_insert(data_path="default_value", frame=1)
    noise.inputs["W"].default_value = 0.42
    noise.inputs["W"].keyframe_insert(data_path="default_value", frame=99)
    noise.inputs["Scale"].default_value = 5.8
    noise.inputs["Detail"].default_value = 4.2
    noise.inputs["Roughness"].default_value = 0.68
    noise.inputs["Distortion"].default_value = 0.16
    bump.inputs["Strength"].default_value = 0.16
    bump.inputs["Distance"].default_value = 0.025
    ramp.color_ramp.elements[0].position = 0.08
    ramp.color_ramp.elements[0].color = (0.028, 0.16, 0.18, 1.0)
    ramp.color_ramp.elements[1].position = 0.74
    ramp.color_ramp.elements[1].color = (0.002, 0.007, 0.01, 1.0)
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 0.72
    mix.inputs[2].default_value = (0.04, 0.14, 0.15, 1.0)
    principled.inputs["Metallic"].default_value = 0.08
    principled.inputs["Roughness"].default_value = 0.16
    principled.inputs["IOR"].default_value = 1.333

    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    tree.links.new(layer.outputs["Facing"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], mix.inputs[1])
    tree.links.new(mix.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def configure_lights() -> None:
    for name in ("Ripple cyan rake", "Ripple amber rake"):
        light = bpy.data.objects.get(name)
        if light is not None and light.type == "LIGHT":
            light.data.energy *= 0.18

    cyan_data = bpy.data.lights.new("V6 water grazing cyan", "AREA")
    cyan_data.energy = 560.0
    cyan_data.color = (0.20, 0.58, 0.66)
    cyan_data.shape = "RECTANGLE"
    cyan_data.size = 5.5
    cyan_data.size_y = 1.0
    cyan = bpy.data.objects.new("V6 water grazing cyan", cyan_data)
    bpy.context.scene.collection.objects.link(cyan)
    cyan.location = (-3.8, 1.8, 1.45)
    cyan.rotation_euler = (1.18, 0.0, -1.05)

    warm_data = bpy.data.lights.new("V6 residual amber water", "AREA")
    warm_data.energy = 95.0
    warm_data.color = (0.62, 0.20, 0.055)
    warm_data.shape = "DISK"
    warm_data.size = 1.8
    warm = bpy.data.objects.new("V6 residual amber water", warm_data)
    bpy.context.scene.collection.objects.link(warm)
    warm.location = (2.8, 2.0, 1.0)
    warm.rotation_euler = (1.24, 0.0, 1.0)


def configure_camera(surface: bpy.types.Object) -> None:
    camera = bpy.data.objects.get("Lookdev camera")
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Lookdev camera missing")
    if camera.animation_data:
        camera.animation_data_clear()
    camera.data.lens = 58.0
    camera.data.dof.use_dof = True
    camera.data.dof.focus_object = surface
    camera.data.dof.aperture_fstop = 4.0
    bpy.context.scene.camera = camera


def configure_render(output_dir: Path) -> None:
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 99
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(output_dir / "frame-")


def render(mode: str, output_dir: Path) -> None:
    scene = bpy.context.scene
    if mode == "preview":
        preview = output_dir / "previews"
        preview.mkdir(parents=True, exist_ok=True)
        for frame in (1, 26, 34, 52, 78, 99):
            scene.frame_set(frame)
            scene.render.filepath = str(preview / f"frame-{frame:04d}.png")
            bpy.ops.render.render(write_still=True)
        return
    if mode != "final":
        raise RuntimeError(f"unknown mode {mode}")
    output_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_dir / "frame-")
    bpy.ops.render.render(animation=True)


def main() -> None:
    args = args_after_separator()
    if len(args) != 3:
        raise SystemExit("expected after --: preview|final OUTPUT_BLEND OUTPUT_DIR")
    mode, output_blend_arg, output_dir_arg = args
    output_blend = Path(output_blend_arg).resolve()
    output_dir = Path(output_dir_arg).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    hide_named_fragments()
    surface = build_surface()
    configure_lights()
    configure_camera(surface)
    configure_render(output_dir)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    render(mode, output_dir)
    print({"mode": mode, "blend": str(output_blend), "surface": surface.name, "visible_droplet": False})


if __name__ == "__main__":
    main()

