"""Biot-Savart sanity + analytical limit checks.

Tests verify the implementation against known closed-form limits:
  - Long straight wire: B = μ₀·I / (2π·d)
  - Symmetry of 3-phase balanced line (no DC offset; sum of phasors = 0)
  - Decay with distance (1/d for infinite wire)
  - 400 kV reference geometry from the source paper (ballpark)
"""

from __future__ import annotations

import math

import pytest

from engines.em.biot_savart import (
    CatenaryConductor,
    ConductorSegment,
    PowerLine,
    field_along_axis,
    field_at_point,
)
from engines.em.biot_savart.core import MU_0, three_phase_overhead


# Helper: long straight wire along x-axis, current 1000 A real
def _long_wire(I: complex, half_length: float = 1000.0, y0: float = 0.0, z0: float = 0.0) -> PowerLine:
    line = PowerLine()
    line.add(CatenaryConductor(
        start=(-half_length, y0, z0),
        end=(+half_length, y0, z0),
        z_min=z0,           # avoid sag for straight-wire test
        current=I,
        n_segments=20,
    ))
    return line


class TestLongStraightWire:
    """Compare against closed form B = μ₀·I / (2π·d) for an "infinite" wire."""

    def test_field_decays_as_1_over_d(self):
        line = _long_wire(complex(1000.0, 0.0))
        # Field is in y-z plane perpendicular to x; pick distances 1 m and 4 m
        f1 = field_at_point(line, (0.0, 0.0, 1.0))
        f4 = field_at_point(line, (0.0, 0.0, 4.0))
        assert abs(f1["B"] / f4["B"] - 4.0) < 0.01

    def test_field_magnitude_matches_closed_form(self):
        I = 1000.0
        line = _long_wire(complex(I, 0.0))
        d = 2.0
        f = field_at_point(line, (0.0, 0.0, d))
        # Approximation valid because half-length (1000 m) >> d (2 m)
        expected = MU_0 * I / (2 * math.pi * d)
        assert abs(f["B"] - expected) / expected < 0.005

    def test_microtesla_scale(self):
        # 1 A through a wire at 1 m: B = 2e-7 T = 0.2 μT (textbook number)
        line = _long_wire(complex(1.0, 0.0))
        f = field_at_point(line, (0.0, 0.0, 1.0))
        assert abs(f["B"] - 2e-7) / 2e-7 < 0.01


class TestThreePhaseBalanced:
    def test_centerline_field_above_phase_b(self):
        # Balanced 3-phase line, profile in y at z=1m, x=0; magnitude
        # should be largest near the line, smaller far away.
        line = three_phase_overhead(
            current_a=1000.0,
            span_length_m=2000.0,
            height_m=20.0,
            sag_m=0.0,
            phase_spacing_m=7.0,
        )
        f_center = field_at_point(line, (0.0, 0.0, 1.0))
        f_far = field_at_point(line, (0.0, 100.0, 1.0))
        assert f_center["B"] > 5 * f_far["B"]

    def test_balanced_line_has_finite_field(self):
        line = three_phase_overhead(
            current_a=1000.0, span_length_m=2000.0, height_m=20.0,
            sag_m=0.0, phase_spacing_m=7.0,
        )
        # Field at a typical residential exposure: 30 m offset, 1 m above ground
        f = field_at_point(line, (0.0, 30.0, 1.0))
        # Should be on the order of microtesla
        assert 1e-7 < f["B"] < 1e-3


class Test400kVReferenceCase:
    """400 kV double-conductor bundle, twin shield wires — paper §400 kV.

    Geometry (from the JS reference's create400kVTestCase):
      L1 conductors: y = ±10.2 m bundle pair, z_max=20, z_min=12.5, I=960∠-30°
      L2 conductors: y = ±0.2 m bundle pair, same heights, I=960∠-150°
      L3 conductors: y = ±9.8 m, but JS used y=9.8 and 10.2 for L3 (same as L1 mirror)
    For a sanity test we just check field at ground level under the tower."""

    def test_ground_level_field_in_microtesla_band(self):
        line = PowerLine()
        # Three phases, simplified: single conductor per phase, real magnitudes
        # at the bundle centroids
        for I_complex, y in [
            (complex(960 * math.cos(math.radians(-30)), 960 * math.sin(math.radians(-30))), -10.0),
            (complex(960 * math.cos(math.radians(-150)), 960 * math.sin(math.radians(-150))), 0.0),
            (complex(960 * math.cos(math.radians(90)), 960 * math.sin(math.radians(90))), +10.0),
        ]:
            line.add(CatenaryConductor(
                start=(-150, y, 20.0),
                end=(+150, y, 20.0),
                z_min=12.5,
                current=I_complex,
                n_segments=12,
            ))
        # Field 1 m above ground directly under the tower centre
        f = field_at_point(line, (0.0, 0.0, 1.0))
        # Published numbers for 400 kV right-of-way: ~1–10 μT under the line
        assert 1e-7 < f["B"] < 5e-5, f"expected μT-band, got {f['B']:.3e} T"


class TestProfile:
    def test_profile_returns_steps_entries(self):
        line = _long_wire(complex(1000.0, 0.0))
        prof = field_along_axis(line, "y", -50, 50, steps=21, base_point=(0, 0, 1))
        assert len(prof) == 21
        # Symmetric → max near y=0
        max_B = max(p["B"] for p in prof)
        assert max_B == prof[10]["B"]
