"""System app tests: Interference — vault-backed studies + Biot-Savart analysis."""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


@pytest.mark.api
class TestInterferenceAPI:
    def test_create_study_round_trip(self, http_client):
        name = _uniq("400kv-corridor")
        created = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": name},
        ))
        assert created["ok"] is True
        sid = created["id"]
        # Default geometry = three-phase 400 kV horizontal seed
        fetched = assert_ok(http_client.get(f"/interference/api/studies/{sid}"))
        study = fetched["study"]
        assert study["name"] == name
        assert len(study["conductors"]) == 3
        # Phase angles 0, -120, 120
        phases = sorted(c["current_phase_deg"] for c in study["conductors"])
        assert phases == [-120, 0, 120]

    def test_create_rejects_empty_name(self, http_client):
        resp = http_client.post("/interference/api/studies", json={"name": "  "})
        assert "error" in resp.json()

    def test_list_contains_created_study(self, http_client):
        name = _uniq("listed")
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": name},
        ))["id"]
        listing = assert_ok(http_client.get("/interference/api/studies"))
        ids = [s["id"] for s in listing["studies"]]
        assert sid in ids

    def test_patch_updates_frequency(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("freq"), "frequency_hz": 50},
        ))["id"]
        patched = assert_ok(http_client.patch(
            f"/interference/api/studies/{sid}", json={"frequency_hz": 60.0},
        ))
        assert patched["ok"] is True
        study = assert_ok(http_client.get(f"/interference/api/studies/{sid}"))["study"]
        assert float(study["frequency_hz"]) == 60.0

    def test_patch_rejects_field_outside_whitelist(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("whitelist")},
        ))["id"]
        bad = http_client.patch(
            f"/interference/api/studies/{sid}", json={"backdoor_field": 42},
        ).json()
        assert "error" in bad

    def test_profile_analysis_returns_samples(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("profile")},
        ))["id"]
        result = assert_ok(http_client.post(
            f"/interference/api/studies/{sid}/profile",
            json={"axis": "y", "min": -20, "max": 20, "steps": 21,
                  "x": 0, "y": 0, "z": 1.5},
        ))
        assert result["ok"] is True
        assert result["axis"] == "y"
        assert result["n_samples"] == 21
        assert result["max_B"] > 0  # 1000 A balanced 3-phase produces a field
        # Sample shape — each entry has B + position
        for s in result["samples"][:3]:
            assert "B" in s and "position" in s

    def test_grid_analysis_returns_flat_grid(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("grid")},
        ))["id"]
        result = assert_ok(http_client.post(
            f"/interference/api/studies/{sid}/grid",
            json={"plane": "yz", "fixed": 0,
                  "r1_min": -10, "r1_max": 10, "r1_steps": 5,
                  "r2_min": 0, "r2_max": 10, "r2_steps": 5},
        ))
        assert result["ok"] is True
        assert len(result["grid"]) == 25
        assert result["max_B"] > 0

    def test_delete_archives_and_removes_from_list(self, http_client):
        sid = assert_ok(http_client.post(
            "/interference/api/studies", json={"name": _uniq("doomed")},
        ))["id"]
        deleted = assert_ok(http_client.delete(f"/interference/api/studies/{sid}"))
        assert deleted["ok"] is True
        listing = assert_ok(http_client.get("/interference/api/studies"))
        assert sid not in [s["id"] for s in listing["studies"]]

    def test_app_page_loads(self, http_client):
        resp = http_client.get("/interference/")
        assert resp.status_code == 200
