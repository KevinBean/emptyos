"""Unit tests for emptyos.sdk.georef — pure, no daemon, no pyproj required."""

import math

import pytest

from emptyos.sdk.georef import (
    AffineTransform,
    ProjTransform,
    fit_transform,
    from_epsg,
    residual_rms,
    transform_from_dict,
)


# A realistic Sydney-ish site: local easting/northing (m) → WGS84, with the
# anisotropic deg/metre scaling a uniform-scale similarity could NOT recover.
LAT0, LON0 = -33.80, 151.20
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def _true(x, y):
    return (LON0 + x / M_PER_DEG_LON, LAT0 + y / M_PER_DEG_LAT)


def _control(points):
    return [((x, y), _true(x, y)) for (x, y) in points]


def test_affine_recovers_anisotropic_mapping():
    pairs = _control([(0, 0), (100, 0), (0, 100), (140, 60)])
    t = fit_transform(pairs)
    assert isinstance(t, AffineTransform)
    # A point not in the control set must map to ~ the true location.
    lon, lat = t.apply(55.0, 70.0)
    tlon, tlat = _true(55.0, 70.0)
    assert lon == pytest.approx(tlon, abs=1e-9)
    assert lat == pytest.approx(tlat, abs=1e-9)
    # Sub-millimetre fit.
    assert residual_rms(pairs, t) < 1e-3


def test_affine_with_rotation():
    # Rotate the local frame 30° before mapping; affine must still recover it.
    th = math.radians(30.0)
    rot = [(0, 0), (100, 0), (0, 100), (100, 100)]
    pairs = []
    for x, y in rot:
        rx = x * math.cos(th) - y * math.sin(th)
        ry = x * math.sin(th) + y * math.cos(th)
        pairs.append(((x, y), _true(rx, ry)))
    t = fit_transform(pairs)
    lon, lat = t.apply(50, 50)
    rx = 50 * math.cos(th) - 50 * math.sin(th)
    ry = 50 * math.sin(th) + 50 * math.cos(th)
    tlon, tlat = _true(rx, ry)
    assert lon == pytest.approx(tlon, abs=1e-9)
    assert lat == pytest.approx(tlat, abs=1e-9)


def test_two_point_similarity():
    pairs = _control([(0, 0), (100, 100)])
    t = fit_transform(pairs)
    # The two control points themselves must map exactly.
    for (lx, ly), (lon, lat) in pairs:
        plon, plat = t.apply(lx, ly)
        assert plon == pytest.approx(lon, abs=1e-9)
        assert plat == pytest.approx(lat, abs=1e-9)


def test_residual_rms_metres_for_noisy_fit():
    pairs = _control([(0, 0), (100, 0), (0, 100), (100, 100)])
    # Nudge one target by ~1 m north.
    (lx, ly), (lon, lat) = pairs[-1]
    pairs[-1] = ((lx, ly), (lon, lat + 1.0 / M_PER_DEG_LAT))
    t = fit_transform(pairs)
    rms = residual_rms(pairs, t)
    # A single ~1 m perturbation spread over 4 points → sub-metre RMS, but > 0.
    assert 0.1 < rms < 1.0


def test_affine_dict_roundtrip():
    pairs = _control([(0, 0), (100, 0), (0, 100), (100, 100)])
    t = fit_transform(pairs)
    d = t.to_dict()
    assert d["kind"] == "affine" and len(d["coeffs"]) == 6
    t2 = transform_from_dict(d)
    assert t2.apply(33, 77) == pytest.approx(t.apply(33, 77), abs=1e-12)


def test_epsg_transform_dict_roundtrip():
    t = from_epsg(7855)
    assert isinstance(t, ProjTransform)
    assert t.to_dict() == {"kind": "epsg", "epsg": 7855}
    assert isinstance(transform_from_dict(t.to_dict()), ProjTransform)


def test_too_few_points():
    with pytest.raises(ValueError):
        fit_transform([((0, 0), (151.2, -33.8))])


def test_collinear_points_raise():
    # 3 collinear points → degenerate affine.
    pairs = _control([(0, 0), (50, 50), (100, 100)])
    with pytest.raises(ValueError):
        fit_transform(pairs)


def test_apply_many_returns_lonlat_pairs():
    pairs = _control([(0, 0), (100, 0), (0, 100), (100, 100)])
    t = fit_transform(pairs)
    out = t.apply_many([[10, 20], [30, 40, 999]])  # extra z ignored
    assert len(out) == 2 and all(len(p) == 2 for p in out)
