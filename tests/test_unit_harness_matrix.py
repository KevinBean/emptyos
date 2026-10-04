"""Unit tests for the harness-matrix driver (scripts/run_harness_matrix.py) +
the refactor-verify harness hint. Pure — no daemon, no kernel boot.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "harness_matrix", REPO / "scripts" / "run_harness_matrix.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drv = _load_driver()


def test_subjects_for_expands_eos_keeps_others_verbatim():
    out = drv._subjects_for("minimax-m2.5:cloud",
                            ["eos+ollama", "claude-external", "eos+openai:gpt-5.4-mini", "ext:claude"])
    assert out == [
        "eos+ollama:minimax-m2.5:cloud",  # expanded per-model
        "claude-external",                 # model-fixed, verbatim
        "eos+openai:gpt-5.4-mini",         # model-fixed, verbatim
        "ext:claude",
    ]


def test_cell_includes_cost_when_present():
    cell = drv._cell({"ok": True, "tool_calls": 3, "wall_ms": 5200, "cost_usd": 0.042})
    assert cell.startswith("PASS")
    assert "3t" in cell and "5.2s" in cell and "$0.042" in cell


def test_cell_omits_cost_when_free():
    cell = drv._cell({"ok": False, "tool_calls": 0, "wall_ms": 8000, "cost_usd": 0.0})
    assert cell.startswith("FAIL")
    assert "$" not in cell  # local/free runs don't clutter the cell


def test_attributed_cost_ollama_cloud_is_estimated_not_zero():
    # :cloud ollama model reports $0 but should attribute iterations × rate
    row = {"subject": "eos+ollama", "model": "minimax-m2.5:cloud",
           "iterations": 6, "cost_usd": 0.0}
    c = drv._attributed_cost(row)
    assert c > 0
    assert abs(c - 6 * drv.OLLAMA_CLOUD_USD_PER_REQUEST) < 1e-9
    assert drv._is_ollama_cloud(row)


def test_attributed_cost_usage_priced_passthrough():
    # openai/claude rows carry real cost_usd — use it verbatim, not estimated
    row = {"subject": "eos+openai", "model": "gpt-5.4-mini", "iterations": 6, "cost_usd": 0.012}
    assert drv._attributed_cost(row) == 0.012
    assert not drv._is_ollama_cloud(row)


def test_attributed_cost_local_is_zero():
    # local (own-GPU) ollama model = genuinely $0
    row = {"subject": "eos+ollama", "model": "qwen3.5-32k:latest", "iterations": 5, "cost_usd": 0.0}
    assert drv._attributed_cost(row) == 0.0
    assert not drv._is_ollama_cloud(row)


def test_cell_marks_ollama_cloud_estimate_with_tilde():
    cell = drv._cell({"ok": True, "tool_calls": 2, "wall_ms": 9000,
                      "subject": "eos+ollama", "model": "gemma4:31b-cloud", "iterations": 4})
    assert "~$" in cell  # marked as an estimate


def test_norm_subject_collapses_model_suffix():
    assert drv._norm_subject("eos+ollama:gpt-oss:120b-cloud") == "eos+ollama"
    assert drv._norm_subject("eos+openai:gpt-5.4-mini") == "eos+openai"
    assert drv._norm_subject("claude-external") == "claude-external"


def test_build_report_has_cost_section_and_passes_per_dollar():
    rows = [
        {"model": "m1", "scenario": "s1", "subject": "eos+ollama", "ok": True,
         "tool_calls": 1, "wall_ms": 5000, "cost_usd": 0.0},
        {"model": "m1", "scenario": "s1", "subject": "claude-external", "ok": True,
         "tool_calls": 6, "wall_ms": 40000, "cost_usd": 0.08},
    ]
    rep = drv.build_report(rows, ["m1"], ["s1"], "STAMP")
    assert "Cost & efficiency" in rep
    assert "passes per $" in rep
    assert "$0.08" in rep             # claude cost surfaced
    assert "$0 local" in rep          # eos local row = free marker


def test_refactor_verify_hint_wired_in_agent_loop():
    # Source-level: the hint constant + its gated application both exist.
    src = (REPO / "emptyos" / "sdk" / "agent_loop.py").read_text(encoding="utf-8")
    assert "REFACTOR_VERIFY_HINT" in src
    assert 'feature_enabled(app_ref, "refactor-verify")' in src
    assert "Re-Grep until zero remain" in src


def test_refactor_verify_gate_wired_in_agent_loop():
    # Option B: the forced gate — message constant, the once-per-turn guard,
    # edit tracking, and the synthetic-user-turn + continue (not return).
    src = (REPO / "emptyos" / "sdk" / "agent_loop.py").read_text(encoding="utf-8")
    assert "REFACTOR_VERIFY_GATE_MSG" in src
    assert "_verify_forced" in src and "_made_edits" in src
    # gate fires only once per turn and re-enters the loop rather than finishing
    assert "not _verify_forced" in src
    assert '"content": REFACTOR_VERIFY_GATE_MSG' in src
    # DeleteFunction (delete-with-callers) also arms the gate
    assert "DeleteFunction" in src
