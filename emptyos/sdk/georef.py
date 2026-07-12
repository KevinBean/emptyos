"""Georeferencing transforms — map a local CAD/survey coordinate frame to WGS84.

Pure math, no I/O, no kernel — unit-testable without a daemon. Two ways to
build a transform from a local easting/northing (or arbitrary drawing) frame to
WGS84 ``[lon, lat]``:

- :func:`fit_transform` — least-squares **affine** fit from control-point pairs
  ``[(local_xy, lonlat), ...]``. No EPSG needed; handles an arbitrary local
  grid, rotation, scale, and the deg/metre axis anisotropy (a degree of
  longitude shrinks with latitude — a uniform-scale similarity can't capture
  that, a 6-parameter affine can). ≥3 non-collinear points → full affine;
  exactly 2 → a 4-parameter Helmert similarity fallback.
- :func:`from_epsg` — wrap a known projected CRS (EPSG) with ``pyproj`` and
  reproject directly. ``pyproj`` is an optional dep (``pip install
  'emptyos[geo]'``), imported lazily so the module loads without it.

Both produce a :class:`Transform` exposing ``apply`` / ``apply_many`` and a
serialisable ``to_dict`` (rebuild via :func:`transform_from_dict`).
:func:`residual_rms` reports fit quality in **metres** so callers can surface
alignment confidence.

Output coordinate order is always GeoJSON ``[lon, lat]`` (RFC 7946).
"""

from __future__ import annotations

import math
from typing import Sequence

# Metres per degree, near the surface. Latitude is ~constant; longitude scales
# by cos(latitude). Used only to express residuals + degree errors in metres.
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LON_EQ = 111_320.0

Pair = tuple[Sequence[float], Sequence[float]]  # ((local_x, local_y), (lon, lat))


class Transform:
    """Base: map a local ``(x, y)`` to WGS84 ``(lon, lat)``."""

    kind = "identity"

    def apply(self, x: float, y: float) -> tuple[float, float]:  # pragma: no cover
        raise NotImplementedError

    def apply_many(
        self, coords: Sequence[Sequence[float]]
    ) -> list[list[float]]:
        """Transform a list of ``[x, y]`` (or ``[x, y, ...]``) to ``[lon, lat]``."""
        out = []
        for c in coords:
            lon, lat = self.apply(float(c[0]), float(c[1]))
            out.append([lon, lat])
        return out

    def to_dict(self) -> dict:  # pragma: no cover
        raise NotImplementedError


class AffineTransform(Transform):
    """``lon = a*x + b*y + c`` ; ``lat = d*x + e*y + f``."""

    kind = "affine"

    def __init__(self, coeffs: Sequence[float]):
        if len(coeffs) != 6:
            raise ValueError("affine needs 6 coefficients (a,b,c,d,e,f)")
        self.a, self.b, self.c, self.d, self.e, self.f = (float(v) for v in coeffs)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.a * x + self.b * y + self.c,
            self.d * x + self.e * y + self.f,
        )

    def to_dict(self) -> dict:
        return {
            "kind": "affine",
            "coeffs": [self.a, self.b, self.c, self.d, self.e, self.f],
        }


class ProjTransform(Transform):
    """Reproject from a known source EPSG to WGS84 via ``pyproj`` (lazy)."""

    kind = "epsg"

    def __init__(self, epsg: int):
        self.epsg = int(epsg)
        self._tx = None  # lazily built pyproj.Transformer

    def _transformer(self):
        if self._tx is None:
            try:
                from pyproj import Transformer
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise RuntimeError(
                    "pyproj not installed — pip install 'emptyos[geo]' "
                    "or pip install 'pyproj>=3.6' to reproject from an EPSG. "
                    "Alternatively supply control points (no EPSG needed)."
                ) from exc
            # always_xy=True → input/output ordered (x=lon/easting, y=lat/northing)
            self._tx = Transformer.from_crs(
                f"EPSG:{self.epsg}", "EPSG:4326", always_xy=True
            )
        return self._tx

    def apply(self, x: float, y: float) -> tuple[float, float]:
        lon, lat = self._transformer().transform(x, y)
        return (float(lon), float(lat))

    def to_dict(self) -> dict:
        return {"kind": "epsg", "epsg": self.epsg}


def from_epsg(src_epsg: int) -> ProjTransform:
    """Transform from a known projected CRS (e.g. ``7855`` for GDA2020/MGA z55)."""
    return ProjTransform(src_epsg)


def transform_from_dict(d: dict) -> Transform:
    """Rebuild a Transform from :meth:`Transform.to_dict` output."""
    kind = (d or {}).get("kind")
    if kind == "affine":
        return AffineTransform(d["coeffs"])
    if kind == "epsg":
        return ProjTransform(d["epsg"])
    raise ValueError(f"unknown transform kind: {kind!r}")


