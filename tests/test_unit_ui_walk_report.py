"""Unit tests for scripts/ui_walk_report.py — the manual UI-walk renderer.

First test coverage for the renderer. Pins the loop-traceability guarantees:
legacy steplog rows (no trace fields) render unchanged, rows carrying
usecase_id/milestone_id gain a trace chip, `missing` renders as the GAP badge,
unknown statuses normalize to info, and a missing screenshot degrades to a
placeholder instead of failing the report.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location("ui_walk_report", REPO / "scripts" / "ui_walk_report.py")
uwr = importlib.util.module_from_spec(_spec)
sys.modules["ui_walk_report"] = uwr
_spec.loader.exec_module(uwr)

LEGACY = {"usecase": "Capture flow", "step": 1, "action": "type a task",
          "status": "pass", "note": "fine", "url": "http://127.0.0.1:9000/hub/"}
TRACED = {"usecase": "UC2 — network studies", "step": 3, "action": "save a run",
          "status": "missing", "note": "GAP: no approver",
          "usecase_id": "riverside-bess-132kv", "milestone_id": "month-1-network"}


def render(rows):
    return uwr.render(rows, title="t", persona="Kevin", shots_base=Path("."))


class TestStatusVocabulary:
    def test_missing_is_gap_badge_ranked_second_worst(self):
        assert uwr.STATUS_LABEL["missing"] == "GAP"
        assert uwr.STATUS_RANK["missing"] == 1
        assert ">GAP<" in render([TRACED])

    def test_unknown_status_normalizes_to_info(self):
        assert uwr._norm_status("banana") == "info"
        html = render([{**LEGACY, "status": "banana"}])
        assert ">INFO<" in html


class TestTraceChip:
    def test_legacy_rows_have_no_trace_chip(self):
        assert 'class="trace"' not in render([LEGACY])

    def test_traced_rows_render_milestone_chip(self):
        html = render([TRACED])
        assert 'class="trace"' in html
        assert "month-1-network/s3" in html
        assert "riverside-bess-132kv::month-1-network::s3" in html  # full key in title

    def test_mixed_log_renders_both(self):
        html = render([LEGACY, TRACED])
        assert html.count('class="trace"') == 1
        assert "Capture flow" in html and "UC2" in html


class TestTolerance:
    def test_missing_screenshot_renders_placeholder_not_crash(self):
        html = render([{**LEGACY, "shot": "does-not-exist.png"}])
        assert "screenshot not found" in html

    def test_no_shot_key_renders_no_placeholder(self):
        assert "screenshot not found" not in render([LEGACY])

    def test_steplog_loader_skips_torn_lines(self, tmp_path):
        p = tmp_path / "steplog.jsonl"
        p.write_text('{"usecase":"a","step":1,"status":"pass"}\n{"torn', encoding="utf-8")
        rows = uwr._load_steplog(p)
        assert len(rows) == 1
