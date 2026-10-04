"""Mesh primitive — URDF emits `<mesh filename="..."/>` + writes sibling .stl,
glb embeds tessellated triangles. Verifies the CadQuery integration end-to-end.

Run via:
    "%LOCALAPPDATA%/eos/envs/cadquery-3.12/Scripts/python.exe" -m pytest engines/articulated/tests/test_mesh.py -v
"""

from __future__ import annotations

import sys
import struct
from pathlib import Path
from xml.etree.ElementTree import fromstring

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engines.articulated import (
    ArticulatedObject,
    Cylinder,
    Fixed,
    Mesh,
    Origin,
    Part,
    PbrMaterial,
    TestContext,
    export_glb,
    export_urdf,
)


def _build_loft():
    """A simple loft between two circles — wide rim narrowing to a smaller rim."""
    import cadquery as cq
    return (
        cq.Workplane("XY")
        .circle(0.15)
        .workplane(offset=0.18)
        .circle(0.08)
        .loft(combine=True)
    )


def _build_model() -> ArticulatedObject:
    m = ArticulatedObject(name="mesh_test")
    m.add_part(Part(
        name="base",
        geometry=Cylinder(radius=0.08, length=0.02),
        origin=Origin(xyz=(0, 0, 0.01)),
    ))
    m.add_part(Part(
        name="shade",
        geometry=Mesh(build=_build_loft, tolerance=0.005),
        origin=Origin(xyz=(0, 0, 0)),
        material=PbrMaterial(name="linen"),
    ))
    m.add_joint(Fixed(name="base_to_shade", parent="base", child="shade",
                     origin=Origin(xyz=(0, 0, 0.02))))
    return m


def test_mesh_construction_validates():
    """`Mesh.build` must be callable; `tolerance` must be positive."""
    Mesh(build=_build_loft)  # OK
    Mesh(build=_build_loft, tolerance=0.001)  # OK

    with pytest.raises(ValueError, match="callable"):
        Mesh(build="not callable")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="positive"):
        Mesh(build=_build_loft, tolerance=0)

    with pytest.raises(ValueError, match="positive"):
        Mesh(build=_build_loft, tolerance=-0.01)


def test_mesh_get_shape_caches():
    """`Mesh.get_shape()` runs `build()` exactly once per instance."""
    call_count = 0

    def counted_build():
        nonlocal call_count
        call_count += 1
        import cadquery as cq
        return cq.Workplane("XY").box(0.1, 0.1, 0.1)

    mesh = Mesh(build=counted_build)
    s1 = mesh.get_shape()
    s2 = mesh.get_shape()
    s3 = mesh.get_shape()
    assert s1 is s2 is s3
    assert call_count == 1


def test_baseline_passes_with_mesh():
    """A Mesh-containing model survives baseline checks (single root, joint
    resolution, no zero geometry — Mesh is excluded from the zero check)."""
    model = _build_model()
    ctx = TestContext(model)
    ctx.run_baseline()
    assert ctx.ok, f"baseline should pass: {[(f.code, f.message) for f in ctx.findings]}"


def test_urdf_writes_mesh_filename_and_stl(tmp_path):
    """URDF emits `<mesh filename="shade.stl"/>` and the .stl file lands sibling."""
    model = _build_model()
    urdf_path = export_urdf(model, tmp_path / "out.urdf")

    text = urdf_path.read_text(encoding="utf-8")
    assert '<mesh filename="shade.stl"' in text, text
    # Cylinder primitive remains inline
    assert "<cylinder" in text

    stl_path = tmp_path / "shade.stl"
    assert stl_path.exists(), "sibling shade.stl was not written"
    assert stl_path.stat().st_size > 0

    # Round-trip parse — XML well-formed, structure intact
    root = fromstring(text)
    shade_link = root.find(".//link[@name='shade']")
    assert shade_link is not None
    mesh_elem = shade_link.find("./visual/geometry/mesh")
    assert mesh_elem is not None
    assert mesh_elem.attrib["filename"] == "shade.stl"
    # Collision must reference the same mesh — no need to write it twice on disk
    coll_mesh = shade_link.find("./collision/geometry/mesh")
    assert coll_mesh is not None
    assert coll_mesh.attrib["filename"] == "shade.stl"


