"""System app tests: Lightning — vault-backed studies + rolling-sphere analysis."""

from __future__ import annotations

import time

import pytest

from helpers import TEST_PREFIX, assert_ok


def _uniq(stem: str) -> str:
    return f"{TEST_PREFIX}{stem}-{int(time.time() * 1000)}"


_TWO_MASTS = [
    {"x": 0.0,  "height": 25.0, "label": "M1"},
    {"x": 30.0, "height": 25.0, "label": "M2"},
]


@pytest.mark.api
class TestLightningAPI:
    def test_create_study_round_trip(self, http_client):
        name = _uniq("substation-roof")
        created = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": name, "lpl": "II", "terminals": _TWO_MASTS},
        ))
        assert created["ok"] is True
        sid = created["id"]
        fetched = assert_ok(http_client.get(f"/lightning/api/studies/{sid}"))
        study = fetched["study"]
        assert study["name"] == name
        assert study["lpl"] == "II"
        assert len(study["terminals"]) == 2
        # Analysis runs eagerly on GET
        analysis = fetched["analysis"]
        assert analysis["lpl"] == "II"
        assert analysis["R"] > 0  # rolling-sphere radius for LPL II
        assert 0.0 <= analysis["coverage_fraction"] <= 1.0

    def test_create_rejects_empty_name(self, http_client):
        resp = http_client.post("/lightning/api/studies", json={"name": ""})
        assert "error" in resp.json()

    def test_list_contains_created_study(self, http_client):
        name = _uniq("listed")
        sid = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": name, "terminals": _TWO_MASTS},
        ))["id"]
        listing = assert_ok(http_client.get("/lightning/api/studies"))
        rows = listing["studies"]
        match = next((s for s in rows if s["id"] == sid), None)
        assert match is not None
        assert match["n_terminals"] == 2

    def test_patch_updates_lpl_and_terminals(self, http_client):
        sid = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("patch"), "lpl": "II", "terminals": _TWO_MASTS},
        ))["id"]
        new_terms = _TWO_MASTS + [{"x": 60.0, "height": 25.0, "label": "M3"}]
        patched = assert_ok(http_client.patch(
            f"/lightning/api/studies/{sid}",
            json={"lpl": "I", "terminals": new_terms},
        ))
        assert patched["ok"] is True
        study = assert_ok(http_client.get(f"/lightning/api/studies/{sid}"))["study"]
        assert study["lpl"] == "I"
        assert len(study["terminals"]) == 3

    def test_patch_rejects_field_outside_whitelist(self, http_client):
        sid = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("whitelist"), "terminals": _TWO_MASTS},
        ))["id"]
        bad = http_client.patch(
            f"/lightning/api/studies/{sid}", json={"backdoor_field": 42},
        ).json()
        assert "error" in bad

    def test_analyse_returns_rolling_sphere(self, http_client):
        sid = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("analyse"), "lpl": "II", "terminals": _TWO_MASTS},
        ))["id"]
        result = assert_ok(http_client.post(f"/lightning/api/studies/{sid}/analyse"))
        assert result["ok"] is True
        a = result["analysis"]
        assert a["lpl"] == "II"
        assert a["R"] > 0
        assert isinstance(a["samples"], list) and len(a["samples"]) > 0
        # Each terminal preserved through analysis
        assert len(a["terminals"]) == 2

    def test_lpl_radius_ordering(self, http_client):
        # Smaller LPL number → smaller rolling sphere R (more stringent).
        sid_i = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("lpl-I"), "lpl": "I", "terminals": _TWO_MASTS},
        ))["id"]
        sid_iv = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("lpl-IV"), "lpl": "IV", "terminals": _TWO_MASTS},
        ))["id"]
        a_i = assert_ok(http_client.get(f"/lightning/api/studies/{sid_i}"))["analysis"]
        a_iv = assert_ok(http_client.get(f"/lightning/api/studies/{sid_iv}"))["analysis"]
        assert a_i["R"] < a_iv["R"]

    def test_delete_archives_and_removes_from_list(self, http_client):
        sid = assert_ok(http_client.post(
            "/lightning/api/studies",
            json={"name": _uniq("doomed"), "terminals": _TWO_MASTS},
        ))["id"]
        deleted = assert_ok(http_client.delete(f"/lightning/api/studies/{sid}"))
        assert deleted["ok"] is True
        listing = assert_ok(http_client.get("/lightning/api/studies"))
        assert sid not in [s["id"] for s in listing["studies"]]

    def test_app_page_loads(self, http_client):
        resp = http_client.get("/lightning/")
        assert resp.status_code == 200
