"""System tests: reliability — IEEE 493 study CRUD + calculate + Annex Q library.

Studies are vault notes under 30_Resources/EmptyOS/reliability/. Tests use the
TEST_PREFIX in titles and delete what they create, so reruns stay clean.
The reliability math itself is covered offline by tests/test_eng_reliability.py;
these tests exercise the HTTP surface + persistence.
"""

from __future__ import annotations

import re

import pytest

from helpers import TEST_PREFIX, assert_ok


def _seed_model(study_id: str, title: str) -> dict:
    """A small system: 2 redundant transformers (parallel) + a feeder cable + CB."""
    return {
        "id": study_id, "title": title,
        "operating_hours": 8760, "downtime_cost": 50000,
        "components": [
            {"name": "Transformer 2.5 MVA", "kind": "standard", "lambda": 0.01, "lambda_r": 6.0, "count": 2, "config": "parallel"},
            {"name": "Feeder Cable 11kV", "kind": "cable", "base_lambda": 0.1, "base_lambda_r": 5.0, "length_m": 500},
            {"name": "Main Circuit Breaker", "kind": "breaker", "base_lambda": 1.0, "base_lambda_r": 10.0, "quantity": 1, "failure_mode": "failed_in_service"},
        ],
    }


def _fresh(client, title: str, model: dict | None = None) -> str:
    sid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    client.delete(f"/reliability/api/studies/{sid}")
    r = assert_ok(client.post("/reliability/api/studies", json={"title": title}))
    sid = r["id"]
    if model is not None:
        m = dict(model)
        m["id"] = sid
        assert_ok(client.put(f"/reliability/api/studies/{sid}", json={"model": m}))
    return sid


@pytest.mark.api
class TestReliabilityAPI:
    def test_list_returns_dict(self, http_client):
        data = assert_ok(http_client.get("/reliability/api/studies"))
        assert isinstance(data["studies"], list)

    def test_create_and_get(self, http_client):
        sid = _fresh(http_client, f"{TEST_PREFIX}create")
        try:
            data = assert_ok(http_client.get(f"/reliability/api/studies/{sid}"))
            assert data["model"]["id"] == sid
            assert data["model"]["components"] == []
            assert data["result"]["availability"] == 1.0   # empty = perfect
        finally:
            http_client.delete(f"/reliability/api/studies/{sid}")

    def test_duplicate_create_409(self, http_client):
        sid = _fresh(http_client, f"{TEST_PREFIX}dup")
        try:
            r = http_client.post("/reliability/api/studies", json={"title": f"{TEST_PREFIX}dup"})
            assert r.status_code == 409, r.text
        finally:
            http_client.delete(f"/reliability/api/studies/{sid}")

    def test_update_persists_and_computes(self, http_client):
        sid = _fresh(http_client, f"{TEST_PREFIX}upd", _seed_model("x", "x"))
        try:
            data = assert_ok(http_client.get(f"/reliability/api/studies/{sid}"))
            assert len(data["model"]["components"]) == 3
            res = data["result"]
            assert 0.0 < res["availability"] < 1.0
            assert res["total_lambda_r"] > 0
            # component analysis has one row per component, impacts sum ~100%
            assert len(res["components"]) == 3
            assert abs(sum(c["impact_pct"] for c in res["components"]) - 100.0) < 0.01
        finally:
            http_client.delete(f"/reliability/api/studies/{sid}")

    def test_calculate_endpoint(self, http_client):
        sid = _fresh(http_client, f"{TEST_PREFIX}calc", _seed_model("x", "x"))
        try:
            data = assert_ok(http_client.post(f"/reliability/api/studies/{sid}/calculate", json={}))
            assert data["result"]["availability"] < 1.0
        finally:
            http_client.delete(f"/reliability/api/studies/{sid}")

    def test_adhoc_calculate_no_persistence(self, http_client):
        # Stateless compute: a single breaker at 32% mode → λr = 10*0.32 = 3.2
        body = {"components": [
            {"name": "CB", "kind": "breaker", "base_lambda": 1.0, "base_lambda_r": 10.0,
             "quantity": 1, "failure_mode": "failed_in_service"}],
            "operating_hours": 8760, "downtime_cost": 1000}
        data = assert_ok(http_client.post("/reliability/api/calculate", json=body))
        assert abs(data["result"]["total_lambda_r"] - 3.2) < 1e-6
        assert abs(data["result"]["annual_outage_cost"] - 3200.0) < 1e-3

    def test_parallel_redundancy_beats_single(self, http_client):
        single = {"components": [{"name": "T", "kind": "standard", "lambda": 0.1, "lambda_r": 8.0, "count": 1, "config": "series"}]}
        dual = {"components": [{"name": "T", "kind": "standard", "lambda": 0.1, "lambda_r": 8.0, "count": 2, "config": "parallel"}]}
        a = assert_ok(http_client.post("/reliability/api/calculate", json=single))["result"]
        b = assert_ok(http_client.post("/reliability/api/calculate", json=dual))["result"]
        assert b["availability"] > a["availability"]   # redundancy lowers λr

    def test_database_search(self, http_client):
        data = assert_ok(http_client.get("/reliability/api/database?q=battery"))
        assert data["components"]
        assert all("battery" in (c["name"] + c["category"]).lower() for c in data["components"])
        assert data["categories"]

    def test_breaker_modes(self, http_client):
        data = assert_ok(http_client.get("/reliability/api/breaker-modes"))
        assert "failed_in_service" in data["modes"]
        assert data["modes"]["failed_in_service"]["percentage"] == 32

    def test_delete(self, http_client):
        sid = _fresh(http_client, f"{TEST_PREFIX}del")
        assert_ok(http_client.delete(f"/reliability/api/studies/{sid}"))
        r = http_client.get(f"/reliability/api/studies/{sid}")
        assert r.status_code == 404
