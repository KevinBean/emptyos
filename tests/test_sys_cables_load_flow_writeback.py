"""apps/cables — load-flow writeback end-to-end smoke test.

Closes the missing-coverage gap on `_writeback_load_flow` flagged by the
2026-05-03 cable-reticulation brief. The refactor that extracted
`_persist_topology_writeback` was mechanical, but neither path had a live
integration test until the breaker-rating one shipped on the SC side. This
mirrors that test's shape for load flow.

Builds a small radial feeder (22 kV slack → 500 m line → 11 kV bus carrying
a 1 MW / 0.3 MVAr load), runs the load flow with `writeback=true`, then
re-fetches nodes/edges and asserts the `lf_*` fields persisted.

Skipped when the daemon isn't on localhost:9000.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import TEST_PREFIX

_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}lf-writeback-{_RUN_ID}"


@pytest.fixture(scope="module")
def project_id(http_client: httpx.Client):
    """22 kV slack → cable → 11 kV bus with a load."""
    proj = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "frequency_hz": 50,
        "slack_voltage_kv": 22.0,
        "system_voltage_kv": 22.0,
        "source_mva_3ph": 250.0,
        "phases": 3,
    })
    assert proj.status_code == 200, proj.text
    pid = proj.json()["id"]

    n1 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "src-22kv",
        "label": "Source 22kV",
        "kind": "substation",
        "voltage_kv": 22.0,
        "is_slack": True,
    })
    assert n1.status_code == 200, n1.text

    # Downstream bus carries a real load so the load flow produces non-zero
    # currents + a measurable voltage drop.
    n2 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "load-11kv",
        "label": "Load 11kV",
        "kind": "bus",
        "voltage_kv": 11.0,
        "p_load_kw": 1000.0,
        "q_load_kvar": 300.0,
    })
    assert n2.status_code == 200, n2.text

    edge = http_client.post(f"/cables/api/projects/{pid}/edges", json={
        "id": "feeder",
        "from_node": "src-22kv",
        "to_node": "load-11kv",
        "length_m": 500.0,
        "r_ohm_per_km": 0.10,
        "x_ohm_per_km": 0.08,
    })
    assert edge.status_code == 200, edge.text

    yield pid

    http_client.delete(f"/cables/api/projects/{pid}")


@pytest.mark.api
def test_load_flow_no_writeback(http_client, project_id):
    res = http_client.post(
        f"/cables/api/projects/{project_id}/run-load-flow",
        json={"writeback": False},
    ).json()
    assert res.get("ok"), res
    assert res["result"]["converged"] is True
    wb = res.get("writeback") or {}
    assert wb.get("persisted") is False
    assert wb.get("n_writes") == 0


@pytest.mark.api
def test_writeback_persists_lf_fields(http_client, project_id):
    res = http_client.post(
        f"/cables/api/projects/{project_id}/run-load-flow",
        json={"writeback": True},
    ).json()
    assert res.get("ok"), res
    assert res["result"]["converged"] is True
    wb = res.get("writeback") or {}
    assert wb.get("persisted") is True
    # 2 buses + 1 edge → 3 writes minimum.
    assert wb.get("n_writes") >= 3

    nodes = http_client.get(f"/cables/api/projects/{project_id}/nodes").json()["nodes"]
    edges = http_client.get(f"/cables/api/projects/{project_id}/edges").json()["edges"]
    nodes_by_id = {n["id"]: n for n in nodes}
    edges_by_id = {e["id"]: e for e in edges}

    # Edge — current, voltage drop, timestamp persisted.
    feeder = edges_by_id["feeder"]
    assert "lf_current_a" in feeder
    assert float(feeder["lf_current_a"]) > 0.0
    assert "lf_voltage_drop_pct" in feeder
    assert float(feeder["lf_voltage_drop_pct"]) > 0.0
    assert "lf_at" in feeder

    # Slack bus — voltage pinned at 1.0 pu.
    slack = nodes_by_id["src-22kv"]
    assert "lf_voltage_kv" in slack
    assert "lf_voltage_pu" in slack
    assert abs(float(slack["lf_voltage_pu"]) - 1.0) < 1e-3
    assert "lf_at" in slack

    # Load bus — voltage drops below 1.0 pu under load.
    load = nodes_by_id["load-11kv"]
    assert "lf_voltage_kv" in load
    assert "lf_voltage_pu" in load
    assert float(load["lf_voltage_pu"]) < 1.0
    assert "lf_at" in load
