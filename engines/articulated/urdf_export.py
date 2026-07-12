"""ArticulatedObject -> URDF XML string + file write.

Primitives Box/Cylinder/Sphere are inline (URDF supports them natively).
Mesh geometry writes a sibling `<part_name>.stl` next to the URDF and
references it as `<mesh filename="..."/>` — STL written via
`cadquery.exporters.export` at URDF emit time using the mesh's `tolerance`.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

from .types import (
    ArticulatedObject,
    Box,
    Cylinder,
    Geometry,
    Joint,
    JointType,
    Material,
    Mesh,
    Origin,
    Part,
    Sphere,
)


def _fmt_vec3(v) -> str:
    return f"{v[0]:.6g} {v[1]:.6g} {v[2]:.6g}"


def _origin_elem(parent: Element, origin: Origin) -> None:
    SubElement(parent, "origin", {
        "xyz": _fmt_vec3(origin.xyz),
        "rpy": _fmt_vec3(origin.rpy),
    })


def _geometry_elem(
    parent: Element,
    geom: Geometry,
    *,
    part_name: str = "",
    urdf_dir: Path | None = None,
) -> None:
    g = SubElement(parent, "geometry")
    if isinstance(geom, Box):
        SubElement(g, "box", {"size": _fmt_vec3(geom.size)})
    elif isinstance(geom, Cylinder):
        SubElement(g, "cylinder", {"radius": f"{geom.radius:.6g}", "length": f"{geom.length:.6g}"})
    elif isinstance(geom, Sphere):
        SubElement(g, "sphere", {"radius": f"{geom.radius:.6g}"})
    elif isinstance(geom, Mesh):
        if not part_name or urdf_dir is None:
            raise ValueError("Mesh geometry needs part_name + urdf_dir to write the STL sibling")
        from cadquery import exporters
        stl_name = f"{part_name}.stl"
        exporters.export(
            geom.get_shape(),
            str(urdf_dir / stl_name),
            exportType="STL",
            tolerance=geom.tolerance,
            angularTolerance=0.1,
        )
        SubElement(g, "mesh", {"filename": stl_name})
    else:
        raise TypeError(f"Unsupported geometry: {type(geom).__name__}")


def _material_elem(parent: Element, mat) -> None:
    """Emit URDF `<material>` element.

    Accepts both legacy `Material(name, rgba=...)` and the Tier-3
    `PbrMaterial(name, base_color=..., metallic, roughness, texture?)`.
    URDF can't express PBR — we encode only the base color here. The
    full PBR shading lives in the parallel `.glb` (see gltf_export.py).
    """
    rgba = getattr(mat, "rgba", None) or getattr(mat, "base_color", None) or (0.7, 0.7, 0.7, 1.0)
    m = SubElement(parent, "material", {"name": mat.name})
    SubElement(m, "color", {"rgba": f"{rgba[0]:.4g} {rgba[1]:.4g} {rgba[2]:.4g} {rgba[3]:.4g}"})


def _part_to_link(part: Part, *, urdf_dir: Path | None = None) -> Element:
    link = Element("link", {"name": part.name})
    visual = SubElement(link, "visual")
    _origin_elem(visual, part.origin)
    _geometry_elem(visual, part.geometry, part_name=part.name, urdf_dir=urdf_dir)
    if part.material is not None:
        _material_elem(visual, part.material)
    # Mirror visual as collision (URDF requires it for physics; cheap for primitives).
    # For Mesh geometry the STL was already written by the visual pass — emit
    # the same `<mesh filename="..."/>` reference without re-writing the file.
    collision = SubElement(link, "collision")
    _origin_elem(collision, part.origin)
    if isinstance(part.geometry, Mesh):
        cg = SubElement(collision, "geometry")
        SubElement(cg, "mesh", {"filename": f"{part.name}.stl"})
    else:
        _geometry_elem(collision, part.geometry, part_name=part.name, urdf_dir=urdf_dir)
    return link


def _joint_to_xml(joint: Joint) -> Element:
    j = Element("joint", {"name": joint.name, "type": joint.type.value})
    _origin_elem(j, joint.origin)
    SubElement(j, "parent", {"link": joint.parent})
    SubElement(j, "child", {"link": joint.child})
    if joint.type == JointType.REVOLUTE:
        SubElement(j, "axis", {"xyz": _fmt_vec3(joint.axis)})
        # URDF revolute requires limits; default to ±pi if unspecified
        import math
        lower = joint.lower if joint.lower is not None else -math.pi
        upper = joint.upper if joint.upper is not None else math.pi
        SubElement(j, "limit", {
            "lower": f"{lower:.6g}",
            "upper": f"{upper:.6g}",
            "effort": f"{joint.effort:.6g}",
            "velocity": f"{joint.velocity:.6g}",
        })
    return j


def to_urdf_xml(model: ArticulatedObject, *, urdf_dir: Path | None = None) -> str:
    """Render an ArticulatedObject to a URDF XML string (pretty-printed).

    `urdf_dir` is required when any Part uses Mesh geometry — the directory
    where the sibling .stl files get written. Primitive-only models don't
    need it; the parameter is accepted but ignored.
    """
    # Validate single-root invariant before write
    _ = model.root_part()

    robot = Element("robot", {"name": model.name})
    # Top-level materials (URDF allows <material> at robot level with name only used by links)
    for mat in model.materials:
        _material_elem(robot, mat)
    for part in model.parts:
        robot.append(_part_to_link(part, urdf_dir=urdf_dir))
    for joint in model.joints:
        robot.append(_joint_to_xml(joint))

    raw = tostring(robot, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ")


def export_urdf(model: ArticulatedObject, path: str | Path) -> Path:
    """Write URDF XML to `path` and return the resolved Path.

    For Mesh geometry, sibling `<part_name>.stl` files are written next to
    the URDF — URDF's `<mesh filename="..."/>` uses relative paths resolved
    by the URDF parser against the URDF's location.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_urdf_xml(model, urdf_dir=p.parent), encoding="utf-8")
    return p.resolve()
