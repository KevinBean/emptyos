"""A posable seated mannequin for the blockout — the pose control Flow was missing.

The first stand-in was a ball on stacked boxes. Flow put the man where it sat
but invented his posture per angle, because boxes carry occupancy and nothing
about where an elbow or a hand is. This builds a jointed figure by forward
kinematics from a named pose, so ONE pose in world space renders from every
camera and the gesture is the same gesture in every shot.

Pose data is angles, not positions: each limb is (azimuth, elevation) in
degrees in the body's frame. Azimuth 0 = the way he faces, +90 = his left;
elevation +90 = straight up, -90 = straight down. Changing a pose is editing
numbers here, never moving meshes by hand.

Pure bpy + mathutils; every object is named prx_* so a set script's per-camera HIDE
rules (e.g. an empty-seat or exterior camera) can remove him by prefix.
"""
import math
import bpy
from mathutils import Vector

# Adult male, ~1.76 m standing. Seated proportions only.
SEG = {
    "spine": 0.52,      # pelvis centre -> neck base
    "neck": 0.10,
    "head_up": 0.11,    # neck top -> head centre
    "upper_arm": 0.29,
    "forearm": 0.26,
    "hand": 0.17,
    "thigh": 0.45,
    "shin": 0.45,
    "foot": 0.24,
}
SHOULDER_HALF = 0.19    # shoulder joint offset from the spine axis
HIP_HALF = 0.10
R = {"upper_arm": 0.047, "forearm": 0.038, "hand": 0.030,
     "thigh": 0.075, "shin": 0.052, "foot": 0.040, "neck": 0.050}

POSES = {
    # The master (C1-M) and cover gesture: seated at the aisle end of his
    # banquette, leaning in toward the table, one forearm resting on it and the
    # other hand half raised as if about to speak. Head lands on (0.64, -0.80,
    # 1.18), where the pinhole solve of the master put it.
    #   near side to camera A = his right (-y); that forearm is on the table,
    #   the far (left) hand is the raised one, as in the first proxy.
    "lean_in_hand_raised": {
        "pelvis": (0.24, -0.80, 0.55),
        "facing": 0.0,                  # degrees about z; 0 = facing +x (the table)
        "spine": (0, 55),               # 35 deg forward lean (60 left the head at x 0.58)
        "neck": (0, 45),
        "head_pitch": -12,              # chin slightly down, toward the table
        "r_upper_arm": (-5, -48),
        "r_forearm": (-18, 6),          # along the table top, angled inward
        "r_hand": (-18, 0),
        "l_upper_arm": (8, -62),
        "l_forearm": (-8, 38),          # half raised
        "l_hand": (-5, 48),
        "r_thigh": (-4, -6), "r_shin": (0, -80), "r_foot": (0, -8),
        "l_thigh": (4, -6),  "l_shin": (0, -80), "l_foot": (0, -8),
    },
    # His double on the OPPOSITE seat (S19/S21): the same man, facing him across
    # the table, sitting upright and still, both forearms resting on the table.
    # Mirror of his seat position: the opposite banquette's back is at x 2.09.
    "double_opposite": {
        "pelvis": (1.85, -0.80, 0.55),
        "facing": 180.0,                # facing -x, toward him
        "spine": (0, 74),
        "neck": (0, 70),
        "head_pitch": -8,
        "r_upper_arm": (-8, -66), "r_forearm": (18, 2), "r_hand": (12, -4),
        "l_upper_arm": (8, -66),  "l_forearm": (-18, 2), "l_hand": (-12, -4),
        "r_thigh": (-4, -6), "r_shin": (0, -80), "r_foot": (0, -8),
        "l_thigh": (4, -6),  "l_shin": (0, -80), "l_foot": (0, -8),
    },
}


def _dir(az, el, facing):
    """Unit vector for body-frame (azimuth, elevation), rotated by `facing`."""
    a, e = math.radians(az + facing), math.radians(el)
    return Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))


def _left(facing):
    a = math.radians(facing + 90)
    return Vector((math.cos(a), math.sin(a), 0.0))


def _sphere(name, loc, scale, mat, rot_to=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = scale
    if rot_to is not None:
        o.rotation_mode = 'QUATERNION'
        o.rotation_quaternion = rot_to.to_track_quat('Z', 'X')
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat)
    return o


