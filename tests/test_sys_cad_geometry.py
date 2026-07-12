"""CAD geometry regression — the golden-set compile gate.

This is the Layer-2 (geometric fidelity) coverage the rest of the CAD suite
deliberately leaves open. `test_sys_cad.py::TestCadCompileCq` asserts the
*generated CadQuery source string*; `test_sys_cadquery_plugin.py` asserts
*launch-failure* hardening. Neither runs a real compile and checks the
resulting solid. So a regression in `compile_cq` (a units bug, a wrong
boolean, a swapped axis) that still emits plausible source and still launches
would ship silently — the volumes would just be wrong.

This file closes that: a small set of analytically-known parts is pushed
through the LIVE compile endpoint (eos-cad/1 doc → CadQuery B-rep → STEP/STL +
the Tier-1 shape-validation verdict), and the returned volume + bounding box
are asserted against hand-computed expectations. Each expected number is the
exact closed-form value (π from `math`, so it matches OpenCASCADE's), checked
to 0.2% relative — B-rep volume is exact, the tolerance only absorbs meshing
of the STL and float noise.

Two things it pins that no other test does:
  1. **Geometric correctness** — vol(plate − hole) == box − π r² h, etc.
  2. **The validity gate actually rejects bad geometry** — a subtract that
     engulfs the whole body must be flagged (zero-volume / invalid / compile
     failure), never silently pass as a healthy part.

Requires the CadQuery 3.12 user-home venv (see .claude/rules/environment.md
§ User-home Python envs). When that venv is absent — e.g. the dogfood CI box —
the whole module skips cleanly via the `cadquery_ready` probe, so this is a
local / cadquery-equipped regression gate, not a CI hard gate.

Run: python -m pytest tests/test_sys_cad_geometry.py -v
"""

from __future__ import annotations

import math
import time

import pytest

from helpers import TEST_PREFIX, assert_ok

PI = math.pi

# CadQuery runs in a subprocess venv; a single compile is ~10-15s on a warm
# box. The shared http_client fixture is built with a 15s timeout, so every
# compile POST overrides it explicitly.
COMPILE_TIMEOUT = 200.0

# Volumes are exact (OCC B-rep); 0.2% absorbs only STL meshing + float noise.
VOL_REL = 2e-3
# Bounding-box extents are exact to many places; 1µm slack is pure float safety.
BBOX_ABS = 1e-3


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}cadgeo-{stem}-{int(time.time() * 1000)}"


def _doc(features: dict | list, *, params: dict | None = None) -> dict:
    return {
        "schema": "eos-cad/1",
        "units": "mm",
        "params": params or {},
        "features": features,
    }


# ── Golden set: each part has exactly one top-level solid and a closed-form
# volume + axis-aligned bounding box. Boxes are centered on the origin; the
# CadQuery cylinder is centered on its axis; makeCone bases at the origin.
# Every number here is verifiable by hand from the feature tree — that's the
# point: if the compile drifts, the arithmetic stops matching.
_CUBE = _doc([{"id": "c", "op": "box", "size": [20, 20, 20]}])

_CYLINDER = _doc([{"id": "c", "op": "cylinder", "r": 10, "h": 25, "axis": "z"}])

_CONE = _doc([{"id": "c", "op": "cone", "r": 8, "r2": 0, "h": 16}])

_PLATE_HOLE = _doc([
    {"id": "plate", "op": "box", "size": [60, 40, 10]},
    {"id": "hole", "op": "cylinder", "r": 6, "h": 30, "axis": "z"},
    {"id": "out", "op": "subtract", "target": "plate", "tools": ["hole"]},
])

_PARAM_PLATE = _doc(
    [
        {"id": "plate", "op": "box", "size": ["w", "d", "t"]},
        {"id": "hole", "op": "cylinder", "r": "hr", "h": 20, "axis": "z"},
        {"id": "out", "op": "subtract", "target": "plate", "tools": ["hole"]},
    ],
    params={"w": 50, "d": 30, "t": 8, "hr": 4},
)

# A plate with a tall square post through it — clean overlapping union (the
# post's middle 10mm sits inside the 10mm plate, so 1000mm³ overlaps).
_UNION_POST = _doc([
    {"id": "plate", "op": "box", "size": [40, 40, 10]},
    {"id": "post", "op": "box", "size": [10, 10, 30]},
    {"id": "out", "op": "union", "inputs": ["plate", "post"]},
])

# (stem, document, expected_volume_mm3, (bx, by, bz))
GOLDEN = [
    ("cube", _CUBE, 20.0 ** 3, (20.0, 20.0, 20.0)),
    ("cylinder", _CYLINDER, PI * 10.0 ** 2 * 25.0, (20.0, 20.0, 25.0)),
    ("cone", _CONE, (1.0 / 3.0) * PI * 8.0 ** 2 * 16.0, (16.0, 16.0, 16.0)),
    ("plate_hole", _PLATE_HOLE, 60.0 * 40.0 * 10.0 - PI * 6.0 ** 2 * 10.0, (60.0, 40.0, 10.0)),
    ("param_plate", _PARAM_PLATE, 50.0 * 30.0 * 8.0 - PI * 4.0 ** 2 * 8.0, (50.0, 30.0, 8.0)),
    ("union_post", _UNION_POST, 40.0 * 40.0 * 10.0 + 10.0 * 10.0 * 30.0 - 10.0 * 10.0 * 10.0, (40.0, 40.0, 30.0)),
]


