"""Table props turned on a lathe, so Flow copies a cup rather than a tin can.

Round 2 made the blockout credible enough that Flow stopped reinterpreting
the crude props and copied them: a handleless cylinder came back as a
handleless cup, the glass as a solid blue cylinder, the vase as a tube.
In round 1 the same primitives came back as a mug with a handle, a plate of
food and a proper glass — the looser the blockout, the more Flow fills in.
So a prop now has to be the shape it should be.

Each profile is (radius, height) points in metres, bottom to top, outside
then inside, spun 360 degrees about z. Names keep the prop_* prefix.
"""
import math
import bmesh
import bpy


def lathe(name, profile, loc, mat, steps=48):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    vs = [bm.verts.new((r, 0.0, z)) for r, z in profile]
    es = [bm.edges.new((a, b)) for a, b in zip(vs, vs[1:])]
    bmesh.ops.spin(bm, geom=vs + es, cent=(0, 0, 0), axis=(0, 0, 1),
                   angle=math.tau, steps=steps, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(o)
    o.location = loc
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(mat)
    return o


CUP = [(0, 0), (0.028, 0), (0.033, 0.004), (0.039, 0.035), (0.041, 0.066),
       (0.038, 0.066), (0.036, 0.036), (0.032, 0.010), (0, 0.010)]
SAUCER = [(0, 0), (0.035, 0), (0.060, 0.006), (0.070, 0.012), (0.068, 0.014),
          (0.058, 0.009), (0.034, 0.004), (0, 0.004)]
PLATE = [(0, 0), (0.050, 0), (0.074, 0.010), (0.078, 0.015), (0.075, 0.016),
         (0.068, 0.011), (0.046, 0.004), (0, 0.004)]
TUMBLER = [(0, 0), (0.029, 0), (0.034, 0.110), (0.032, 0.110), (0.027, 0.008), (0, 0.008)]
WATER = [(0, 0.008), (0.028, 0.008), (0.031, 0.070), (0, 0.070)]
BUD_VASE = [(0, 0), (0.021, 0), (0.028, 0.025), (0.026, 0.055), (0.014, 0.085),
            (0.010, 0.100), (0.012, 0.110), (0.008, 0.110), (0.006, 0.090), (0, 0.090)]
SHAKER = [(0, 0), (0.017, 0), (0.017, 0.055), (0.014, 0.066), (0.007, 0.071), (0, 0.072)]


def cup_with_handle(name, x, y, z, mat, handle_dir=(1, 0)):
    lathe(name, CUP, (x, y, z), mat)
    # handle: a torus stood on edge, half buried in the wall
    hx, hy = handle_dir
    bpy.ops.mesh.primitive_torus_add(major_radius=0.019, minor_radius=0.0045,
                                     major_segments=32, minor_segments=12,
                                     location=(x + hx * 0.046, y + hy * 0.046, z + 0.036))
    h = bpy.context.object
    h.name = name + "_handle"
    h.rotation_euler = (math.pi / 2, 0, math.atan2(hy, hx))
    h.scale = (0.8, 1.0, 1.0)
    bpy.ops.object.shade_smooth()
    h.data.materials.append(mat)


def build_table(tcx, T, M, glass_mat):
    """The hero table's props, same places as the old primitives."""
    lathe("prop_glass", TUMBLER, (tcx - 0.05, -0.42, T), glass_mat)
    lathe("prop_water", WATER, (tcx - 0.05, -0.42, T), glass_mat)
    lathe("prop_dish", PLATE, (tcx - 0.14, -0.29, T), M["ceramic"])
    lathe("prop_dish2", PLATE, (tcx + 0.04, -0.30, T), M["ceramic"])
    lathe("prop_saucer", SAUCER, (tcx + 0.29, -0.36, T), M["ceramic"])
    cup_with_handle("prop_cup", tcx + 0.29, -0.36, T + 0.004, M["ceramic"], handle_dir=(0.7, -0.7))
    lathe("prop_vase", BUD_VASE, (tcx + 0.12, -0.12, T), M["ceramic"])
    lathe("prop_salt", SHAKER, (tcx + 0.19, -0.10, T), M["steel"])
    lathe("prop_pepper", SHAKER, (tcx + 0.23, -0.13, T), M["steel"])