def _solve3(A: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""
    # Augmented matrix.
    m = [row[:] + [rhs[i]] for i, row in enumerate(A)]
    for col in range(3):
        # pivot
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-15:
            raise ValueError("control points are degenerate (collinear?)")
        m[col], m[piv] = m[piv], m[col]
        # eliminate
        for r in range(3):
            if r == col:
                continue
            factor = m[r][col] / m[col][col]
            for k in range(col, 4):
                m[r][k] -= factor * m[col][k]
    return [m[i][3] / m[i][i] for i in range(3)]


def _fit_affine(pairs: Sequence[Pair]) -> AffineTransform:
    """6-parameter least-squares affine from ≥3 control points."""
    # Normal equations for lon = a*x + b*y + c and lat = d*x + e*y + f.
    # Shared design matrix M = [[x,y,1]]; solve MᵀM · p = Mᵀ·target per axis.
    s_xx = s_xy = s_x = s_yy = s_y = s_1 = 0.0
    s_xlon = s_ylon = s_lon = 0.0
    s_xlat = s_ylat = s_lat = 0.0
    for (lx, ly), (lon, lat) in pairs:
        lx, ly, lon, lat = float(lx), float(ly), float(lon), float(lat)
        s_xx += lx * lx
        s_xy += lx * ly
        s_x += lx
        s_yy += ly * ly
        s_y += ly
        s_1 += 1.0
        s_xlon += lx * lon
        s_ylon += ly * lon
        s_lon += lon
        s_xlat += lx * lat
        s_ylat += ly * lat
        s_lat += lat
    ata = [[s_xx, s_xy, s_x], [s_xy, s_yy, s_y], [s_x, s_y, s_1]]
    a, b, c = _solve3(ata, [s_xlon, s_ylon, s_lon])
    d, e, f = _solve3(ata, [s_xlat, s_ylat, s_lat])
    return AffineTransform([a, b, c, d, e, f])


def _fit_similarity(pairs: Sequence[Pair]) -> AffineTransform:
    """4-parameter Helmert (uniform scale + rotation + translation), for 2 points.

    Expressed as an affine so the result type is uniform. Note: with a single
    uniform scale this cannot correct the deg/metre longitude anisotropy — use
    ≥3 points for a precise fit; this is the best obtainable from exactly 2.
    """
    n = len(pairs)
    cx = sum(float(p[0][0]) for p in pairs) / n
    cy = sum(float(p[0][1]) for p in pairs) / n
    clon = sum(float(p[1][0]) for p in pairs) / n
    clat = sum(float(p[1][1]) for p in pairs) / n
    num_a = num_b = den = 0.0
    for (lx, ly), (lon, lat) in pairs:
        ax, ay = float(lx) - cx, float(ly) - cy
        bx, by = float(lon) - clon, float(lat) - clat
        num_a += ax * bx + ay * by
        num_b += ax * by - ay * bx
        den += ax * ax + ay * ay
    if den < 1e-15:
        raise ValueError("control points coincide — cannot fit a transform")
    a = num_a / den  # s*cosθ
    b = num_b / den  # s*sinθ
    # lon = a*x - b*y + tx ; lat = b*x + a*y + ty
    tx = clon - (a * cx - b * cy)
    ty = clat - (b * cx + a * cy)
    return AffineTransform([a, -b, tx, b, a, ty])


def fit_transform(pairs: Sequence[Pair]) -> AffineTransform:
    """Least-squares affine from control-point pairs ``[(local_xy, lonlat), ...]``.

    ≥3 non-collinear points → full 6-parameter affine (corrects axis
    anisotropy). Exactly 2 → 4-parameter Helmert similarity. <2 → error.
    """
    pairs = list(pairs)
    if len(pairs) < 2:
        raise ValueError("need at least 2 control points")
    if len(pairs) == 2:
        return _fit_similarity(pairs)
    return _fit_affine(pairs)


def residual_rms(pairs: Sequence[Pair], transform: Transform) -> float:
    """RMS of control-point residuals in **metres** (alignment confidence)."""
    pairs = list(pairs)
    if not pairs:
        return 0.0
    mean_lat = sum(float(p[1][1]) for p in pairs) / len(pairs)
    m_per_deg_lon = _M_PER_DEG_LON_EQ * math.cos(math.radians(mean_lat))
    sq = 0.0
    for (lx, ly), (lon, lat) in pairs:
        plon, plat = transform.apply(float(lx), float(ly))
        dx = (plon - float(lon)) * m_per_deg_lon
        dy = (plat - float(lat)) * _M_PER_DEG_LAT
        sq += dx * dx + dy * dy
    return math.sqrt(sq / len(pairs))


def arc_ring(
    cx: float, cy: float, r: float, a0: float, a1: float, *, segments: int = 48,
) -> list[list[float]]:
    """Sample a circle/arc as ``[x, y]`` points in its OWN local frame.

    Angles in degrees CCW; ``a1 <= a0`` → a full circle. Used to approximate
    circle/arc CAD entities as polylines before projecting to WGS84 — shared by
    the geo-cad DXF importer and the cad-draft publisher.
    """
    if a1 <= a0:
        a1 += 360.0
    n = max(8, int(segments * (a1 - a0) / 360.0))
    return [[cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))]
            for i in range(n + 1)]
