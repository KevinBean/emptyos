"""Offline unit tests for feature-pipeline's workflow-graph VIEW.

No daemon. Loads workflow_graph.py by path (it only imports decision_graph).
Guards the topology validity and the brief-state → {current, visited} mapping
that drives EOS_GRAPH highlighting.
"""

from __future__ import annotations

import importlib.util
import os

from emptyos.sdk.decision_graph import validate_graph

_PATH = os.path.join(os.path.dirname(__file__), "..", "apps", "extension", "dev",
                     "feature-pipeline", "workflow_graph.py")
_spec = importlib.util.spec_from_file_location("fp_workflow_graph_under_test", _PATH)
wfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wfg)


def test_graph_is_structurally_valid():
    g = wfg.build_feature_pipeline_graph()
    errors = [e for e in validate_graph(g) if not e.startswith("warn:")]
    assert errors == [], errors


def test_graph_has_the_loop_and_both_terminals():
    g = wfg.build_feature_pipeline_graph()
    assert {"brief", "research", "design", "implement", "conf_gate", "fix",
            "dogfood", "review", "released", "blocked"} == set(g.nodes)
    # the conformance gate routes three ways, all auto
    gate = g.nodes["conf_gate"]
    assert {t.to for t in gate.transitions} == {"dogfood", "fix", "blocked"}
    assert all(t.kind == "auto" for t in gate.transitions)
    # the repair loop goes back to implement
    assert g.nodes["fix"].transitions[0].to == "implement"
    # terminals
    assert g.nodes["released"].kind == "end"
    assert g.nodes["blocked"].kind == "end"
    assert g.nodes["blocked"].data.get("fail") is True


def test_brief_path_happy_progression():
    assert wfg.brief_path("queued", None, [])["current"] == "research"
    assert wfg.brief_path("researched", None, [])["current"] == "design"
    assert wfg.brief_path("designed", None, [])["current"] == "implement"
    assert wfg.brief_path("implemented", None, [])["current"] == "dogfood"
    assert wfg.brief_path("dogfooded", None, [])["current"] == "review"
    assert wfg.brief_path("released", None, [])["current"] == "released"


def test_brief_path_marks_repair_loop_when_history_shows_it():
    p = wfg.brief_path("implemented", None, [{"stage": "fix", "note": "conformance attempt 1"}])
    assert "fix" in p["visited"] and "conf_gate" in p["visited"]
    # no repair history → no fix node highlighted
    clean = wfg.brief_path("designed", None, [])
    assert "fix" not in clean["visited"]


def test_brief_path_blocked_lands_on_blocked_terminal():
    p = wfg.brief_path("designed", "blocked", [])
    assert p["current"] == "blocked"
    assert "design" in p["visited"]
