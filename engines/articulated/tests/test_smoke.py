"""Smoke test — build, validate, export URDF, parse it back, assert structure.

Run via:
    "%LOCALAPPDATA%/eos/envs/cadquery-3.12/Scripts/python.exe" -m pytest engines/articulated/tests/ -v
"""

from __future__ import annotations

import sys
import math
from pathlib import Path
from xml.etree.ElementTree import fromstring

import pytest

# Repo root on sys.path so `engines.articulated` imports cleanly
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engines.articulated import (
    ArticulatedObject,
    Box,
    Cylinder,
    Fixed,
    Origin,
    Part,
    Revolute,
    Sphere,
    TestContext,
    export_urdf,
    to_urdf_xml,
)
from engines.articulated.examples.desk_lamp import build_object_model


# ─── Primitive validation ─────────────────────────────────────────────

def test_box_rejects_zero_size():
    with pytest.raises(ValueError):
        Box(size=(1, 0, 1))


def test_cylinder_rejects_negative():
    with pytest.raises(ValueError):
        Cylinder(radius=-1, length=5)


def test_sphere_rejects_zero():
    with pytest.raises(ValueError):
        Sphere(radius=0)


# ─── ArticulatedObject composition ────────────────────────────────────

def test_duplicate_part_rejected():
    m = ArticulatedObject(name="x")
    m.add_part(Part(name="p1", geometry=Box(size=(1, 1, 1))))
    with pytest.raises(ValueError, match="Duplicate part"):
        m.add_part(Part(name="p1", geometry=Box(size=(2, 2, 2))))


def test_joint_unknown_parent_rejected():
    m = ArticulatedObject(name="x")
    m.add_part(Part(name="child", geometry=Box(size=(1, 1, 1))))
    with pytest.raises(ValueError, match="parent part 'ghost' not found"):
        m.add_joint(Revolute(name="j", parent="ghost", child="child"))


def test_revolute_lower_must_be_below_upper():
    with pytest.raises(ValueError, match="lower"):
        Revolute(name="j", parent="a", child="b", lower=1.0, upper=0.5)


# ─── TestContext baseline ─────────────────────────────────────────────

def test_baseline_clean_for_valid_model():
    m = build_object_model()
    ctx = TestContext(m)
    ctx.run_baseline()
    assert ctx.ok, f"Expected clean baseline, got errors: {[e.message for e in ctx.errors]}"
    report = ctx.report()
    assert report["part_count"] == 3
    assert report["joint_count"] == 2


def test_baseline_catches_disconnected_part():
    m = ArticulatedObject(name="bad")
    m.add_part(Part(name="root", geometry=Box(size=(1, 1, 1))))
    m.add_part(Part(name="orphan", geometry=Box(size=(1, 1, 1))))
    # No joint connects orphan -> two roots
    ctx = TestContext(m)
    ctx.run_baseline()
    assert not ctx.ok
    codes = [e.code for e in ctx.errors]
    assert "ROOT_COUNT" in codes


def test_baseline_catches_zero_root():
    m = ArticulatedObject(name="cycle")
    m.add_part(Part(name="a", geometry=Box(size=(1, 1, 1))))
    m.add_part(Part(name="b", geometry=Box(size=(1, 1, 1))))
    # a -> b AND b -> a means every part is some child = 0 roots
    m.add_joint(Fixed(name="ab", parent="a", child="b"))
    m.add_joint(Fixed(name="ba", parent="b", child="a"))
    ctx = TestContext(m)
    ctx.run_baseline()
    assert not ctx.ok
    assert any(e.code == "ROOT_COUNT" for e in ctx.errors)


# ─── URDF export end-to-end ────────────────────────────────────────────

def test_urdf_xml_well_formed():
    m = build_object_model()
    xml = to_urdf_xml(m)
    root = fromstring(xml.encode("utf-8"))  # fromstring needs bytes for the XML declaration
    assert root.tag == "robot"
    assert root.attrib["name"] == "desk_lamp"

    links = root.findall("link")
    joints = root.findall("joint")
    assert len(links) == 3, f"expected 3 links, got {[l.attrib['name'] for l in links]}"
    assert len(joints) == 2, f"expected 2 joints, got {[j.attrib['name'] for j in joints]}"

    # Revolute joint must carry axis + limit
    rev = next(j for j in joints if j.attrib["name"] == "base_pan")
    assert rev.attrib["type"] == "revolute"
    assert rev.find("axis") is not None
    assert rev.find("limit") is not None


def test_urdf_file_roundtrip(tmp_path):
    m = build_object_model()
    out = export_urdf(m, tmp_path / "desk_lamp.urdf")
    assert out.exists(), f"URDF file not written: {out}"
    text = out.read_text(encoding="utf-8")
    assert '<robot name="desk_lamp"' in text
    assert '<joint name="base_pan" type="revolute">' in text
    assert '<joint name="head_tilt" type="revolute">' in text


# ─── CadQuery integration smoke (does the 3.12 venv actually work?) ────

def test_cadquery_import_works():
    """Confirms we're running inside the right Python venv. Hard fail surface for env drift."""
    import cadquery as cq
    box = cq.Workplane("XY").box(10, 20, 30)
    verts = box.val().Vertices()
    assert len(verts) == 8
