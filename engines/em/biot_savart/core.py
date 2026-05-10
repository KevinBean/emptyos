"""Biot-Savart for finite straight conductor segments + catenary spans.

Phasor convention: each conductor carries a complex current Ī (rms).
Field components are returned as complex phasors; magnitude is the
rms of the resulting time-varying field.

Conductor segment magnetic-field formula (closed form for a finite
straight segment, IEC 60287 / classical EM):

    Per segment with start B = (xB,yB,zB), end E = (xE,yE,zE), current Ī:
        a, b, c = E − B
        l       = |E − B|
        t       = [a(x−xB) + b(y−yB) + c(z−zB)] / l²
        S       = B + t·(E−B)                  (foot of perpendicular)
        δ²      = |r − S|²
        rB, rE  = |r − B|, |r − E|
        coef    = μ₀ / (4π·δ²) · (t/rB + (1−t)/rE)
        Bx̄     = Ī · coef · [(z−Sz)·b − (y−Sy)·c]
        Bȳ     = Ī · coef · [(x−Sx)·c − (z−Sz)·a]
        Bz̄     = Ī · coef · [(y−Sy)·a − (x−Sx)·b]

E-field (overhead lines, ground as PEC) uses image charges:
    Each segment carries linear charge density λ; its image is mirrored
    across the ground plane (z=0). Field at r = (x,y,z):
        E = (q/4πε₀) · ((r − r_real)/|r − r_real|³ − (r − r_image)/|r − r_image|³)

Numpy is used for vector ops; complex phasors are built-in `complex`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


MU_0 = 4e-7 * math.pi          # H/m
EPS_0 = 8.8541878128e-12        # F/m


def _polar(magnitude: float, angle_deg: float) -> complex:
    """Construct a complex phasor from magnitude + angle in degrees."""
    a = math.radians(angle_deg)
    return complex(magnitude * math.cos(a), magnitude * math.sin(a))


# ── Segment ───────────────────────────────────────────────────────────────

@dataclass
class ConductorSegment:
    begin: tuple[float, float, float]
    end: tuple[float, float, float]
    current: complex                # rms phasor, A
    charge_density: float = 0.0     # λ, C/m (for E-field; signed)

    @property
    def direction(self) -> tuple[float, float, float]:
        bx, by, bz = self.begin
        ex, ey, ez = self.end
        return (ex - bx, ey - by, ez - bz)

    @property
    def length(self) -> float:
        a, b, c = self.direction
        return math.sqrt(a * a + b * b + c * c)

    def magnetic_field_at(self, point: tuple[float, float, float]) -> tuple[complex, complex, complex]:
        """Return (Bx, By, Bz) phasors at `point`. Zero for points on the segment axis."""
        bx, by, bz = self.begin
        ex, ey, ez = self.end
        a, b, c = ex - bx, ey - by, ez - bz
        l2 = a * a + b * b + c * c
        if l2 <= 0:
            return (0j, 0j, 0j)
        x, y, z = point
        # foot-of-perpendicular parameter
        t = (a * (x - bx) + b * (y - by) + c * (z - bz)) / l2
        Sx = bx + a * t
        Sy = by + b * t
        Sz = bz + c * t
        delta2 = (x - Sx) ** 2 + (y - Sy) ** 2 + (z - Sz) ** 2
        if delta2 < 1e-12:
            return (0j, 0j, 0j)
        rB = math.sqrt((x - bx) ** 2 + (y - by) ** 2 + (z - bz) ** 2)
        rE = math.sqrt((x - ex) ** 2 + (y - ey) ** 2 + (z - ez) ** 2)
        if rB == 0 or rE == 0:
            return (0j, 0j, 0j)
        coef = MU_0 / (4 * math.pi * delta2) * (t / rB + (1 - t) / rE)
        Bx = self.current * (coef * ((z - Sz) * b - (y - Sy) * c))
        By = self.current * (coef * ((x - Sx) * c - (z - Sz) * a))
        Bz = self.current * (coef * ((y - Sy) * a - (x - Sx) * b))
        return (Bx, By, Bz)

    def electric_field_at(
        self, point: tuple[float, float, float], use_image: bool = True
    ) -> tuple[float, float, float]:
        """E-field from segment as point charge at midpoint with optional image.

        For long-line approximation along x with the field plane in y-z,
        a single point-charge approximation per segment is the same as
        what the JS reference does. Returns real-valued E (V/m); for AC
        problems the caller scales by phase.
        """
        bx, by, bz = self.begin
        ex, ey, ez = self.end
        cx = 0.5 * (bx + ex)
        cy = 0.5 * (by + ey)
        cz = 0.5 * (bz + ez)
        q = self.charge_density * self.length
        x, y, z = point
        # real charge
        dx, dy, dz = x - cx, y - cy, z - cz
        r1 = math.sqrt(dx * dx + dy * dy + dz * dz)
        if r1 < 1e-9:
            return (0.0, 0.0, 0.0)
        coef = q / (4 * math.pi * EPS_0)
        Ex = coef * dx / (r1 ** 3)
        Ey = coef * dy / (r1 ** 3)
        Ez = coef * dz / (r1 ** 3)
        if use_image and cz > 0:
            # Image charge at -cz with opposite sign
            dx2, dy2, dz2 = x - cx, y - cy, z - (-cz)
            r2 = math.sqrt(dx2 * dx2 + dy2 * dy2 + dz2 * dz2)
            if r2 > 0:
                Ex -= coef * dx2 / (r2 ** 3)
                Ey -= coef * dy2 / (r2 ** 3)
                Ez -= coef * dz2 / (r2 ** 3)
        return (Ex, Ey, Ez)


# ── Catenary span ─────────────────────────────────────────────────────────

@dataclass
class CatenaryConductor:
    """Single overhead conductor span with catenary sag.

    Approximated by `n_segments` straight segments along the catenary.
    For symmetric attachment (equal end heights), the catenary is
        z(u) = z_min · cosh((u − u_min) / a),  a = L / (2·acosh(z_end/z_min))

    Asymmetric attachment uses the offset-minimum-point approximation
    from the source paper.
    """

    start: tuple[float, float, float]
    end: tuple[float, float, float]
    z_min: float
    current: complex
    radius: float = 0.01
    n_segments: int = 10
    charge_density: float = 0.0

    def segments(self) -> list[ConductorSegment]:
        sx, sy, sz = self.start
        ex, ey, ez = self.end
        L = math.hypot(ex - sx, ey - sy)
        if L <= 0:
            return []
        if abs(sz - ez) < 1e-6:
            u_min = L / 2
            denom = math.acosh(sz / self.z_min) if self.z_min > 0 and sz > self.z_min else 1.0
            a = L / (2 * denom) if denom > 0 else L
        else:
            u_min = L * (sz / (sz + ez))
            zmax = max(sz, ez)
            denom = math.acosh(zmax / self.z_min) if self.z_min > 0 and zmax > self.z_min else 1.0
            a = L / (2 * denom) if denom > 0 else L

        dx_unit = (ex - sx) / L
        dy_unit = (ey - sy) / L
        points = []
        for i in range(self.n_segments + 1):
            t = i / self.n_segments
            u = t * L
            try:
                z = self.z_min * math.cosh((u - u_min) / a)
            except OverflowError:
                z = self.z_min
            x = sx + dx_unit * u
            y = sy + dy_unit * u
            points.append((x, y, z))
        return [
            ConductorSegment(points[i], points[i + 1], self.current, self.charge_density)
            for i in range(self.n_segments)
        ]


# ── Power line aggregator ────────────────────────────────────────────────

@dataclass
class PowerLine:
    conductors: list[CatenaryConductor] = field(default_factory=list)

    def add(self, conductor: CatenaryConductor) -> None:
        self.conductors.append(conductor)

    def all_segments(self) -> list[ConductorSegment]:
        out: list[ConductorSegment] = []
        for c in self.conductors:
            out.extend(c.segments())
        return out


# ── Public field functions ───────────────────────────────────────────────

def field_at_point(
    line: PowerLine,
    point: tuple[float, float, float],
) -> dict:
    """Compute B and E at a single field point.

    Returns:
      Bx, By, Bz : complex phasors (T, rms)
      |B|        : rms magnitude (T)
      Ex, Ey, Ez : real V/m (E-field uses charge_density on segments)
      |E|        : rms magnitude (V/m)
    """
    Bx = By = Bz = 0j
    Ex = Ey = Ez = 0.0
    for seg in line.all_segments():
        bx, by, bz = seg.magnetic_field_at(point)
        Bx += bx
        By += by
        Bz += bz
        if seg.charge_density != 0.0:
            ex, ey, ez = seg.electric_field_at(point, use_image=True)
            Ex += ex
            Ey += ey
            Ez += ez
    B_mag = math.sqrt(abs(Bx) ** 2 + abs(By) ** 2 + abs(Bz) ** 2)
    E_mag = math.sqrt(Ex * Ex + Ey * Ey + Ez * Ez)
    return {
        "Bx": Bx, "By": By, "Bz": Bz, "B": B_mag,
        "Ex": Ex, "Ey": Ey, "Ez": Ez, "E": E_mag,
    }


def field_along_axis(
    line: PowerLine,
    axis: str,
    minimum: float,
    maximum: float,
    steps: int,
    base_point: tuple[float, float, float],
) -> list[dict]:
    """Compute field along a straight profile (vary one axis, fix others).

    `axis` ∈ {"x", "y", "z"}. base_point provides the fixed coordinates.
    """
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be 'x', 'y', or 'z'")
    out: list[dict] = []
    bx, by, bz = base_point
    for i in range(steps):
        v = minimum + (maximum - minimum) * i / max(1, steps - 1)
        point = (
            v if axis == "x" else bx,
            v if axis == "y" else by,
            v if axis == "z" else bz,
        )
        r = field_at_point(line, point)
        out.append({
            "position": v,
            "x": point[0], "y": point[1], "z": point[2],
            "B": r["B"], "E": r["E"],
            "Bx": abs(r["Bx"]), "By": abs(r["By"]), "Bz": abs(r["Bz"]),
        })
    return out


def field_grid(
    line: PowerLine,
    plane: str,             # "xy" | "xz" | "yz"
    range1: tuple[float, float, int],
    range2: tuple[float, float, int],
    fixed: float,
) -> list[list[dict]]:
    """Compute |B| over a 2D grid for contour visualisation."""
    if plane not in ("xy", "xz", "yz"):
        raise ValueError("plane must be 'xy', 'xz', 'yz'")
    a_min, a_max, na = range1
    b_min, b_max, nb = range2
    out: list[list[dict]] = []
    for j in range(nb):
        v2 = b_min + (b_max - b_min) * j / max(1, nb - 1)
        row: list[dict] = []
        for i in range(na):
            v1 = a_min + (a_max - a_min) * i / max(1, na - 1)
            if plane == "xy":
                point = (v1, v2, fixed)
            elif plane == "xz":
                point = (v1, fixed, v2)
            else:  # yz
                point = (fixed, v1, v2)
            r = field_at_point(line, point)
            row.append({"x": point[0], "y": point[1], "z": point[2], "B": r["B"], "E": r["E"]})
        out.append(row)
    return out


# ── Convenience: balanced 3-phase line ────────────────────────────────────

def three_phase_overhead(
    current_a: float,
    span_length_m: float,
    height_m: float,
    sag_m: float,
    phase_spacing_m: float,
    n_segments: int = 10,
) -> PowerLine:
    """Symmetric horizontal 3-phase line centred at y=0, built as one span."""
    line = PowerLine()
    z_min = height_m - sag_m
    half = span_length_m / 2
    iA = _polar(current_a, 0)
    iB = _polar(current_a, -120)
    iC = _polar(current_a, 120)
    for I, y in ((iA, -phase_spacing_m), (iB, 0.0), (iC, +phase_spacing_m)):
        line.add(CatenaryConductor(
            start=(-half, y, height_m),
            end=(+half, y, height_m),
            z_min=z_min,
            current=I,
            n_segments=n_segments,
        ))
    return line
