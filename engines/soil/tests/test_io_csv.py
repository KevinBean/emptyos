"""Tests for CSV measurement load + JSON result export."""

import json
from pathlib import Path

import pytest
from engines.soil.geometry import ElectrodeArray
from engines.soil.inverse import InversionConfig, Measurement, invert
from engines.soil.io_csv import (
    load_wenner_csv,
    measurements_to_csv,
    result_to_json,
    save_result_json,
)


REFERENCE_CSV = """active,spacing_m,apparent_resistance_ohm,apparent_resistivity_ohm_m,comment
1,2.0,15.1197,190.0,
1,4.0,7.2813,183.0,
1,8.0,2.9245,147.0,
1,16.0,1.1738,118.0,
1,32.0,0.5322,107.0,
"""


def test_load_wenner_csv_round_trip(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(REFERENCE_CSV, encoding="utf-8")
    measurements = load_wenner_csv(src)
    assert len(measurements) == 5
    assert measurements[0].array.kind == "wenner"
    assert measurements[0].array.spacings == (2.0,)
    assert measurements[0].apparent_resistivity == pytest.approx(190.0)
    assert all(m.active for m in measurements)


def test_load_csv_computes_missing_rho_from_resistance(tmp_path: Path):
    """If apparent_resistivity is empty, compute it from K_g · R."""
    src = tmp_path / "in.csv"
    src.write_text(
        "active,spacing_m,apparent_resistance_ohm,apparent_resistivity_ohm_m,comment\n"
        "1,2.0,15.1197,,\n",
        encoding="utf-8",
    )
    measurements = load_wenner_csv(src)
    # K_g · R = 2π · 2 · 15.1197 ≈ 190
    assert measurements[0].apparent_resistivity == pytest.approx(190.0, abs=0.5)


def test_load_csv_rejects_missing_both(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(
        "active,spacing_m,apparent_resistance_ohm,apparent_resistivity_ohm_m,comment\n"
        "1,2.0,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must supply"):
        load_wenner_csv(src)


def test_load_csv_inactive_flag(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(
        "active,spacing_m,apparent_resistance_ohm,apparent_resistivity_ohm_m,comment\n"
        "1,2.0,,190.0,kept\n"
        "0,4.0,,183.0,excluded outlier\n",
        encoding="utf-8",
    )
    measurements = load_wenner_csv(src)
    assert measurements[0].active
    assert not measurements[1].active
    assert measurements[1].comment == "excluded outlier"


def test_round_trip_csv(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(REFERENCE_CSV, encoding="utf-8")
    ms = load_wenner_csv(src)
    out = tmp_path / "out.csv"
    measurements_to_csv(out, ms)
    ms2 = load_wenner_csv(out)
    assert len(ms2) == len(ms)
    for a, b in zip(ms, ms2):
        assert a.array.spacings == b.array.spacings
        assert a.apparent_resistivity == pytest.approx(b.apparent_resistivity, rel=1e-4)


def test_result_to_json_includes_all_diagnostics(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(REFERENCE_CSV, encoding="utf-8")
    ms = load_wenner_csv(src)
    result = invert(ms, InversionConfig(n_layers=2))
    payload = result_to_json(result, measurements=ms, site="East Central Substation")

    assert payload["site"] == "East Central Substation"
    assert len(payload["soil_model"]["layers"]) == 2
    assert payload["soil_model"]["layers"][0]["top_depth_m"] == 0.0
    assert payload["interfaces"][0]["between"] == [1, 2]
    assert "rms_error_pct" in payload
    assert "convergence" in payload
    assert "jacobian" in payload
    assert payload["jacobian"]["is_well_conditioned"] in (True, False)
    assert len(payload["per_point"]) == 5
    assert payload["per_point"][0]["a_m"] == 2.0


def test_save_result_json(tmp_path: Path):
    src = tmp_path / "in.csv"
    src.write_text(REFERENCE_CSV, encoding="utf-8")
    ms = load_wenner_csv(src)
    result = invert(ms, InversionConfig(n_layers=2))
    out = tmp_path / "result.json"
    save_result_json(out, result, measurements=ms, site="Test Site")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["site"] == "Test Site"
