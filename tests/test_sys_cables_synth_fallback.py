"""apps/cables — synth-fallback fields persist on cable creation.

When a cable is created without `library_id`, the rating engine falls back
to a synthesized library entry built from `conductor_class`,
`conductor_csa_mm2`, and `rated_voltage_kv` on the cable note. The
cable-add form exposes these as inputs; this test asserts they survive
the POST → vault → GET round-trip so user input actually reaches
`_resolve_library_entry` instead of silently dropping to defaults.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import TEST_PREFIX

_RUN_ID = uuid.uuid4().hex[:6]
PROJECT_NAME = f"{TEST_PREFIX}synth-{_RUN_ID}"


@pytest.fixture(scope="module")
def project_id(http_client: httpx.Client):
    proj = http_client.post("/cables/api/projects", json={
        "name": PROJECT_NAME,
        "frequency_hz": 50,
        "system_voltage_kv": 11.0,
        "phases": 3,
    })
    assert proj.status_code == 200, proj.text
    pid = proj.json()["id"]
    yield pid
    http_client.delete(f"/cables/api/projects/{pid}")


@pytest.mark.api
def test_synth_fallback_fields_persist_on_create(http_client, project_id):
    res = http_client.post(f"/cables/api/projects/{project_id}/cables", json={
        "label": f"{TEST_PREFIX}synth-cable",
        # No library_id — forces synth-fallback path on schedule run.
        "length_m": 100.0,
        "installation": "direct_buried",
        "bonding": "single_point",
        "conductor_class": "5",
        "conductor_csa_mm2": 95,
        "rated_voltage_kv": 22,
    }).json()
    assert res.get("ok"), res
    cable_id = res["id"]

    cables = http_client.get(f"/cables/api/projects/{project_id}/cables").json()["cables"]
    cable = next((c for c in cables if c["id"] == cable_id), None)
    assert cable is not None

    # Vault props arrive as strings — coerce.
    assert int(cable["conductor_class"]) == 5
    assert float(cable["conductor_csa_mm2"]) == 95.0
    assert float(cable["rated_voltage_kv"]) == 22.0


@pytest.mark.api
def test_synth_fallback_omitted_fields_stay_none(http_client, project_id):
    """Caller silence ⇒ frontmatter is None, engine applies its own defaults."""
    res = http_client.post(f"/cables/api/projects/{project_id}/cables", json={
        "label": f"{TEST_PREFIX}synth-bare",
        "length_m": 50.0,
    }).json()
    assert res.get("ok"), res
    cable_id = res["id"]

    cables = http_client.get(f"/cables/api/projects/{project_id}/cables").json()["cables"]
    cable = next((c for c in cables if c["id"] == cable_id), None)
    assert cable is not None
    # None / missing — engine fallback (2 / 240 / 11) applies, not an
    # accidentally-frozen literal.
    assert cable.get("conductor_class") in (None, "")
    assert cable.get("conductor_csa_mm2") in (None, "")
    assert cable.get("rated_voltage_kv") in (None, "")
