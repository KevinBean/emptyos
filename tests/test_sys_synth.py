"""System app tests: synth — LLM data-synthesiser closed loop.

API smoke covers the no-LLM surface (graph topology, run list, spec validation).
The full generate→QC→human→large-scale drive is LLM-bound + long, so it's
exercised offline in tests/test_sdk_graph_pipeline.py (the graph + runner) rather
than hit live here.
"""

from __future__ import annotations

import pytest

from helpers import assert_dict_response, assert_ok


@pytest.mark.api
class TestSynthAPI:
    def test_graph_topology(self, http_client):
        data = assert_dict_response(http_client.get("/synth/api/graph"))
        ids = {n["id"] for n in data.get("nodes", [])}
        # the loop's load-bearing stations + gate + human branch all present
        assert {"generate", "qc_gate", "auto_fix", "human_review", "large_scale", "done", "escalate"} <= ids
        assert data.get("start") == "learn"

    def test_graph_has_qc_gate_routing(self, http_client):
        data = assert_dict_response(http_client.get("/synth/api/graph"))
        gate = next(n for n in data["nodes"] if n["id"] == "qc_gate")
        tos = {t["to"] for t in gate.get("transitions", [])}
        assert {"human_review", "auto_fix", "escalate"} <= tos
        # gate routes via auto transitions only (never landed on)
        assert all(t["kind"] == "auto" for t in gate["transitions"])

    def test_human_review_is_a_choice_node(self, http_client):
        data = assert_dict_response(http_client.get("/synth/api/graph"))
        hr = next(n for n in data["nodes"] if n["id"] == "human_review")
        assert hr.get("data", {}).get("human") is True
        labels = {t["label"] for t in hr.get("transitions", [])}
        assert {"Approve", "Reject"} <= labels

    def test_start_rejects_empty_description(self, http_client):
        r = http_client.post("/synth/api/runs", json={"spec": {"description": ""}})
        data = assert_dict_response(r)
        assert data.get("error")
        assert "description" in data["error"].lower()

    def test_runs_list_returns_shape(self, http_client):
        data = assert_dict_response(http_client.get("/synth/api/runs"))
        assert "runs" in data
        assert isinstance(data["runs"], list)

    def test_decision_on_missing_run(self, http_client):
        r = http_client.post("/synth/api/runs/nope-xyz/decision", json={"decision": "approve"})
        data = assert_dict_response(r)
        assert data.get("error")


@pytest.mark.interactive
class TestSynthUI:
    def test_page_loads(self, page, base_url):
        page.goto(base_url + "/synth/")
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").first.is_visible()

    def test_graph_canvas_renders_nodes(self, page, base_url):
        page.goto(base_url + "/synth/")
        page.wait_for_load_state("networkidle")
        # the SVG canvas should paint node rects from /api/graph
        page.wait_for_selector("#canvas rect", timeout=5000)
        assert page.locator("#canvas rect").count() >= 8

    def test_run_button_present(self, page, base_url):
        page.goto(base_url + "/synth/")
        page.wait_for_load_state("networkidle")
        assert page.locator("#run-btn").is_visible()
