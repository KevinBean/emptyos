"""Blockout -> depth-pass probe / control-sequence renderer (Blender 5.0).

Grey-box scenes with a real camera move, rendered as ControlNet-convention
depth (white = near, black = far) over a FIXED near/far range.

Two modes:

* ``--sample`` renders three diagnostic frames (first / middle / last). Use it
  to eyeball a new scene before paying for the full range.
* default renders the whole ``--frames`` range as a lossless PNG sequence,
  which is what a video ControlNet / VACE ``control_video`` actually consumes.
  Feed the directory to ``VHS_LoadImagesPath`` — PNGs keep the depth ramp
  intact, where an h264 intermediate would chroma-subsample and band it.

Three deliberate choices worth keeping if this graduates:

1. ``view_layer.material_override`` + an emission shader instead of the
   compositor. Blender 5.0 removed ``scene.node_tree`` (it is
   ``compositing_node_group`` now), so the classic
   RenderLayers->Normalize->Composite recipe is version-fragile. A material
   override is stable API and renders depth straight out of the beauty pass.
2. A FIXED near/far per scene, never a per-frame normalize. Auto-normalising
   each frame re-maps the range whenever the camera moves, so the control
   signal breathes and the restyle flickers. The camera move is exactly when
   this bites.
3. Camera keys sit on a normalised 0..1 arc and scale to the requested frame
   count, so the same scene renders correctly at 49 or 121 frames. Frame counts
   must sit on the I2V latent grid (Wan 2.2 wants 4n+1, LTX 8n+1 — snap to the
   stricter; 49 and 121 satisfy both).

Usage:
    blender -b -noaudio -P blockout_depth_probe.py -- <scene> <outdir>
            [--frames 49] [--width 832] [--height 480] [--sample]

    # declarative spec (the LLM-authoring path — see blockout_spec.py):
    blender -b -noaudio -P blockout_depth_probe.py -- spec <outdir>
            --spec scene.json [--frames 49] [--width 832] [--height 480]

The first positional is the built-in scene name; with --spec it is ignored and
the spec's own `name` is used. Exit code 2 means the spec was rejected — the
renderer refuses rather than producing something the spec did not describe.
"""

import sys
import math
import bpy


# ----------------------------------------------------------------- utilities

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0, 0, 0, 1)
    bpy.context.scene.world = world


def box(name, loc, scale):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    return o


def cyl(name, loc, r, d):
    bpy.ops.mesh.primitive_cylinder_add(location=loc, radius=r, depth=d)
    o = bpy.context.active_object
    o.name = name
    return o


def depth_material(near, far):
    """Emission shader: camera view-Z remapped to 1.0 near -> 0.0 far."""
    m = bpy.data.materials.new("EOS_Depth")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    cam = nt.nodes.new("ShaderNodeCameraData")
    rng = nt.nodes.new("ShaderNodeMapRange")
    emi = nt.nodes.new("ShaderNodeEmission")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    rng.inputs["From Min"].default_value = near
    rng.inputs["From Max"].default_value = far
    rng.inputs["To Min"].default_value = 1.0   # near = white
    rng.inputs["To Max"].default_value = 0.0   # far  = black
    rng.clamp = True
    nt.links.new(cam.outputs["View Z Depth"], rng.inputs["Value"])
    nt.links.new(rng.outputs[0], emi.inputs["Color"])
    nt.links.new(emi.outputs[0], out.inputs["Surface"])
    return m


def camera(loc, rot):
    bpy.ops.object.camera_add(location=loc, rotation=rot)
    c = bpy.context.active_object
    bpy.context.scene.camera = c
    return c


def key_camera(cam, keys, last):
    """keys: [(t, location, rotation_euler)] with t in 0..1, scaled to 1..last."""
    for t, loc, rot in keys:
        cam.location = loc
        cam.rotation_euler = rot
        cam.keyframe_insert("location", frame=1 + round(t * (last - 1)))
        cam.keyframe_insert("rotation_euler", frame=1 + round(t * (last - 1)))
    # Blender 4.4+ moved f-curves behind slotted actions, so `action.fcurves`
    # is gone in 5.0. Bezier is already the default interpolation, so this is
    # a nicety — walk the new layout when it exists and never fail on it.
    try:
        act = cam.animation_data.action
        curves = getattr(act, "fcurves", None)
        if curves is None:
            curves = [
                fc
                for layer in act.layers
                for strip in layer.strips
                for bag in strip.channelbags
                for fc in bag.fcurves
            ]
        for fc in curves:
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"
    except Exception as exc:  # cosmetic only — never block a render
        print(f"NOTE keyframe interpolation skipped: {exc}")


