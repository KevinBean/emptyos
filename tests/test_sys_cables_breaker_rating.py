"""apps/cables — short-circuit breaker-rating end-to-end smoke test.

Builds a real-flavour MV feeder (22 kV slack → 240 mm² Cu XLPE 500 m → 11 kV
distribution bus) where the 11 kV switchgear is intentionally under-rated.
Runs the short-circuit calc with `writeback=true` and asserts:

  - the response carries a `violations` list naming the under-rated bus
  - sc_isc_3ph_ka, sc_utilization_pct, sc_ok=False are persisted on the
    violator's node note
  - a slack-side bus with adequate rating is reported `sc_ok=True`

Skipped when the daemon isn't on localhost:9000.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import TEST_PREFIX

_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}sc-breaker-{_RUN_ID}"


@pytest.fixture(scope="module")
def project_id(http_client: httpx.Client):
    """Create slack 22 kV → cable → low-rated 11 kV switchgear bus."""
    proj = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "frequency_hz": 50,
        "slack_voltage_kv": 22.0,
        "system_voltage_kv": 22.0,
        "source_mva_3ph": 250.0,  # stiff network
        "phases": 3,
    })
    assert proj.status_code == 200, proj.text
    pid = proj.json()["id"]

    # Slack bus — substation MV switchgear, generously rated 25 kA.
    n1 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "sub-22kv",
        "label": "Substation 22kV",
        "kind": "substation",
        "voltage_kv": 22.0,
        "is_slack": True,
        "switchgear_rating_ka": 25.0,
    })
    assert n1.status_code == 200, n1.text

    # Downstream bus — 11 kV distribution switchgear intentionally under-rated.
    # With a 250 MVA stiff source on the 22 kV side and a short low-impedance
    # cable, the fault level easily exceeds 8 kA.
    n2 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "dist-11kv",
        "label": "Distribution 11kV",
        "kind": "bus",
        "voltage_kv": 11.0,
        "switchgear_rating_ka": 4.0,  # under-rated on purpose
    })
    assert n2.status_code == 200, n2.text

    # Edge — short, low-impedance feeder so the per-unit fault stays high.
    edge = http_client.post(f"/cables/api/projects/{pid}/edges", json={
        "id": "feeder",
        "from_node": "sub-22kv",
        "to_node": "dist-11kv",
        "length_m": 200.0,
        "r_ohm_per_km": 0.10,
        "x_ohm_per_km": 0.08,
    })
    assert edge.status_code == 200, edge.text

    yield pid

    http_client.delete(f"/cables/api/projects/{pid}")


@pytest.mark.api
def test_short_circuit_returns_violation(http_client, project_id):
    res = http_client.post(
        f"/cables/api/projects/{project_id}/run-short-circuit",
        json={"writeback": False},
    ).json()
    assert res.get("ok"), res
    violations = res.get("violations") or []
    assert len(violations) == 1, f"expected 1 violation, got {violations}"
    v = violations[0]
    assert v["node_id"] == "dist-11kv"
    assert v["isc_3ph_ka"] > v["switchgear_rating_ka"]
    assert v["utilization_pct"] > 100.0


@pytest.mark.api
def test_writeback_persists_sc_fields(http_client, project_id):
    res = http_client.post(
        f"/cables/api/projects/{project_id}/run-short-circuit",
        json={"writeback": True},
    ).json()
    assert res.get("ok"), res
    wb = res.get("writeback") or {}
    assert wb.get("persisted") is True
    assert wb.get("n_writes") >= 2

    # Re-fetch nodes; the violator should carry sc_ok=False, the slack sc_ok=True.
    nodes = http_client.get(f"/cables/api/projects/{project_id}/nodes").json()["nodes"]
    by_id = {n["id"]: n for n in nodes}

    violator = by_id["dist-11kv"]
    assert "sc_isc_3ph_ka" in violator and float(violator["sc_isc_3ph_ka"]) > 4.0
    assert "sc_z_thev_ohm" in violator
    assert "sc_at" in violator
    # Vault props arrive as strings — coerce.
    assert float(violator["sc_utilization_pct"]) > 100.0
    assert str(violator["sc_ok"]).lower() in ("false", "0")

    slack = by_id["sub-22kv"]
    # Slack node has adequate rating; sc_ok should be True.
    assert "sc_isc_3ph_ka" in slack
    assert str(slack["sc_ok"]).lower() in ("true", "1")
    assert float(slack["sc_utilization_pct"]) <= 100.0