def _capsule(name, a, b, r, mat):
    """Cylinder from a to b with a sphere at b (the joint), r = radius."""
    v = b - a
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=r, depth=v.length,
                                        location=(a + b) / 2)
    c = bpy.context.object
    c.name = name
    c.rotation_mode = 'QUATERNION'
    c.rotation_quaternion = v.to_track_quat('Z', 'Y')
    bpy.ops.object.shade_smooth()
    c.data.materials.append(mat)
    _sphere(name + "_j", b, (r, r, r), mat)
    return b


def joints(pose):
    """Forward kinematics only — world positions of every joint, no Blender."""
    P = POSES[pose] if isinstance(pose, str) else pose
    f = P["facing"]
    J = {"pelvis": Vector(P["pelvis"])}
    sp = _dir(*P["spine"], f)
    J["neck_base"] = J["pelvis"] + sp * SEG["spine"]
    J["neck_top"] = J["neck_base"] + _dir(*P["neck"], f) * SEG["neck"]
    J["head"] = J["neck_top"] + _dir(P["neck"][0], 90 + P["head_pitch"], f) * SEG["head_up"]
    left = _left(f)
    sh_c = J["neck_base"] - sp * 0.05
    hip_c = J["pelvis"] - Vector((0, 0, 0.03))
    for side, s in (("l", +1), ("r", -1)):
        J[f"{side}_shoulder"] = sh_c + left * (s * SHOULDER_HALF)
        J[f"{side}_elbow"] = J[f"{side}_shoulder"] + _dir(*P[f"{side}_upper_arm"], f) * SEG["upper_arm"]
        J[f"{side}_wrist"] = J[f"{side}_elbow"] + _dir(*P[f"{side}_forearm"], f) * SEG["forearm"]
        J[f"{side}_fingertip"] = J[f"{side}_wrist"] + _dir(*P[f"{side}_hand"], f) * SEG["hand"]
        J[f"{side}_hip"] = hip_c + left * (s * HIP_HALF)
        J[f"{side}_knee"] = J[f"{side}_hip"] + _dir(*P[f"{side}_thigh"], f) * SEG["thigh"]
        J[f"{side}_ankle"] = J[f"{side}_knee"] + _dir(*P[f"{side}_shin"], f) * SEG["shin"]
        J[f"{side}_toe"] = J[f"{side}_ankle"] + _dir(*P[f"{side}_foot"], f) * SEG["foot"]
    return J


def build(pose, mat, prefix="prx_"):
    """Build the figure in the current scene; returns the joint dict."""
    J = joints(pose)
    P = POSES[pose] if isinstance(pose, str) else pose
    sp = (J["neck_base"] - J["pelvis"]).normalized()
    # torso: pelvis block + chest, both ellipsoids aligned to the spine
    _sphere(prefix + "pelvis", J["pelvis"], (0.15, 0.17, 0.12), mat, rot_to=sp)
    chest = J["pelvis"] + sp * (SEG["spine"] * 0.55)
    _sphere(prefix + "torso", chest, (0.13, 0.21, 0.27), mat, rot_to=sp)
    _capsule(prefix + "neck", J["neck_base"], J["neck_top"], R["neck"], mat)
    # head: taller than wide, deeper than wide, tilted with the pitch
    hd = _dir(P["neck"][0], 90 + P["head_pitch"], P["facing"])
    _sphere(prefix + "head", J["head"], (0.085, 0.078, 0.115), mat, rot_to=hd)
    for side in ("l", "r"):
        _sphere(f"{prefix}{side}_shoulder", J[f"{side}_shoulder"], (0.06, 0.06, 0.06), mat)
        _capsule(f"{prefix}{side}_uarm", J[f"{side}_shoulder"], J[f"{side}_elbow"], R["upper_arm"], mat)
        _capsule(f"{prefix}{side}_farm", J[f"{side}_elbow"], J[f"{side}_wrist"], R["forearm"], mat)
        _capsule(f"{prefix}{side}_hand", J[f"{side}_wrist"], J[f"{side}_fingertip"], R["hand"], mat)
        _capsule(f"{prefix}{side}_thigh", J[f"{side}_hip"], J[f"{side}_knee"], R["thigh"], mat)
        _capsule(f"{prefix}{side}_shin", J[f"{side}_knee"], J[f"{side}_ankle"], R["shin"], mat)
        _capsule(f"{prefix}{side}_foot", J[f"{side}_ankle"], J[f"{side}_toe"], R["foot"], mat)
    return J