# -------------------------------------------------------------------- scenes

def scene_street_crane(last):
    """The 2026-07-25 failure case: wet street, walking figure, crane up.

    A text-prompted crane-up turned the street into a rotated rooftop. Here the
    crane is real geometry, so the buildings cannot move.
    """
    reset()
    box("road", (0, 12, -0.05), (5, 24, 0.05))
    for i in range(6):                       # buildings both sides
        y = i * 8
        box(f"bldg_L{i}", (-7.5, y, 5 + (i % 3)), (2.5, 3.5, 5 + (i % 3)))
        box(f"bldg_R{i}", (7.5, y, 6 - (i % 2)), (2.5, 3.5, 6 - (i % 2)))
    for i in range(5):                       # street furniture = mid-depth cues
        box(f"pole{i}", (-4.6, i * 9 + 3, 2), (0.12, 0.12, 2))
    fig = cyl("figure", (-1.2, 9, 0.9), 0.35, 1.8)   # the subject
    fig.scale = (1, 0.6, 1)
    cam = camera((0, -6, 1.6), (math.radians(88), 0, 0))
    key_camera(cam, [
        (0.0, (0, -6, 1.6), (math.radians(88), 0, 0)),
        (1.0, (0, -6, 9.0), (math.radians(66), 0, 0)),   # crane up + tilt down
    ], last)
    return cam, 2.0, 48.0


def scene_room_dolly(last):
    """Interior, dolly in past a foreground table toward a window.

    KNOWN-BAD (2026-07-27): the move ends staring at a featureless wall, so the
    last frames collapse to ~5 grey levels. Kept deliberately as the negative
    fixture for the acceptance guard — it must keep tripping FLAT/DRIFT.
    """
    reset()
    box("floor",   (0, 4, 0),   (4, 6, 0.05))
    box("ceiling", (0, 4, 3.0), (4, 6, 0.05))
    box("wall_L",  (-4, 4, 1.5), (0.05, 6, 1.5))
    box("wall_R",  (4, 4, 1.5),  (0.05, 6, 1.5))
    box("wall_far", (0, 10, 1.5), (4, 0.05, 1.5))
    box("window_frame", (0, 9.9, 1.7), (1.2, 0.06, 0.9))
    box("table_top", (0, 2.2, 0.75), (1.1, 0.6, 0.05))
    for sx, sy in ((-1.0, -0.5), (1.0, -0.5), (-1.0, 0.5), (1.0, 0.5)):
        box(f"leg_{sx}_{sy}", (sx, 2.2 + sy, 0.37), (0.05, 0.05, 0.37))
    cyl("vase", (0.3, 2.2, 0.95), 0.12, 0.4)
    cam = camera((0, -1.5, 1.5), (math.radians(90), 0, 0))
    key_camera(cam, [
        (0.0, (0, -1.5, 1.5), (math.radians(90), 0, 0)),
        (1.0, (0,  3.6, 1.5), (math.radians(90), 0, 0)),
    ], last)
    return cam, 0.5, 13.0


def scene_horizon_orbit(last):
    """Foreground subject, distant ridge, camera orbits the subject.

    Large depth range plus real parallax between foreground and background —
    precisely what a single still cannot fake. The ridge only spans one side,
    so late frames lose it (VOID advisory fires); see docs/GUIDED-GENERATION.md.
    """
    reset()
    box("ground", (0, 0, -0.05), (60, 60, 0.05))
    cyl("subject", (0, 0, 1.0), 0.5, 2.0)
    box("rock_a", (2.5, 1.5, 0.4), (0.8, 0.6, 0.4))
    box("rock_b", (-3.0, 2.2, 0.3), (0.6, 0.9, 0.3))
    for i in range(7):
        x = -30 + i * 10
        box(f"ridge{i}", (x, 45, 4 + (i % 4) * 2), (6, 3, 4 + (i % 4) * 2))
    r, h = 8.0, 2.2
    keys = []
    for idx, t in enumerate((0.0, 0.5, 1.0)):
        a = math.radians(-90 + idx * 55)
        keys.append((
            t,
            (r * math.cos(a), r * math.sin(a), h),
            (math.radians(84), 0, math.radians(90) + a),
        ))
    cam = camera(keys[0][1], keys[0][2])
    key_camera(cam, keys, last)
    return cam, 3.0, 70.0


