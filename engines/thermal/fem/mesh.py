"""2D mesh generation for direct-buried trefoil cable groups (MVP).

Geometry: 3 single-core cables in trefoil-touching arrangement, buried at
a fixed depth in homogeneous soil. A large rectangular soil domain is
meshed with gmsh; concentric cable layers (conductor / insulation /
sheath / oversheath) are cut as discs and classified by element-centroid
distance from each cable centre after the mesh is generated.

All coordinates in millimetres internally. y = 0 is the soil surface;
positive depths in the input map to negative gmsh-y.

This is a deliberately thin port of cable-current-rating/src/fem/mesh.py.
Screen, duct, HDD, joint, multi-circuit, asphalt, backfill zones are
omitted — they re-enter as later validation gates demand them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import gmsh
import numpy as np

# Material IDs (per element) — only the layers MVP needs.
MAT_CONDUCTOR = 1
MAT_INSULATION = 2
MAT_SHEATH = 3
MAT_OVERSHEATH = 4
MAT_SOIL = 9

MATERIAL_NAMES = {
    MAT_CONDUCTOR: "conductor",
    MAT_INSULATION: "insulation",
    MAT_SHEATH: "sheath",
    MAT_OVERSHEATH: "oversheath",
    MAT_SOIL: "soil",
}


@dataclass
class CableLayers:
    """Concentric layer outer radii for one cable, mm."""
    r_conductor: float
    r_insulation: float
    r_sheath: float
    r_oversheath: float


@dataclass
class CableZone:
    """Element indices belonging to one cable's layers."""
    cable_id: str
    conductor_elems: np.ndarray
    insulation_elems: np.ndarray
    sheath_elems: np.ndarray
    oversheath_elems: np.ndarray


@dataclass
class FEMMesh:
    nodes: np.ndarray          # (N, 2) coordinates in mm
    elements: np.ndarray       # (M, 3) triangle node indices
    material_tags: np.ndarray  # (M,) element material ID
    cable_zones: list[CableZone]
    boundary_nodes: np.ndarray  # node indices on Dirichlet boundary
    n_nodes: int
    n_elements: int


def trefoil_centres_mm(burial_depth_m: float, cable_outer_diameter_m: float
                        ) -> list[tuple[float, float]]:
    """Centres of 3 cables in trefoil-touching, two-bottom-one-top arrangement.

    Returns gmsh-coords (y < 0 for buried). Depth is measured to the
    centroid of the trefoil triangle.
    """
    L_mm = burial_depth_m * 1000.0
    d_e_mm = cable_outer_diameter_m * 1000.0
    # Equilateral triangle, side = d_e. Centroid at (0, -L). Two cables on
    # bottom (deeper), one on top (shallower).
    half_side = d_e_mm / 2.0
    half_height = d_e_mm * math.sqrt(3) / 4.0  # half the centroid-to-vertex y-distance
    return [
        (-half_side, -L_mm - half_height),  # bottom-left
        (+half_side, -L_mm - half_height),  # bottom-right
        (0.0,        -L_mm + 2 * half_height),  # top
    ]


def create_trefoil_mesh(
    layers: CableLayers,
    cable_centres_mm: list[tuple[float, float]],
    domain_width_mm: float = 40000.0,
    domain_depth_mm: float = 20000.0,
    mesh_size_cable: float = 1.0,
    mesh_size_far: float = 600.0,
    isothermal_all_boundaries: bool = True,
) -> FEMMesh:
    """Build a 2D mesh for a trefoil cable group in homogeneous soil.

    Args:
        layers: outer radii of the four cable layers in mm.
        cable_centres_mm: list of (x, y) cable centres in gmsh coords (y<0 below surface).
        domain_width_mm / domain_depth_mm: soil rectangle size.
        mesh_size_cable: target mesh size near cables (mm).
        mesh_size_far: target mesh size at far field (mm).
        isothermal_all_boundaries: when True, Dirichlet BC on top + sides + bottom
            (semi-infinite-soil approximation). When False, only top.
    """
    half_w = domain_width_mm / 2.0
    cable_radii: list[tuple[str, float]] = [
        ("conductor", layers.r_conductor),
        ("insulation", layers.r_insulation),
        ("sheath", layers.r_sheath),
        ("oversheath", layers.r_oversheath),
    ]

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("trefoil_thermal")

    all_dim_tags: list[tuple[int, int]] = []

    for cx, cy in cable_centres_mm:
        for _, r in cable_radii:
            t = gmsh.model.occ.addDisk(cx, cy, 0, r, r)
            all_dim_tags.append((2, t))

    domain_tag = gmsh.model.occ.addRectangle(-half_w, -domain_depth_mm, 0,
                                              domain_width_mm, domain_depth_mm)
    all_dim_tags.append((2, domain_tag))

    gmsh.model.occ.synchronize()
    gmsh.model.occ.fragment([all_dim_tags[0]], all_dim_tags[1:],
                             removeObject=True, removeTool=True)
    gmsh.model.occ.synchronize()

    all_surfaces = gmsh.model.getEntities(dim=2)
    pg = gmsh.model.addPhysicalGroup(2, [tag for _, tag in all_surfaces], 999)
    gmsh.model.setPhysicalName(2, pg, "all")

    _setup_mesh_fields(cable_centres_mm, mesh_size_cable, mesh_size_far,
                       layers.r_oversheath)

    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay
    gmsh.model.mesh.generate(2)

    mesh = _extract_and_classify(
        cable_centres_mm, cable_radii, layers,
        isothermal_all_boundaries=isothermal_all_boundaries,
    )

    gmsh.finalize()
    return mesh


