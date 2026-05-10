"""apps/cables — list_nodes / list_edges return typed values.

Regression coverage for the 2026-05-04 string-typed-frontmatter class of
bug. Vault frontmatter parses every value as a string, which broke the
SLD layout (every node treated as slack because `"false"` is JS-truthy)
and the topology canvas (`+ "1004"` produced "NaN" coords). The fix is
backend coercion of known typed fields in `list_nodes` / `list_edges`.

Tests assert that bools come back as `bool`, numerics as `int`/`float`,
and that the drag-to-move PATCH (x/y update) round-trips as numbers.

Skipped when the daemon isn't on localhost:9000.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import TEST_PREFIX

_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}topology-types-{_RUN_ID}"


@pytest.fixture(scope="module")
def project_id(http_client: httpx.Client):
    proj = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "frequency_hz": 50,
        "system_voltage_kv": 22.0,
        "phases": 3,
    })
    assert proj.status_code == 200, proj.text
    pid = proj.json()["id"]

    # Slack node — placed at integer coords; is_slack should round-trip as bool.
    n1 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "slack-22kv",
        "label": "Slack",
        "kind": "substation",
        "voltage_kv": 22.0,
        "is_slack": True,
        "x": 100,
        "y": 200,
    })
    assert n1.status_code == 200, n1.text

    # Plain bus — is_slack omitted, should default to False (not None or "").
    n2 = http_client.post(f"/cables/api/projects/{pid}/nodes", json={
        "id": "bus-11kv",
        "label": "Bus",
        "kind": "bus",
        "voltage_kv": 11.0,
        "p_load_kw": 1000.0,
        "x": 400,
        "y": 250,
    })
    assert n2.status_code == 200, n2.text

    edge = http_client.post(f"/cables/api/projects/{pid}/edges", json={
        "id": "feeder",
        "from_node": "slack-22kv",
        "to_node": "bus-11kv",
        "length_m": 500.0,
        "r_ohm_per_km": 0.10,
        "x_ohm_per_km": 0.08,
    })
    assert edge.status_code == 200, edge.text

    yield pid

    http_client.delete(f"/cables/api/projects/{pid}")


@pytest.mark.api
def test_node_bool_fields_are_bool(http_client, project_id):
    nodes = http_client.get(f"/cables/api/projects/{project_id}/nodes").json()["nodes"]
    by_id = {n["id"]: n for n in nodes}

    slack = by_id["slack-22kv"]
    assert slack["is_slack"] is True, f"is_slack={slack['is_slack']!r} type={type(slack['is_slack'])}"
    # archived defaults to False (not present, or coerced from missing)
    assert slack.get("archived", False) is False

    bus = by_id["bus-11kv"]
    # is_slack was omitted on creation — backend default is False (bool).
    assert bus.get("is_slack", False) is False
    assert isinstance(bus.get("is_slack", False), bool)


@pytest.mark.api
def test_node_numeric_fields_are_numbers(http_client, project_id):
    nodes = http_client.get(f"/cables/api/projects/{project_id}/nodes").json()["nodes"]
    by_id = {n["id"]: n for n in nodes}

    slack = by_id["slack-22kv"]
    # Coordinates round-trip as numbers (this is what broke the SVG layout).
    assert isinstance(slack["x"], (int, float)) and not isinstance(slack["x"], bool)
    assert isinstance(slack["y"], (int, float)) and not isinstance(slack["y"], bool)
    assert slack["x"] == 100
    assert slack["y"] == 200
    assert isinstance(slack["voltage_kv"], (int, float))
    assert slack["voltage_kv"] == 22.0

    bus = by_id["bus-11kv"]
    assert isinstance(bus["p_load_kw"], (int, float))
    assert bus["p_load_kw"] == 1000.0


@pytest.mark.api
def test_edge_numeric_fields_are_numbers(http_client, project_id):
    edges = http_client.get(f"/cables/api/projects/{project_id}/edges").json()["edges"]
    by_id = {e["id"]: e for e in edges}

    feeder = by_id["feeder"]
    assert isinstance(feeder["length_m"], (int, float))
    assert feeder["length_m"] == 500.0
    assert isinstance(feeder["r_ohm_per_km"], (int, float))
    assert feeder["r_ohm_per_km"] == 0.10
    assert isinstance(feeder["x_ohm_per_km"], (int, float))
    # archived defaults to False bool (not the string "False" or None).
    assert feeder.get("archived", False) is False


@pytest.mark.api
def test_drag_to_move_persists_as_numbers(http_client, project_id):
    """Simulates the topology-canvas drag flow: PATCH x/y → re-read."""
    patch = http_client.request(
        "PATCH",
        f"/cables/api/projects/{project_id}/nodes/bus-11kv",
        json={"x": 555, "y": 333},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json().get("ok") is True

    nodes = http_client.get(f"/cables/api/projects/{project_id}/nodes").json()["nodes"]
    bus = next(n for n in nodes if n["id"] == "bus-11kv")
    assert isinstance(bus["x"], (int, float)) and not isinstance(bus["x"], bool)
    assert isinstance(bus["y"], (int, float)) and not isinstance(bus["y"], bool)
    assert bus["x"] == 555
    assert bus["y"] == 333
