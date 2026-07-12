"""TestContext — baseline sanity checks the agent loop relies on.

Mirrors Articraft's `sdk._core.v0.testing` at the conceptual level: a context
object the model script calls into to declare expectations + run checks. v0.1
ships three checks, the rest land when consumers actually need them:

- exactly_one_root() — single connected articulation tree
- all_joints_resolve() — every joint's parent/child names exist as parts
- no_zero_geometry() — no degenerate primitives

`ctx.report()` returns a structured dict the harness formats into
`<compile_signals>` for the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .types import ArticulatedObject, Box, Cylinder, Sphere

Severity = Literal["error", "warning", "info"]


@dataclass
class Finding:
    severity: Severity
    code: str           # short stable id e.g. "ROOT_COUNT"
    message: str        # human-readable
    where: str = ""     # part/joint name involved


@dataclass
class TestContext:
    """Per-run check accumulator. Pass the ArticulatedObject; call run_baseline()."""
    # Stops pytest from trying to collect this as a test class.
    __test__ = False

    model: ArticulatedObject
    findings: list[Finding] = field(default_factory=list)

    def _add(self, severity: Severity, code: str, message: str, where: str = "") -> None:
        self.findings.append(Finding(severity=severity, code=code, message=message, where=where))

    def check_exactly_one_root(self) -> None:
        child_names = {j.child for j in self.model.joints}
        roots = [p for p in self.model.parts if p.name not in child_names]
        if len(roots) == 0:
            self._add("error", "ROOT_COUNT",
                      "No root part — every part is the child of some joint (cycle?)")
        elif len(roots) > 1:
            self._add("error", "ROOT_COUNT",
                      f"Multiple roots: {[r.name for r in roots]} — expected exactly one",
                      where=",".join(r.name for r in roots))

    def check_all_joints_resolve(self) -> None:
        names = {p.name for p in self.model.parts}
        for j in self.model.joints:
            if j.parent not in names:
                self._add("error", "JOINT_PARENT_MISSING",
                          f"Joint {j.name!r}: parent part {j.parent!r} not in model",
                          where=j.name)
            if j.child not in names:
                self._add("error", "JOINT_CHILD_MISSING",
                          f"Joint {j.name!r}: child part {j.child!r} not in model",
                          where=j.name)

    def check_no_zero_geometry(self) -> None:
        for p in self.model.parts:
            g = p.geometry
            if isinstance(g, Box) and any(s <= 1e-9 for s in g.size):
                self._add("warning", "ZERO_BOX", f"Part {p.name!r} has near-zero box dimension {g.size}", where=p.name)
            elif isinstance(g, Cylinder) and (g.radius <= 1e-9 or g.length <= 1e-9):
                self._add("warning", "ZERO_CYLINDER", f"Part {p.name!r} has near-zero cylinder dim r={g.radius} l={g.length}", where=p.name)
            elif isinstance(g, Sphere) and g.radius <= 1e-9:
                self._add("warning", "ZERO_SPHERE", f"Part {p.name!r} has near-zero sphere radius {g.radius}", where=p.name)

    def _world_aabbs(self) -> dict:
        """World-space visual AABB per part: ``{name: ((lo),(hi))}`` or {} when
        the kinematic tree is invalid (multi-root / unresolved joints).

        Shared by `check_spatial_sanity` (joint-meets checks) and
        `check_assembly` (interpenetration). Position-only — ignores joint rpy
        rotations (the common axis-aligned furniture/equipment case doesn't use
        rotation; SE(3) handling is a later upgrade, see the v1 note below).
        """
        # The tree walk assumes one root + resolved refs.
        if any(f.code in ("ROOT_COUNT", "JOINT_PARENT_MISSING", "JOINT_CHILD_MISSING")
               for f in self.findings):
            return {}
        try:
            root = self.model.root_part()
        except ValueError:
            return {}

        # BFS from root. world_xyz[name] = position of part's LINK frame.
        parent_to_children: dict[str, list] = {}
        for j in self.model.joints:
            parent_to_children.setdefault(j.parent, []).append(j)
        world_xyz: dict[str, tuple] = {root.name: (0.0, 0.0, 0.0)}
        queue: list[str] = [root.name]
        while queue:
            parent_name = queue.pop(0)
            for j in parent_to_children.get(parent_name, []):
                px, py, pz = world_xyz[parent_name]
                jx, jy, jz = j.origin.xyz
                world_xyz[j.child] = (px + jx, py + jy, pz + jz)
                queue.append(j.child)

        def visual_aabb(part) -> tuple:
            link_xyz = world_xyz.get(part.name)
            if link_xyz is None:
                return None
            vx, vy, vz = part.origin.xyz
            cx = link_xyz[0] + vx
            cy = link_xyz[1] + vy
            cz = link_xyz[2] + vz
            g = part.geometry
            if isinstance(g, Box):
                hx, hy, hz = g.size[0] / 2.0, g.size[1] / 2.0, g.size[2] / 2.0
            elif isinstance(g, Cylinder):
                # Cylinder length is along link Z by URDF convention. Without
                # rotation handling, treat cylinder as axis-aligned along z.
                hx = hy = g.radius
                hz = g.length / 2.0
            elif isinstance(g, Sphere):
                hx = hy = hz = g.radius
            else:
                return None
            return (cx - hx, cy - hy, cz - hz), (cx + hx, cy + hy, cz + hz)

        return {p.name: visual_aabb(p) for p in self.model.parts}

    def check_assembly(self) -> None:
        """World-model assembly smell: gross interpenetration of independent
        (unconnected) parts. Delegates to the shared `shape_validation` engine.

        Ground-contact + scale are intentionally left OFF here — a lone object's
        rest height is the scene's concern (the sited-layout path enables it),
        and scale is already covered by `check_spatial_sanity`'s MODEL_HUGE.
        Jointed pairs are passed as `connected_pairs` so the bodies that are
        MEANT to touch at a joint aren't flagged. Soft only — annotates, never
        fails the compile. Import is guarded so a missing engine never breaks
        the baseline.
        """
        try:
            from engines.shape_validation.assembly import validate_assembly
        except Exception:
            return
        aabbs = self._world_aabbs()
        boxes = [
            {"name": n, "min": a[0], "max": a[1]}
            for n, a in aabbs.items() if a is not None
        ]
        if len(boxes) < 2:
            return
        connected = {frozenset({j.parent, j.child}) for j in self.model.joints}
        rep = validate_assembly(
            boxes, connected_pairs=connected,
            check_ground=False, check_scale=False, check_overlap=True,
        )
        for v in rep.violations:
            self._add("warning", v.code, v.message, where=v.where)

    def check_spatial_sanity(self) -> None:
        """Detect joints whose connected parts don't actually meet in space.

        Walks the kinematic tree from the root, computes each part's world
        position (chain of joint origins from the root) and visual AABB
        (link world position + part visual origin + primitive extent), then
        for every joint checks the parent and child bodies actually touch
        — overlapping or within a small tolerance.

        Catches the common URDF mistake where the LLM confuses VISUAL
        origin with LINK FRAME origin: setting `top.visual.origin = (0,0,0.75)`
        makes the top RENDER at z=0.75, but top_link's frame stays at world
        origin (it's the root). A joint with origin (0.28, 0.18, 0) puts
        the child's link at world (0.28, 0.18, 0), NOT at the top's visual
        height — so the connected leg ends up hanging below the floor while
        the table top floats far above it.

        Limitations (v1): position-only arithmetic, ignores joint rpy
        rotations in the chain. A model that uses rotations significantly
        may slip past this check. The most common failure mode (axis-aligned
        furniture, stacked parts) does NOT involve rotation, so this catches
        the bulk of the problem without the SE(3) complexity.

        Severity: `error` (compile-fail) for clearly disconnected joints —
        the gap > tolerance. `warning` for implausibly large total extent
        (probable units mistake).
        """
        # World-space AABBs (shared with check_assembly). Empty when the tree
        # is invalid — the per-joint checks below need a resolved single-root tree.
        aabbs = self._world_aabbs()
        if not aabbs:
            return

        def gap(a, b) -> float:
            """Min distance between two axis-aligned bounding boxes. 0 if overlapping."""
            gx = max(0.0, max(a[0][0] - b[1][0], b[0][0] - a[1][0]))
            gy = max(0.0, max(a[0][1] - b[1][1], b[0][1] - a[1][1]))
            gz = max(0.0, max(a[0][2] - b[1][2], b[0][2] - a[1][2]))
            return (gx * gx + gy * gy + gz * gz) ** 0.5

        def extent_max(aabb) -> float:
            return max(
                aabb[1][0] - aabb[0][0],
                aabb[1][1] - aabb[0][1],
                aabb[1][2] - aabb[0][2],
            )

        # Per-joint connectivity check.
        for j in self.model.joints:
            a, b = aabbs.get(j.parent), aabbs.get(j.child)
            if a is None or b is None:
                continue
            d = gap(a, b)
            # Tolerance: 5% of the larger part's extent, floored at 5mm
            # to absorb numerical noise on tiny parts.
            tol = max(0.005, 0.05 * max(extent_max(a), extent_max(b)))
            if d > tol:
                self._add(
                    "error",
                    "PART_DISCONNECTED",
                    f"Joint {j.name!r} attaches {j.parent!r} ↔ {j.child!r} "
                    f"but their visual bodies are {d * 1000:.0f}mm apart "
                    f"(tolerance {tol * 1000:.0f}mm). "
                    f"Common cause: the joint origin is in the LINK frame "
                    f"(the root link is at world origin, not at the visual "
                    f"position). If parent.visual.origin=(0,0,h), the joint "
                    f"origin must include +h in z to put the child at the "
                    f"parent's visual height.",
                    where=j.name,
                )

        # Implausible total extent — model spans more than 5m in any axis.
        # Likely a unit mistake (cm written as 100 instead of 1) or a
        # runaway origin. Warning, not error — large robots exist.
        if aabbs:
            valid = [a for a in aabbs.values() if a is not None]
            if valid:
                lo_x = min(a[0][0] for a in valid)
                lo_y = min(a[0][1] for a in valid)
                lo_z = min(a[0][2] for a in valid)
                hi_x = max(a[1][0] for a in valid)
                hi_y = max(a[1][1] for a in valid)
                hi_z = max(a[1][2] for a in valid)
                span = max(hi_x - lo_x, hi_y - lo_y, hi_z - lo_z)
                if span > 5.0:
                    self._add(
                        "warning",
                        "MODEL_HUGE",
                        f"Model spans {span:.1f}m on its largest axis. "
                        f"Likely a units mistake (cm vs m) or a runaway "
                        f"origin. SDK expects metres.",
                        where=self.model.name,
                    )

    def run_baseline(self) -> None:
        """Run every baseline check. Call this once per compile."""
        self.check_exactly_one_root()
        self.check_all_joints_resolve()
        self.check_no_zero_geometry()
        self.check_spatial_sanity()
        self.check_assembly()

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def report(self) -> dict:
        """Structured report — the harness formats this into <compile_signals> for the LLM."""
        return {
            "ok": self.ok,
            "part_count": len(self.model.parts),
            "joint_count": len(self.model.joints),
            "errors": [{"code": f.code, "message": f.message, "where": f.where} for f in self.errors],
            "warnings": [{"code": f.code, "message": f.message, "where": f.where} for f in self.warnings],
        }