def _setup_mesh_fields(centres, size_cable, size_far, r_outermost):
    field_ids = []
    for cx, cy in centres:
        dist_field = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(
            dist_field, "F",
            f"Sqrt(({_x_expr(cx)})^2 + ({_y_expr(cy)})^2)"
        )
        thresh = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(thresh, "InField", dist_field)
        gmsh.model.mesh.field.setNumber(thresh, "SizeMin", size_cable)
        gmsh.model.mesh.field.setNumber(thresh, "SizeMax", size_far)
        gmsh.model.mesh.field.setNumber(thresh, "DistMin", r_outermost)
        gmsh.model.mesh.field.setNumber(thresh, "DistMax", r_outermost * 15)
        field_ids.append(thresh)

    if field_ids:
        min_field = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(min_field, "FieldsList", field_ids)
        gmsh.model.mesh.field.setAsBackgroundMesh(min_field)

    gmsh.option.setNumber("Mesh.MeshSizeMax", size_far)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size_cable * 0.3)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)


def _x_expr(v: float) -> str:
    if v == 0:
        return "x"
    return f"x-{v}" if v > 0 else f"x+{-v}"


def _y_expr(v: float) -> str:
    if v == 0:
        return "y"
    return f"y-{v}" if v > 0 else f"y+{-v}"


def _extract_and_classify(
    centres: list[tuple[float, float]],
    cable_radii: list[tuple[str, float]],
    layers: CableLayers,
    isothermal_all_boundaries: bool,
) -> FEMMesh:
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    n_nodes = len(node_tags)
    coords = np.array(node_coords, dtype=np.float64).reshape(-1, 3)[:, :2]

    max_tag = int(max(node_tags))
    tag_to_idx = np.zeros(max_tag + 1, dtype=np.int32)
    for i, t in enumerate(node_tags):
        tag_to_idx[int(t)] = i

    elements_list: list[list[int]] = []
    for _, surf_tag in gmsh.model.getEntities(dim=2):
        et, _ets, ents = gmsh.model.mesh.getElements(2, surf_tag)
        for etype, _, enode_arr in zip(et, _ets, ents):
            if etype != 2:  # only triangles
                continue
            n_el = len(enode_arr) // 3
            enodes = np.array(enode_arr, dtype=np.int64).reshape(n_el, 3)
            for row in enodes:
                elements_list.append([
                    tag_to_idx[int(row[0])],
                    tag_to_idx[int(row[1])],
                    tag_to_idx[int(row[2])],
                ])

    elements = np.array(elements_list, dtype=np.int32)
    n_elements = len(elements)

    cx_arr = (coords[elements[:, 0], 0] + coords[elements[:, 1], 0] + coords[elements[:, 2], 0]) / 3.0
    cy_arr = (coords[elements[:, 0], 1] + coords[elements[:, 1], 1] + coords[elements[:, 2], 1]) / 3.0

    material_tags = np.full(n_elements, MAT_SOIL, dtype=np.int32)

    zone_mat = {
        "conductor": MAT_CONDUCTOR,
        "insulation": MAT_INSULATION,
        "sheath": MAT_SHEATH,
        "oversheath": MAT_OVERSHEATH,
    }
    outermost_r = cable_radii[-1][1]

    for ccx, ccy in centres:
        r = np.sqrt((cx_arr - ccx) ** 2 + (cy_arr - ccy) ** 2)
        near_mask = r <= outermost_r + 1.0
        if not np.any(near_mask):
            continue
        prev_r = 0.0
        for zone_name, zone_r in cable_radii:
            zone_mask = near_mask & (r >= prev_r - 0.1) & (r < zone_r + 0.1)
            material_tags[zone_mask] = zone_mat[zone_name]
            prev_r = zone_r

    boundary_parts = [np.where(np.abs(coords[:, 1]) < 0.5)[0]]
    if isothermal_all_boundaries:
        x_min = coords[:, 0].min()
        x_max = coords[:, 0].max()
        y_min = coords[:, 1].min()
        boundary_parts.append(np.where(np.abs(coords[:, 1] - y_min) < 0.5)[0])
        boundary_parts.append(np.where(np.abs(coords[:, 0] - x_min) < 0.5)[0])
        boundary_parts.append(np.where(np.abs(coords[:, 0] - x_max) < 0.5)[0])
    boundary_nodes = np.unique(np.concatenate(boundary_parts))

    cable_zones: list[CableZone] = []
    for i, (ccx, ccy) in enumerate(centres):
        r = np.sqrt((cx_arr - ccx) ** 2 + (cy_arr - ccy) ** 2)
        cond = (material_tags == MAT_CONDUCTOR) & (r < layers.r_conductor + 0.5)
        ins = (material_tags == MAT_INSULATION) & (r < layers.r_insulation + 0.5)
        sh = (material_tags == MAT_SHEATH) & (r < layers.r_sheath + 0.5)
        os_ = (material_tags == MAT_OVERSHEATH) & (r < layers.r_oversheath + 0.5)
        cable_zones.append(CableZone(
            cable_id=f"c{i}",
            conductor_elems=np.where(cond)[0],
            insulation_elems=np.where(ins)[0],
            sheath_elems=np.where(sh)[0],
            oversheath_elems=np.where(os_)[0],
        ))

    return FEMMesh(
        nodes=coords,
        elements=elements,
        material_tags=material_tags,
        cable_zones=cable_zones,
        boundary_nodes=boundary_nodes,
        n_nodes=n_nodes,
        n_elements=n_elements,
    )
