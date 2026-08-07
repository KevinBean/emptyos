"""Build one scanned, flexing leaf for the v6 organic-release shot."""

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


def hide_tree(root_name: str) -> None:
    root = bpy.data.objects.get(root_name)
    if root is None:
        return
    root.hide_render = True
    for obj in descendants(root):
        obj.hide_render = True


def make_ground_material() -> bpy.types.Material:
    material = bpy.data.materials.new("V6 damp greenhouse soil")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    bump = tree.nodes.new("ShaderNodeBump")
    principled.inputs["Base Color"].default_value = (0.012, 0.016, 0.014, 1.0)
    principled.inputs["Roughness"].default_value = 0.72
    noise.inputs["Scale"].default_value = 5.5
    noise.inputs["Detail"].default_value = 5.0
    noise.inputs["Roughness"].default_value = 0.72
    bump.inputs["Strength"].default_value = 0.22
    bump.inputs["Distance"].default_value = 0.08
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def load_image(path: Path, colorspace: str) -> bpy.types.Image:
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = colorspace
    return image


def make_leaf_material(texture_root: Path) -> bpy.types.Material:
    material = bpy.data.materials.new("V6 scanned leaf — ambientCG LeafSet006")
    material.use_nodes = True
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    tree = material.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    color = tree.nodes.new("ShaderNodeTexImage")
    opacity = tree.nodes.new("ShaderNodeTexImage")
    roughness = tree.nodes.new("ShaderNodeTexImage")
    normal = tree.nodes.new("ShaderNodeTexImage")
    normal_map = tree.nodes.new("ShaderNodeNormalMap")
    hue = tree.nodes.new("ShaderNodeHueSaturation")

    color.image = load_image(texture_root / "LeafSet006_1K-PNG_Color.png", "sRGB")
    opacity.image = load_image(texture_root / "LeafSet006_1K-PNG_Opacity.png", "Non-Color")
    roughness.image = load_image(texture_root / "LeafSet006_1K-PNG_Roughness.png", "Non-Color")
    normal.image = load_image(texture_root / "LeafSet006_1K-PNG_NormalGL.png", "Non-Color")
    hue.inputs["Hue"].default_value = 0.44
    hue.inputs["Saturation"].default_value = 0.55
    hue.inputs["Value"].default_value = 0.34
    principled.inputs["Roughness"].default_value = 0.58

    tree.links.new(color.outputs["Color"], hue.inputs["Color"])
    tree.links.new(hue.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(opacity.outputs["Color"], principled.inputs["Alpha"])
    tree.links.new(roughness.outputs["Color"], principled.inputs["Roughness"])
    tree.links.new(normal.outputs["Color"], normal_map.inputs["Color"])
    tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def build_leaf(texture_root: Path) -> bpy.types.Object:
    columns = 25
    rows = 13
    vertices = []
    faces = []
    uvs = []
    for row in range(rows):
        v = row / (rows - 1)
        across = (v - 0.5) * 0.88
        for column in range(columns):
            u = column / (columns - 1)
            along = (u - 0.5) * 1.72
            arch = 0.055 * math.sin(math.pi * u) * (1.0 - (2.0 * v - 1.0) ** 2)
            curl = 0.035 * math.sin(2.0 * math.pi * u) * (2.0 * v - 1.0)
            vertices.append((along, across, arch + curl))
            uvs.append((0.50 + 0.43 * u, 0.12 + 0.26 * v))
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b = a + 1
            c = a + columns + 1
            d = a + columns
            faces.append((a, b, c, d))

    mesh = bpy.data.meshes.new("V6 scanned leaf mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    uv_layer = mesh.uv_layers.new(name="Leaf atlas UV")
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = uvs[vertex_index]

    leaf = bpy.data.objects.new("V6 single falling scanned leaf", mesh)
    bpy.context.scene.collection.objects.link(leaf)
    leaf.data.materials.append(make_leaf_material(texture_root))
    solidify = leaf.modifiers.new("Natural leaf thickness", "SOLIDIFY")
    solidify.thickness = 0.006
    solidify.offset = 0.0
    subdivision = leaf.modifiers.new("Soft leaf curvature", "SUBSURF")
    subdivision.levels = 1
    subdivision.render_levels = 1

    leaf.shape_key_add(name="Basis")
    flex_left = leaf.shape_key_add(name="Flex left")
    flex_right = leaf.shape_key_add(name="Flex right")
    for index, vertex in enumerate(mesh.vertices):
        u = (index % columns) / (columns - 1)
        v = (index // columns) / (rows - 1)
        envelope = math.sin(math.pi * u)
        lateral = 2.0 * v - 1.0
        flex_left.data[index].co.z += 0.09 * envelope * lateral + 0.025 * math.sin(2.0 * math.pi * u)
        flex_right.data[index].co.z -= 0.075 * envelope * lateral + 0.02 * math.sin(2.0 * math.pi * u + 0.6)

    for frame, left, right in (
        (1, 0.18, 0.0),
        (18, 0.82, 0.05),
        (35, 0.12, 0.68),
        (53, 0.72, 0.08),
        (70, 0.04, 0.78),
        (87, 0.56, 0.18),
        (99, 0.16, 0.44),
    ):
        flex_left.value = left
        flex_right.value = right
        flex_left.keyframe_insert(data_path="value", frame=frame)
        flex_right.keyframe_insert(data_path="value", frame=frame)

    for frame in range(1, 100, 8):
        t = (frame - 1) / 98.0
        leaf.location = (
            -2.35 + 2.45 * t + 0.18 * math.sin(2.0 * math.pi * 1.25 * t + 0.2),
            0.55 + 0.95 * t + 0.10 * math.sin(2.0 * math.pi * 0.8 * t),
            3.45 - 3.85 * t + 0.10 * math.sin(2.0 * math.pi * 2.0 * t),
        )
        leaf.rotation_euler = (
            0.55 + 0.32 * math.sin(2.0 * math.pi * 1.55 * t),
            0.25 * math.sin(2.0 * math.pi * 2.15 * t + 0.7),
            -0.35 + 2.15 * t + 0.22 * math.sin(2.0 * math.pi * 1.1 * t),
        )
        leaf.keyframe_insert(data_path="location", frame=frame)
        leaf.keyframe_insert(data_path="rotation_euler", frame=frame)
    leaf.scale = (1.2, 1.2, 1.2)

    # Blender 5 layered Actions use Bezier for these new keys by default.
    return leaf


def add_leaf_light() -> None:
    data = bpy.data.lights.new("V6 soft leaf edge", "AREA")
    data.energy = 420.0
    data.color = (0.35, 0.68, 0.72)
    data.shape = "DISK"
    data.size = 3.0
    light = bpy.data.objects.new("V6 soft leaf edge", data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (-2.0, -2.0, 5.8)
    light.rotation_euler = (0.35, 0.0, -0.25)


def configure_camera(leaf: bpy.types.Object) -> None:
    camera = bpy.data.objects.get("Lookdev camera")
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError("Lookdev camera missing")
    camera.data.dof.use_dof = True
    camera.data.dof.focus_object = leaf
    camera.data.dof.aperture_fstop = 3.2
    camera.data.lens = 52.0
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
        for frame in (1, 25, 49, 73, 97):
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
    if len(args) != 4:
        raise SystemExit("expected after --: preview|final OUTPUT_BLEND OUTPUT_DIR TEXTURE_ROOT")
    mode, output_blend_arg, output_dir_arg, texture_root_arg = args
    output_blend = Path(output_blend_arg).resolve()
    output_dir = Path(output_dir_arg).resolve()
    texture_root = Path(texture_root_arg).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for obj in bpy.context.scene.objects:
        if obj.name.startswith("Cinematic falling fern") or obj.name.startswith("Cinematic frond"):
            obj.hide_render = True
    hide_tree("Residual multimeter")
    hide_tree("Open threshold cabinet")
    water = bpy.data.objects.get("Black water plane")
    if water is not None:
        water.name = "Damp greenhouse floor"
        water.data.materials.clear()
        water.data.materials.append(make_ground_material())

    leaf = build_leaf(texture_root)
    add_leaf_light()
    configure_camera(leaf)
    configure_render(output_dir)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    render(mode, output_dir)
    print({"mode": mode, "blend": str(output_blend), "leaf": leaf.name, "source": str(texture_root)})


if __name__ == "__main__":
    main()

