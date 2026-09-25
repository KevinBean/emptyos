"""Name the object behind a pixel, per camera — diagnose before fixing.

Run after the set script has built the scene, in the same Blender session:

    blender --background --python booth.py --python probe.py

Edit PROBES: camera name -> list of (u, v), u left->right, v top->bottom,
both 0..1. Cameras are created from the set script's CAMERAS dict (its
render loop normally creates them, and it has not run).

ray_cast ignores hide_render, so objects a camera's HIDE rule removes are
hidden in the viewport here first — otherwise you probe the stand-in you
meant to hide.
"""
import importlib.util
import os

import bpy

SET_SCRIPT = os.path.join(os.getcwd(), "booth.py")
PROBES = {"A-master": [(0.5, 0.5)]}

spec = importlib.util.spec_from_file_location("setscript", SET_SCRIPT)
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)
for n, (loc, tgt, lens) in bk.CAMERAS.items():
    bk.camera(n, loc, tgt, lens)

sc = bpy.context.scene
for cam_name, pts in PROBES.items():
    hidden = getattr(bk, "HIDE", {}).get(cam_name, ())
    for ob in bpy.data.objects:
        ob.hide_viewport = any(ob.name.startswith(p) for p in hidden)
    dg = bpy.context.evaluated_depsgraph_get()
    cam = bpy.data.objects[cam_name]
    tr, br, bl, tl = [cam.matrix_world @ v for v in cam.data.view_frame(scene=sc)]
    o = cam.matrix_world.translation
    for (u, v) in pts:
        p = tl.lerp(tr, u).lerp(bl.lerp(br, u), v)
        hit, loc, _n, _i, ob, _m = sc.ray_cast(dg, o, (p - o).normalized())
        mat = ob.active_material.name if hit and ob.active_material else None
        print("PROBE", cam_name, (u, v), ob.name if hit else None, mat,
              tuple(round(c, 2) for c in loc) if hit else None)