def test_glb_embeds_mesh_geometry(tmp_path):
    """glb contains the lofted shade's tessellated vertices.

    Asserts the glb has a positions accessor whose count matches the
    tessellation (>> 24 for a non-trivial loft) — proves the Mesh path
    isn't silently falling back to a Box.
    """
    model = _build_model()
    glb_path = export_glb(model, tmp_path / "out.glb")
    assert glb_path.exists()
    data = glb_path.read_bytes()
    assert data[:4] == b"glTF"
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    import json as _json
    j = _json.loads(data[20:20 + chunk_len].decode("utf-8"))
    # Two meshes in the model — base (Cylinder) + shade (Mesh)
    assert len(j["meshes"]) == 2
    # Find the shade's mesh by name and check its positions accessor has
    # more vertices than a cylinder would produce (cylinder has ~128 with
    # default 32 segments — loft typically produces several hundred).
    shade_idx = [i for i, m in enumerate(j["meshes"]) if m["name"] == "shade"][0]
    pos_acc_idx = j["meshes"][shade_idx]["primitives"][0]["attributes"]["POSITION"]
    pos_count = j["accessors"][pos_acc_idx]["count"]
    assert pos_count >= 150, f"shade tessellation has only {pos_count} verts — Mesh path may not be tessellating"


def test_glb_emits_texcoord0_for_every_geometry_type(tmp_path):
    """Every primitive in the glb must carry a TEXCOORD_0 attribute pointing
    at a 2-float-per-vertex accessor — without it, Blender's importer
    samples textures at UV=(0,0) for every triangle and the mesh collapses
    to a single-pixel color. Regression locker for the muted-textures bug.
    """
    from engines.articulated import ArticulatedObject, Box, Cylinder, Sphere, Fixed, Origin, Part, PbrMaterial

    m = ArticulatedObject(name="uv_test")
    m.add_part(Part(name="box", geometry=Box(size=(0.1, 0.1, 0.1)),
                    origin=Origin(xyz=(0, 0, 0.05)), material=PbrMaterial(name="oak")))
    m.add_part(Part(name="cyl", geometry=Cylinder(radius=0.04, length=0.08),
                    origin=Origin(xyz=(0, 0, 0.04)), material=PbrMaterial(name="brass")))
    m.add_part(Part(name="sph", geometry=Sphere(radius=0.03),
                    origin=Origin(xyz=(0, 0, 0.03)), material=PbrMaterial(name="walnut")))
    m.add_part(Part(name="msh", geometry=Mesh(build=_build_loft, tolerance=0.005),
                    origin=Origin(xyz=(0, 0, 0)), material=PbrMaterial(name="linen")))
    m.add_joint(Fixed(name="j1", parent="box", child="cyl", origin=Origin(xyz=(0, 0, 0))))
    m.add_joint(Fixed(name="j2", parent="cyl", child="sph", origin=Origin(xyz=(0, 0, 0))))
    m.add_joint(Fixed(name="j3", parent="sph", child="msh", origin=Origin(xyz=(0, 0, 0))))

    glb_path = export_glb(m, tmp_path / "uv.glb")
    data = glb_path.read_bytes()
    chunk_len, _ = struct.unpack("<I4s", data[12:20])
    import json as _json
    j = _json.loads(data[20:20 + chunk_len].decode("utf-8"))

    for mesh_obj in j["meshes"]:
        attrs = mesh_obj["primitives"][0]["attributes"]
        assert "TEXCOORD_0" in attrs, f"mesh {mesh_obj['name']!r} missing TEXCOORD_0"
        uv_acc = j["accessors"][attrs["TEXCOORD_0"]]
        assert uv_acc["type"] == "VEC2"
        # UV count must match POSITION count — one UV per vertex
        pos_acc = j["accessors"][attrs["POSITION"]]
        assert uv_acc["count"] == pos_acc["count"], (
            f"mesh {mesh_obj['name']!r}: UV count {uv_acc['count']} != "
            f"position count {pos_acc['count']}"
        )