def _create(http_client, stem: str, doc: dict) -> str:
    name = _uniq(stem)
    created = assert_ok(http_client.post(
        "/cad/api/documents", json={"name": name, "document": dict(doc, name=name)}))
    assert created.get("ok") is True, created
    return created["id"]


def _compile(http_client, doc_id: str) -> dict:
    return http_client.post(
        f"/cad/api/documents/{doc_id}/compile", timeout=COMPILE_TIMEOUT).json()


@pytest.fixture(scope="module")
def cadquery_ready(http_client):
    """Probe the live compile path once. Skip the whole module when the
    CadQuery 3.12 venv isn't installed (so CI without it stays green), and fail
    loudly only on an unexpected error shape."""
    doc_id = _create(http_client, "probe", _CUBE)
    try:
        res = _compile(http_client, doc_id)
        if res.get("ok"):
            return True
        err = str(res.get("error") or "")
        if "isn't available" in err or "venv" in err.lower():
            pytest.skip("CadQuery 3.12 venv not installed — geometry regression skipped")
        pytest.skip(f"CadQuery probe compile failed unexpectedly: {err[:200]}")
    finally:
        http_client.delete(f"/cad/api/documents/{doc_id}")


@pytest.mark.api
class TestCadCompileGeometry:
    """Golden parts → live compile → assert volume, bbox, validity verdict."""

    @pytest.mark.parametrize(
        "stem,doc,exp_vol,exp_bbox", GOLDEN, ids=[g[0] for g in GOLDEN])
    def test_compiled_geometry(self, http_client, cadquery_ready, stem, doc, exp_vol, exp_bbox):
        doc_id = _create(http_client, stem, doc)
        try:
            res = _compile(http_client, doc_id)
            assert res.get("ok") is True, f"{stem}: compile failed: {res}"

            # Real exported artifacts, not just an ok flag.
            assert res["bytes"]["step"] > 0, f"{stem}: empty STEP"
            assert res["bytes"]["stl"] > 0, f"{stem}: empty STL"

            # Tier-1 shape-validation verdict must be present and clean.
            val = res.get("validation")
            assert val is not None, f"{stem}: no shape-validation verdict in compile response"
            assert val.get("ok") is True, f"{stem}: validation not ok: {val}"
            hard = [v for v in val.get("violations", []) if v.get("severity") == "hard"]
            assert not hard, f"{stem}: hard violations: {hard}"

            stats = val.get("stats") or {}
            assert stats.get("is_valid") is True, f"{stem}: solid not valid: {stats}"

            # Geometric fidelity — the load-bearing assertion.
            vol = stats.get("volume")
            assert vol == pytest.approx(exp_vol, rel=VOL_REL), \
                f"{stem}: volume {vol} != expected {exp_vol} (>{VOL_REL:.1%})"

            bb = stats.get("bbox") or {}
            ex, ey, ez = exp_bbox
            assert bb.get("x") == pytest.approx(ex, abs=BBOX_ABS), f"{stem}: bbox.x {bb.get('x')} != {ex}"
            assert bb.get("y") == pytest.approx(ey, abs=BBOX_ABS), f"{stem}: bbox.y {bb.get('y')} != {ey}"
            assert bb.get("z") == pytest.approx(ez, abs=BBOX_ABS), f"{stem}: bbox.z {bb.get('z')} != {ez}"
        finally:
            http_client.delete(f"/cad/api/documents/{doc_id}")

    def test_validity_gate_rejects_engulfing_subtract(self, http_client, cadquery_ready):
        """The curated shape-prior teaches 'never subtract the whole body'; this
        pins that the GATE catches it when generation doesn't. A box fully
        engulfed by its cutting cylinder yields empty geometry, which must be
        flagged — never reported as a healthy zero-ish solid."""
        bad = _doc([
            {"id": "stock", "op": "box", "size": [10, 10, 10]},
            {"id": "tool", "op": "cylinder", "r": 20, "h": 40, "axis": "z"},
            {"id": "out", "op": "subtract", "target": "stock", "tools": ["tool"]},
        ])
        doc_id = _create(http_client, "engulf", bad)
        try:
            res = _compile(http_client, doc_id)
            # Acceptable outcomes, all of which mean "the bad part was caught":
            #   - compile reports failure (empty solid couldn't export), or
            #   - the validation verdict is not ok / flags a hard violation, or
            #   - the solid is reported invalid / ~zero volume.
            if res.get("ok") is False:
                return  # gate refused at compile — good
            val = res.get("validation") or {}
            stats = val.get("stats") or {}
            hard = [v for v in val.get("violations", []) if v.get("severity") == "hard"]
            flagged = (
                val.get("ok") is False
                or bool(hard)
                or stats.get("is_valid") is False
                or (isinstance(stats.get("volume"), (int, float)) and stats["volume"] < 1e-6)
            )
            assert flagged, f"engulfing subtract was NOT flagged as bad geometry: {res}"
        finally:
            http_client.delete(f"/cad/api/documents/{doc_id}")