SCENES = {
    "street_crane": scene_street_crane,
    "room_dolly": scene_room_dolly,
    "horizon_orbit": scene_horizon_orbit,
}


# ------------------------------------------------------- declarative specs

def scene_from_spec(spec, last):
    """Build a scene by INTERPRETING a validated spec — never by exec'ing it.

    Every value here has already passed `blockout_spec.validate_spec`, so it is
    a finite, range-checked number of a known type. Object names are generated
    rather than taken from the spec, so a model-authored string never reaches a
    name lookup. This is the whole security argument: model output is data that
    indexes into primitives we wrote, and at no point becomes code.
    """
    reset()
    for i, o in enumerate(spec["objects"]):
        loc = tuple(float(c) for c in o["loc"])
        if o["type"] == "box":
            box(f"obj_{i}", loc, tuple(float(c) for c in o["scale"]))
        else:
            cyl(f"obj_{i}", loc, float(o["radius"]), float(o["depth"]))
    keys = [
        (float(k["t"]),
         tuple(float(c) for c in k["loc"]),
         tuple(math.radians(float(c)) for c in k["rot_deg"]))
        for k in spec["camera"]
    ]
    cam = camera(keys[0][1], keys[0][2])
    key_camera(cam, keys, last)
    return cam, float(spec["near"]), float(spec["far"])


# ----------------------------------------------------------------------- run

def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    name, outdir = argv[0], argv[1]

    def opt(flag, default):
        return int(argv[argv.index(flag) + 1]) if flag in argv else default

    frames = opt("--frames", 49)
    width = opt("--width", 1024)
    height = opt("--height", 576)
    sample = "--sample" in argv

    if width % 32 or height % 32:
        print(f"WARN {width}x{height} is off the 32-grid — I2V latents reject it")
    if (frames - 1) % 4:
        print(f"WARN {frames} frames is not 4n+1 — Wan 2.2 latents reject it")

    if "--spec" in argv:
        import importlib.util
        import json
        import os
        # Load the SDK module BY FILE PATH, not as `emptyos.sdk.blockout_spec`.
        # The package `__init__` imports BaseApp and pulls in the whole web
        # stack, which Blender's bundled Python does not have — going through
        # the package raises `ModuleNotFoundError: starlette` before any of this
        # module's own stdlib-only code runs. The module itself is deliberately
        # dependency-free; the package around it is not.
        _sdk = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "emptyos", "sdk", "blockout_spec.py",
        )
        _s = importlib.util.spec_from_file_location("eos_blockout_spec", _sdk)
        _m = importlib.util.module_from_spec(_s)
        _s.loader.exec_module(_m)
        validate_spec = _m.validate_spec

        spec_path = argv[argv.index("--spec") + 1]
        with open(spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        errs = validate_spec(spec)
        if errs:
            # Refuse rather than render something the spec did not describe.
            for e in errs:
                print(f"SPEC-REJECT {e}")
            raise SystemExit(2)
        name = str(spec.get("name", "spec"))
        cam, near, far = scene_from_spec(spec, frames)
    else:
        cam, near, far = SCENES[name](frames)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = width, height
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "BW"
    sc.render.film_transparent = False
    try:
        sc.eevee.taa_render_samples = 1
    except Exception:
        pass
    # Raw view transform: a filmic/AgX curve would bend the depth ramp.
    for vt in ("Raw", "Standard"):
        try:
            sc.view_settings.view_transform = vt
            break
        except Exception:
            continue
    sc.view_settings.look = "None"

    sc.view_layers[0].material_override = depth_material(near, far)

    todo = (1, 1 + (frames - 1) // 2, frames) if sample else range(1, frames + 1)
    for f in todo:
        sc.frame_set(f)
        sc.render.filepath = (
            f"{outdir}/{name}_f{f:02d}.png" if sample
            else f"{outdir}/frame_{f:04d}.png"
        )
        bpy.ops.render.render(write_still=True)
    print(f"OK {name} frames={3 if sample else frames} "
          f"size={width}x{height} near={near} far={far} dir={outdir}")


main()
