"""Articulated 3D object SDK — primitives, parts, joints, URDF export.

Governing standard: none — CAD geometry + URDF robot-description tooling (URDF
is a description format, not an engineering standard).

Run inside the user-home Python 3.12 venv. The EmptyOS daemon (3.13) reaches
this package via subprocess from `plugins/cadquery/` — never imports it
directly.

Public API:

    from engines.articulated import (
        Box, Cylinder, Sphere,
        Origin, Material,
        Part, ArticulatedObject,
        Revolute, Fixed,
        export_urdf, to_urdf_xml,
        TestContext,
    )

See README.md for setup + smoke-test instructions.
"""

from .compile import (
    CompileReport,
    SceneCompileReport,
    compile_scene_source,
    compile_source,
    format_scene_signals,
    format_signals,
)
from .gltf_export import export_glb
from .materials import TEXTURE_LIBRARY, get_material, material_names
from .scene_export import export_scene
from .testing import Finding, TestContext
from .types import (
    SCENE_OBJECT_CAP,
    ArticulatedObject,
    Box,
    Cylinder,
    Fixed,
    Geometry,
    Joint,
    JointType,
    Material,
    Mesh,
    Origin,
    Part,
    PbrMaterial,
    Placement,
    Revolute,
    Scene,
    Sphere,
    TextureRef,
    Vec3,
)
from .urdf_export import export_urdf, to_urdf_xml

__all__ = [
    # primitives
    "Box", "Cylinder", "Sphere", "Mesh", "Geometry",
    # composition (single object)
    "Part", "ArticulatedObject", "Origin", "Material", "Vec3",
    # PBR materials (Tier 3)
    "PbrMaterial", "TextureRef", "TEXTURE_LIBRARY", "get_material", "material_names",
    # joints
    "Joint", "JointType", "Revolute", "Fixed",
    # scenes (multi-object)
    "Scene", "Placement", "SCENE_OBJECT_CAP",
    # export
    "export_urdf", "to_urdf_xml", "export_scene", "export_glb",
    # testing
    "TestContext", "Finding",
    # compile
    "compile_source", "CompileReport", "format_signals",
    "compile_scene_source", "SceneCompileReport", "format_scene_signals",
]
