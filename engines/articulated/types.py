"""Core dataclasses for articulated objects — primitives, parts, joints, model root.

Mirrors the Articraft shape (`sdk/_core/v0/types.py`). Primitives are Box,
Cylinder, Sphere (inline URDF tags + hand-rolled tessellation) plus Mesh
(arbitrary CadQuery shapes — STL sibling for URDF, tessellated triangles for
glb). Joint kinds Revolute + Fixed; Prismatic + Continuous land when the
first real consumer needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

Vec3 = tuple[float, float, float]


def _as_vec3(v: Sequence[float], *, name: str) -> Vec3:
    if len(v) != 3:
        raise ValueError(f"{name} must have 3 elements, got {len(v)}")
    return (float(v[0]), float(v[1]), float(v[2]))


@dataclass(frozen=True)
class Origin:
    """Position + orientation in parent frame. RPY in radians."""
    xyz: Vec3 = (0.0, 0.0, 0.0)
    rpy: Vec3 = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "xyz", _as_vec3(self.xyz, name="origin.xyz"))
        object.__setattr__(self, "rpy", _as_vec3(self.rpy, name="origin.rpy"))


@dataclass(frozen=True)
class Box:
    size: Vec3

    def __post_init__(self) -> None:
        object.__setattr__(self, "size", _as_vec3(self.size, name="Box.size"))
        if any(s <= 0 for s in self.size):
            raise ValueError(f"Box.size must be positive, got {self.size}")


@dataclass(frozen=True)
class Cylinder:
    radius: float
    length: float

    def __post_init__(self) -> None:
        if self.radius <= 0 or self.length <= 0:
            raise ValueError(f"Cylinder requires positive radius+length, got r={self.radius} l={self.length}")


@dataclass(frozen=True)
class Sphere:
    radius: float

    def __post_init__(self) -> None:
        if self.radius <= 0:
            raise ValueError(f"Sphere.radius must be positive, got {self.radius}")


@dataclass(frozen=True, eq=False)
class Mesh:
    """Arbitrary CAD geometry built by a CadQuery callable.

    `build` is a no-arg callable returning a `cadquery.Shape` or `Workplane`.
    The engine calls it once at export time and reuses the shape for both
    URDF (writes a sibling `<part_name>.stl` referenced as
    `<mesh filename="..."/>`) and glTF (embeds tessellated triangles into
    the glb buffer).

    `tolerance` is CadQuery's deflection — max linear distance between the
    true curve and its tessellated approximation. 0.001 = fine; 0.005 =
    medium (default, ~5mm at metre-scale); 0.05 = coarse.

    Equality is identity-based — two `Mesh(build=...)` with different
    callables are never `==` even if the resulting shapes match. This keeps
    hash + eq sane for a dataclass holding a function.

    Example:

        def make_lampshade():
            import cadquery as cq
            return (cq.Workplane("XY")
                    .circle(0.15).workplane(offset=0.20).circle(0.10)
                    .loft(combine=True))

        Mesh(build=make_lampshade, tolerance=0.005)
    """
    build: Callable[[], Any]
    tolerance: float = 0.005

    def __post_init__(self) -> None:
        if not callable(self.build):
            raise ValueError("Mesh.build must be a no-arg callable")
        if self.tolerance <= 0:
            raise ValueError(f"Mesh.tolerance must be positive, got {self.tolerance}")

    def get_shape(self) -> Any:
        """Run `build()` once per Mesh instance, cache the result.

        Both URDF + glTF export reach into this to avoid running the
        CadQuery operations twice. Uses `object.__setattr__` to escape
        the frozen-dataclass restriction — the cache is keyed to this
        specific Mesh instance and lives for its lifetime.
        """
        cached = getattr(self, "_shape_cache", None)
        if cached is not None:
            return cached
        shape = self.build()
        object.__setattr__(self, "_shape_cache", shape)
        return shape


Geometry = Box | Cylinder | Sphere | Mesh


@dataclass(frozen=True)
class Material:
    """RGBA in [0,1]. None = URDF default material.

    Legacy flat-color material — predates Tier 3 PBR. New code should
    prefer `PbrMaterial` (below) which supports texture refs and full
    metallic/roughness shading. `Material` continues to work; the glTF
    exporter promotes it to a procedural `PbrMaterial` with default PBR.
    """
    name: str
    rgba: tuple[float, float, float, float] = (0.7, 0.7, 0.7, 1.0)


@dataclass(frozen=True)
class TextureRef:
    """Reference a bundled texture by name (key in `materials.TEXTURE_LIBRARY`).

    Optional `scale` overrides the library's default UV repeat — useful when
    the same wood texture is applied to a tabletop (large surface, want a
    tighter repeat) vs a leg (small surface, want a wider repeat).
    """
    name: str
    scale: float | None = None


@dataclass(frozen=True)
class PbrMaterial:
    """PBR-shaded material — either textured (via `texture=...`) or procedural
    (base color + metallic + roughness numbers only).

    If `name` matches an entry in `materials.TEXTURE_LIBRARY`, the constructor
    fills missing fields from that entry. Explicit fields always win — caller
    can write `PbrMaterial(name="oak", metallic=0.3)` to tint a textured oak
    with custom metallic. Names that miss the library are still valid — the
    caller's explicit `base_color` / `metallic` / `roughness` are used as-is,
    or defaults fill in.

    Three renderers consume this:
    - Three.js viewer → `MeshStandardMaterial(color, metalness, roughness, map?)`
    - Blender Cycles  → Principled BSDF with the same params + image maps
    - URDF visual     → `<material><color rgba=...></material>` (no PBR; just the base color)
    """
    name: str
    base_color: tuple[float, float, float, float] | None = None
    metallic: float | None = None
    roughness: float | None = None
    texture: TextureRef | None = None

    def __post_init__(self) -> None:
        # Lazy import — avoids circular (.materials may import from us in future)
        from .materials import get_material
        entry = get_material(self.name)
        if entry is not None:
            # Textured library entry → set TextureRef if caller didn't
            if self.texture is None and not entry.get("procedural", False):
                object.__setattr__(self, "texture", TextureRef(name=self.name))
            # Fill PBR numbers from library when caller left them None
            if self.base_color is None and "base_color" in entry:
                bc = entry["base_color"]
                object.__setattr__(self, "base_color", (float(bc[0]), float(bc[1]), float(bc[2]), float(bc[3] if len(bc) > 3 else 1.0)))
            if self.metallic is None:
                object.__setattr__(self, "metallic", float(entry.get("default_metallic", 0.0)))
            if self.roughness is None:
                object.__setattr__(self, "roughness", float(entry.get("default_roughness", 0.5)))
        # Final defaults — unknown names + no caller overrides land here.
        # Textured materials default base_color to white (no tint) so the
        # texture image carries the colour. Untextured materials default
        # to a neutral grey so they're not invisible.
        if self.base_color is None:
            default_bc = (1.0, 1.0, 1.0, 1.0) if self.texture is not None else (0.7, 0.7, 0.7, 1.0)
            object.__setattr__(self, "base_color", default_bc)
        if self.metallic is None:
            object.__setattr__(self, "metallic", 0.0)
        if self.roughness is None:
            object.__setattr__(self, "roughness", 0.5)


def material_to_pbr(m: Material | PbrMaterial | None) -> PbrMaterial | None:
    """Normalise a Part's material to PbrMaterial for the glTF exporter.

    Accepts the legacy `Material` (promoted to procedural PbrMaterial with
    default PBR), a real PbrMaterial (passed through), or None.
    """
    if m is None:
        return None
    if isinstance(m, PbrMaterial):
        return m
    # Legacy Material → procedural PbrMaterial. Use the legacy rgba; the
    # name is preserved so URDF emit can still reference it by name.
    return PbrMaterial(name=m.name, base_color=tuple(m.rgba))


@dataclass
class Part:
    """One rigid body. Has a name (URDF link name), geometry, origin in parent joint frame."""
    name: str
    geometry: Geometry
    origin: Origin = field(default_factory=Origin)
    # Either legacy `Material` (flat RGBA — pre-Tier 3) or `PbrMaterial`
    # (PBR + optional texture). Both shapes flow through `material_to_pbr`
    # at glTF-export time; URDF export still emits an RGBA-only material.
    material: "Material | PbrMaterial | None" = None

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Part.name must be a non-empty string")


class JointType(str, Enum):
    REVOLUTE = "revolute"   # rotates around axis between limits
    FIXED = "fixed"         # rigid attachment


@dataclass
class Joint:
    """Connects child Part to parent Part. Axis + limits define motion (revolute) or are unused (fixed)."""
    name: str
    type: JointType
    parent: str                 # Part name
    child: str                  # Part name
    origin: Origin = field(default_factory=Origin)
    axis: Vec3 = (0.0, 0.0, 1.0)
    lower: float | None = None  # radians (revolute) — None = unbounded
    upper: float | None = None
    effort: float = 100.0
    velocity: float = 1.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Joint.name must be non-empty")
        if self.type == JointType.REVOLUTE:
            self.axis = _as_vec3(self.axis, name="Joint.axis")
            # Lower < upper if both given
            if self.lower is not None and self.upper is not None and self.lower >= self.upper:
                raise ValueError(f"Joint {self.name}: lower ({self.lower}) must be < upper ({self.upper})")


def Revolute(name: str, parent: str, child: str, *, axis: Vec3 = (0, 0, 1),
             origin: Origin | None = None, lower: float | None = None,
             upper: float | None = None) -> Joint:
    """Convenience constructor for a revolute joint."""
    return Joint(name=name, type=JointType.REVOLUTE, parent=parent, child=child,
                 origin=origin or Origin(), axis=axis, lower=lower, upper=upper)


def Fixed(name: str, parent: str, child: str, *, origin: Origin | None = None) -> Joint:
    """Convenience constructor for a fixed joint."""
    return Joint(name=name, type=JointType.FIXED, parent=parent, child=child,
                 origin=origin or Origin())


@dataclass
class ArticulatedObject:
    """Root model. Hold parts + joints; one part is the root (no incoming joint)."""
    name: str
    parts: list[Part] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)

    def add_part(self, part: Part) -> Part:
        if any(p.name == part.name for p in self.parts):
            raise ValueError(f"Duplicate part name: {part.name}")
        self.parts.append(part)
        return part

    def add_joint(self, joint: Joint) -> Joint:
        if any(j.name == joint.name for j in self.joints):
            raise ValueError(f"Duplicate joint name: {joint.name}")
        # Forward-reference check: parent + child must already be parts
        names = {p.name for p in self.parts}
        if joint.parent not in names:
            raise ValueError(f"Joint {joint.name}: parent part '{joint.parent}' not found")
        if joint.child not in names:
            raise ValueError(f"Joint {joint.name}: child part '{joint.child}' not found")
        self.joints.append(joint)
        return joint

    def root_part(self) -> Part:
        """The single part that is not the child of any joint."""
        child_names = {j.child for j in self.joints}
        roots = [p for p in self.parts if p.name not in child_names]
        if len(roots) != 1:
            raise ValueError(f"ArticulatedObject must have exactly one root, found {len(roots)}: {[p.name for p in roots]}")
        return roots[0]


# ─── Multi-object scenes ────────────────────────────────────────────────
#
# A `Scene` holds N `ArticulatedObject`s plus per-object `Placement` (xyz/rpy
# relative to scene origin). URDF format cannot express a scene; the scene
# exporter writes one URDF per object PLUS a `scene.json` placement manifest.
# Viewer parses scene.json, loads each URDF, applies placement transforms.
#
# Why not glTF? It would express scenes natively, but the SDK's `Revolute`/
# `Fixed` joints map to animation tracks rather than joint metadata — losing
# URDF semantics is a regression for the single-object case (which stays
# unchanged here). When EmptyOS wants weight-painting / non-rigid animation,
# the glTF path opens. For v1, multi-URDF + scene.json is the smallest
# correct addition.


# Hard cap so the viewer doesn't ship a hundred URDFs to render in-browser
# (each one is a separate WebGL scene-graph subtree). 10 is a comfortable
# limit for furniture/equipment scenes; raise if a real consumer needs it.
SCENE_OBJECT_CAP = 10


@dataclass(frozen=True)
class Placement:
    """Object placement in the scene frame. RPY in radians."""
    xyz: Vec3 = (0.0, 0.0, 0.0)
    rpy: Vec3 = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "xyz", _as_vec3(self.xyz, name="Placement.xyz"))
        object.__setattr__(self, "rpy", _as_vec3(self.rpy, name="Placement.rpy"))


@dataclass
class Scene:
    """Multi-object container. Each object is a self-contained `ArticulatedObject`
    with its own internal joint graph; objects in the scene have NO joints
    between them — they're independent assemblies arranged in space.
    """
    name: str
    objects: list[ArticulatedObject] = field(default_factory=list)
    placements: dict[str, Placement] = field(default_factory=dict)

    def add_object(self, obj: ArticulatedObject, placement: Placement | None = None) -> ArticulatedObject:
        if any(o.name == obj.name for o in self.objects):
            raise ValueError(f"Duplicate object name in scene: {obj.name}")
        if len(self.objects) >= SCENE_OBJECT_CAP:
            raise ValueError(
                f"Scene cap is {SCENE_OBJECT_CAP} objects; raise SCENE_OBJECT_CAP if you need more"
            )
        self.objects.append(obj)
        self.placements[obj.name] = placement or Placement()
        return obj

    def validate(self) -> list[str]:
        """Return human-readable scene-level errors. Empty = valid.

        Per-object structural checks (single-root, joint refs) are handled
        by each ArticulatedObject's own validators when their root_part()
        is called during export — this method covers only invariants the
        Scene itself enforces (count, placement coverage, name uniqueness).
        """
        errs: list[str] = []
        if not self.objects:
            errs.append("Scene must contain at least one ArticulatedObject")
        if len(self.objects) > SCENE_OBJECT_CAP:
            errs.append(f"Scene cap is {SCENE_OBJECT_CAP} objects, got {len(self.objects)}")
        seen: set[str] = set()
        for obj in self.objects:
            if obj.name in seen:
                errs.append(f"duplicate object name in scene: {obj.name}")
            seen.add(obj.name)
            if obj.name not in self.placements:
                errs.append(f"object '{obj.name}' has no placement in scene")
        return errs
