"""Measuring GeoJSON geometry — length and representative point.

Anchored on published great-circle distances, because a length that is
plausible but wrong is exactly the failure mode here: it flows straight into a
line impedance and nothing downstream looks odd.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.georef import (
    geometry_length_m,
    geometry_point,
    haversine_m,
)


# ── haversine against published distances ──────────────────────────────


def test_one_degree_of_latitude_is_about_111_km():
    d = haversine_m([0.0, 0.0], [0.0, 1.0])
    assert d == pytest.approx(111_195, rel=0.001)


def test_a_degree_of_longitude_shrinks_with_latitude():
    """cos(lat) — the anisotropy the affine fit above exists to capture."""
    at_equator = haversine_m([0.0, 0.0], [1.0, 0.0])
    at_sixty = haversine_m([0.0, 60.0], [1.0, 60.0])
    assert at_sixty == pytest.approx(at_equator * 0.5, rel=0.002)


def test_sydney_to_melbourne():
    """~713 km great-circle, the published figure."""
    d = haversine_m([151.2093, -33.8688], [144.9631, -37.8136])
    assert d == pytest.approx(713_000, rel=0.01)


def test_distance_is_symmetric_and_zero_to_itself():
    a, b = [151.2, -33.9], [151.3, -34.0]
    assert haversine_m(a, b) == pytest.approx(haversine_m(b, a))
    assert haversine_m(a, a) == pytest.approx(0.0, abs=1e-9)


# ── Length ─────────────────────────────────────────────────────────────


def _line(*pts) -> dict:
    return {"type": "LineString", "coordinates": [list(p) for p in pts]}


def test_a_two_point_line_is_the_point_distance():
    geom = _line([151.0, -33.0], [151.1, -33.0])
    assert geometry_length_m(geom) == pytest.approx(
        haversine_m([151.0, -33.0], [151.1, -33.0]))


def test_segments_sum():
    a, b, c = [151.0, -33.0], [151.1, -33.0], [151.1, -33.1]
    assert geometry_length_m(_line(a, b, c)) == pytest.approx(
        haversine_m(a, b) + haversine_m(b, c))


def test_multilinestring_sums_its_parts():
    geom = {"type": "MultiLineString", "coordinates": [
        [[151.0, -33.0], [151.1, -33.0]],
        [[151.2, -33.0], [151.3, -33.0]],
    ]}
    single = haversine_m([151.0, -33.0], [151.1, -33.0])
    assert geometry_length_m(geom) == pytest.approx(2 * single, rel=1e-6)


# ── None means "not a route", not "zero length" ────────────────────────


@pytest.mark.parametrize("geom", [
    {"type": "Point", "coordinates": [151.0, -33.0]},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    {"type": "LineString", "coordinates": [[151.0, -33.0]]},   # single vertex
    {"type": "LineString", "coordinates": []},
    {"type": "Nonsense", "coordinates": [[0, 0], [1, 1]]},
    {},
    None,
])
def test_things_with_no_length_return_none_not_zero(geom):
    """A caller asking for a route length must be able to tell 'this is not a
    route' from 'this route is zero-length'. 0.0 answers the wrong question
    plausibly."""
    assert geometry_length_m(geom) is None


def test_a_degenerate_line_still_reports_zero_not_none():
    """Two identical vertices IS a route — a zero-length one."""
    geom = _line([151.0, -33.0], [151.0, -33.0])
    assert geometry_length_m(geom) == pytest.approx(0.0, abs=1e-9)


# ── Representative point ───────────────────────────────────────────────


def test_a_point_returns_itself():
    assert geometry_point({"type": "Point", "coordinates": [151.0, -33.0]}) == [151.0, -33.0]


def test_a_line_midpoint_is_along_the_route_not_the_vertex_mean():
    """Three vertices clustered at one end: the vertex mean sits near the
    cluster, the along-route midpoint sits in the middle of the line."""
    geom = _line([0.0, 0.0], [0.01, 0.0], [1.0, 0.0])
    mid = geometry_point(geom)
    vertex_mean = (0.0 + 0.01 + 1.0) / 3.0
    assert mid[0] == pytest.approx(0.5, abs=0.01)
    assert abs(mid[0] - vertex_mean) > 0.1


def test_an_even_line_midpoint_lands_in_the_middle():
    mid = geometry_point(_line([0.0, 0.0], [1.0, 0.0]))
    assert mid == pytest.approx([0.5, 0.0], abs=1e-6)


def test_a_polygon_returns_its_first_ring_mean():
    geom = {"type": "Polygon",
            "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    pt = geometry_point(geom)
    assert pt[0] == pytest.approx(0.8, abs=0.01)   # 5 vertices, first repeated


def test_a_zero_length_line_returns_its_first_vertex():
    assert geometry_point(_line([151.0, -33.0], [151.0, -33.0])) == [151.0, -33.0]


@pytest.mark.parametrize("geom", [{}, None, {"type": "Point", "coordinates": []},
                                  {"type": "LineString", "coordinates": [[1, 1]]}])
def test_unusable_geometry_returns_no_point(geom):
    assert geometry_point(geom) is None
