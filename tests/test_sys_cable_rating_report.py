"""System tests — cable rating report endpoint.

Hits the running daemon on localhost:9000. Requires `/cables/api/library`
to return at least one preset (true on any default install — the cables
app ships a library at 30_Resources/cables/library.json).

Skips PDF assertions automatically when Playwright isn't installed —
PDF rendering is opt-in per the reports app contract.
"""

from __future__ import annotations

import pytest
import httpx


@pytest.fixture(scope="module")
def first_preset(http_client: httpx.Client) -> str:
    """Pick the first library preset id; skip the suite if the library
    is empty (e.g. a fresh clone with no cables seed)."""
    resp = http_client.get("/cables/api/library")
    if resp.status_code != 200:
        pytest.skip(f"library endpoint returned {resp.status_code}")
    items = (resp.json() or {}).get("library") or []
    if not items:
        pytest.skip("cable library is empty — nothing to rate")
    return items[0]["id"]


def _rating_payload(preset_id: str) -> dict:
    return {
        "cable_preset": preset_id,
        "installation": "direct_buried",
        "bonding": "single_point",
        "spacing_mode": "trefoil",
        "burial_depth_m": 1.0,
        "grouped_cables": 1,
        "soil_thermal_resistivity_kmw": 1.0,
        "ambient_temperature_c": 20,
        "frequency_hz": 50,
    }


class TestRatingCalculateEnvelope:
    """Phase 1 — verify the engine envelope is now surfaced fully."""

    def test_surfaces_construction_and_electrical_blocks(
        self, http_client: httpx.Client, first_preset: str
    ):
        r = http_client.post("/cables/api/rating/calculate", json=_rating_payload(first_preset))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok"), data
        # Phase-1 envelope additions:
        assert "construction" in data, "construction block missing"
        assert "electrical" in data, "electrical block missing"
        assert "results" in data, "results block missing"
        # Per-cable design fields lifted from the library:
        c = data["construction"]
        assert c.get("id") == first_preset
        assert "rated_voltage_kv" in c
        assert "conductor_material" in c
        # Engine breakdown — analytic always populates these:
        results = data["results"]
        assert results.get("ampacity_a") is not None
        assert results.get("conductor_temperature_c") is not None
        # T4 + lambda1 are bedrock IEC 60287 outputs; require them.
        assert results.get("T4") is not None
        assert results.get("lambda1") is not None


class TestRatingReportHTML:
    """Phase 2 — HTML report renders, has CYMCAP-equivalent sections."""

    def test_html_response_carries_expected_sections(
        self, http_client: httpx.Client, first_preset: str
    ):
        payload = _rating_payload(first_preset)
        payload["format"] = "html"
        r = http_client.post("/cables/api/rating/report", json=payload)
        assert r.status_code == 200, r.text
        ct = r.headers.get("content-type", "")
        assert "text/html" in ct, f"expected HTML, got {ct}"
        body = r.text
        # The eight CYMCAP-equivalent section headers from the plan.
        for heading in [
            "Cable construction",
            "Electrical properties",
            "Installation",
            "Results",
            "Sheath voltage",
            "Engine notes",
            "Not modelled in this report",
        ]:
            assert heading in body, f"missing section: {heading!r}"
        # Honesty footnote enumerates the gaps:
        for gap in ["sheath surface temperature", "Mutual heating", "DLF", "Backfill"]:
            assert gap.lower() in body.lower(), f"footnote missing gap: {gap}"

    def test_unknown_format_rejected(
        self, http_client: httpx.Client, first_preset: str
    ):
        payload = _rating_payload(first_preset)
        payload["format"] = "docx"
        r = http_client.post("/cables/api/rating/report", json=payload)
        assert r.status_code == 400
        assert "unknown format" in r.json().get("error", "").lower()


class TestRatingReportPDF:
    """Phase 2 — PDF render. Skips automatically when Playwright is missing
    (the endpoint returns 503 with the install hint)."""

    def test_pdf_renders_or_signals_missing_dep(
        self, http_client: httpx.Client, first_preset: str
    ):
        payload = _rating_payload(first_preset)
        payload["format"] = "pdf"
        r = http_client.post("/cables/api/rating/report", json=payload)
        if r.status_code == 503:
            err = r.json().get("error", "")
            assert "playwright" in err.lower(), err
            pytest.skip("Playwright not installed — friendly 503 confirmed")
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        body = r.content
        assert len(body) > 5_000, f"PDF suspiciously small: {len(body)} bytes"
        assert body[:4] == b"%PDF", "response is not a PDF magic-number"
