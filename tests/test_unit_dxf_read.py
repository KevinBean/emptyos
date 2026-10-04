"""Unit tests for emptyos.sdk.dxf_read.

Skips when ezdxf is absent (optional `dxf` extra). Builds a tiny DXF in memory,
writes it to a temp file, and reads it back through the public reader.
"""

import pytest

ezdxf = pytest.importorskip("ezdxf")

from emptyos.sdk.dxf_read import layer_names, read_entities  # noqa: E402


def _make_dxf(tmp_path):
    doc = ezdxf.new("R2010")
    doc.layers.add("SURVEY")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 50), dxfattribs={"layer": "SURVEY"})
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True, dxfattribs={"layer": "SURVEY"})
    msp.add_point((25, 75), dxfattribs={"layer": "0"})
    msp.add_circle((50, 50), 5, dxfattribs={"layer": "0"})
    msp.add_text("P1", dxfattribs={"insert": (5, 5), "layer": "0"})
    path = tmp_path / "survey.dxf"
    doc.saveas(str(path))
    return path


def test_reads_typed_entities(tmp_path):
    ents = read_entities(_make_dxf(tmp_path))
    kinds = sorted(e["kind"] for e in ents)
    assert kinds == ["circle", "line", "point", "polyline", "text"]

    line = next(e for e in ents if e["kind"] == "line")
    assert line["coords"] == [[0.0, 0.0], [100.0, 50.0]]

    poly = next(e for e in ents if e["kind"] == "polyline")
    assert poly["meta"]["closed"] is True
    assert poly["coords"][0] == [0.0, 0.0]

    circle = next(e for e in ents if e["kind"] == "circle")
    assert circle["coords"] == [[50.0, 50.0]]
    assert circle["meta"]["r"] == 5.0

    text = next(e for e in ents if e["kind"] == "text")
    assert text["meta"]["text"] == "P1"

    # Every entity carries its layer.
    assert all("layer" in e["meta"] for e in ents)


def test_layer_filter(tmp_path):
    ents = read_entities(_make_dxf(tmp_path), layers=["SURVEY"])
    assert {e["meta"]["layer"] for e in ents} == {"SURVEY"}
    assert sorted(e["kind"] for e in ents) == ["line", "polyline"]


def test_layer_names(tmp_path):
    names = layer_names(_make_dxf(tmp_path))
    assert "SURVEY" in names and "0" in names


def test_unsupported_format(tmp_path):
    bad = tmp_path / "x.stl"
    bad.write_text("solid")
    with pytest.raises(ValueError):
        read_entities(bad)
