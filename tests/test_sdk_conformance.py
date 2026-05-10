"""Tests for emptyos.sdk.conformance — manifest-declared regression cases."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from emptyos.kernel.app_loader import AppManifest
from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.conformance import ConformanceCase, ConformanceRegistry, run_case


def _make_manifest(method_blocks, conformance_blocks):
    return AppManifest(
        id="fakeapp", name="fake", version="0", description="",
        path=Path("."),
        provides={
            "methods": {"solve": method_blocks},
            "conformance": {"solve": conformance_blocks},
        },
    )


class _FakeKernel:
    class _Services:
        def get_optional(self, _): return None
    services = _Services()


# ── Manifest parsing ─────────────────────────────────────────────────


def test_registry_parses_conformance_blocks():
    reg = ConformanceRegistry.from_manifest({
        "conformance": {"solve": [
            {"case_id": "rt07", "label": "RT-07",
             "inputs_fn": "_load_rt07", "expected_fn": "_rt07_expected",
             "methods": ["analytic", "emtp"],
             "tolerances": {"default_pct": 5.0, "central_epr_v_pct": 2.0}}
        ]}
    })
    cases = reg.list("solve")
    assert len(cases) == 1
    c = cases[0]
    assert c.case_id == "rt07"
    assert c.methods == ["analytic", "emtp"]
    assert c.tolerance_for("central_epr_v") == 2.0
    assert c.tolerance_for("split_factor") == 5.0  # falls back to default


def test_registry_skips_malformed_blocks():
    reg = ConformanceRegistry.from_manifest({
        "conformance": {"solve": [
            {"case_id": "good", "inputs_fn": "f1", "expected_fn": "f2"},
            {"case_id": "missing_inputs", "expected_fn": "f2"},  # missing inputs_fn
            "not a dict",  # type: ignore
            {"inputs_fn": "f1", "expected_fn": "f2"},  # missing case_id
        ]}
    })
    assert [c.case_id for c in reg.list("solve")] == ["good"]


# ── Runner ───────────────────────────────────────────────────────────


class FakeAppPasses(BaseApp):
    async def _solve_a(self, payload): return {"epr_v": 100.0, "split": 0.5}
    async def _solve_b(self, payload): return {"epr_v": 99.0, "split": 0.51}
    async def _load_inputs(self): return {"x": 1}
    async def _load_expected(self): return {"epr_v": 100.0, "split": 0.5}


def test_run_case_passes_when_within_tolerance():
    manifest = _make_manifest(
        [
            {"id": "a", "fn": "_solve_a", "default": True},
            {"id": "b", "fn": "_solve_b"},
        ],
        [
            {"case_id": "case1", "inputs_fn": "_load_inputs",
             "expected_fn": "_load_expected", "methods": ["a", "b"],
             "tolerances": {"default_pct": 5.0}},
        ],
    )
    app = FakeAppPasses(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve", "case1"))
    assert out["passed"] is True
    assert out["methods"]["a"]["passed"] is True
    assert out["methods"]["b"]["passed"] is True   # 99 vs 100 = 1% < 5%


def test_run_case_fails_outside_tolerance():
    manifest = _make_manifest(
        [
            {"id": "a", "fn": "_solve_a", "default": True},
            {"id": "b", "fn": "_solve_b"},
        ],
        [
            {"case_id": "tight", "inputs_fn": "_load_inputs",
             "expected_fn": "_load_expected", "methods": ["a", "b"],
             "tolerances": {"default_pct": 0.5}},  # tight; "b" would fail
        ],
    )
    app = FakeAppPasses(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve", "tight"))
    assert out["passed"] is False
    assert out["methods"]["a"]["passed"] is True   # 100 == 100 → 0%
    assert out["methods"]["b"]["passed"] is False  # 1% > 0.5%


def test_run_case_missing_field_in_result_fails():
    class App(BaseApp):
        async def _solve(self, p): return {"only": 1.0}
        async def _inputs(self): return {}
        async def _expected(self): return {"required_field": 99.0}

    manifest = _make_manifest(
        [{"id": "x", "fn": "_solve", "default": True}],
        [{"case_id": "c", "inputs_fn": "_inputs", "expected_fn": "_expected",
          "methods": ["x"], "tolerances": {"default_pct": 5.0}}],
    )
    app = App(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve", "c"))
    assert out["passed"] is False
    diff = out["methods"]["x"]["diffs"][0]
    assert diff["field"] == "required_field"
    assert diff["got"] is None
    assert "missing" in diff.get("reason", "")


def test_run_case_method_exception_fails():
    class App(BaseApp):
        async def _solve(self, p): raise RuntimeError("boom")
        async def _inputs(self): return {}
        async def _expected(self): return {"x": 1.0}

    manifest = _make_manifest(
        [{"id": "x", "fn": "_solve", "default": True}],
        [{"case_id": "c", "inputs_fn": "_inputs", "expected_fn": "_expected",
          "methods": ["x"], "tolerances": {"default_pct": 5.0}}],
    )
    app = App(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve", "c"))
    assert out["passed"] is False
    assert "boom" in out["methods"]["x"]["error"]


def test_run_case_skips_unavailable_method_without_failing():
    """Method gated on missing engine → skipped, doesn't fail the case."""
    class App(BaseApp):
        async def _solve_avail(self, p): return {"x": 1.0}
        async def _solve_gated(self, p): return {"x": 1.0}
        async def _inputs(self): return {}
        async def _expected(self): return {"x": 1.0}

    manifest = _make_manifest(
        [
            {"id": "avail", "fn": "_solve_avail", "default": True},
            {"id": "gated", "fn": "_solve_gated", "requires_engines": ["nonexistent"]},
        ],
        [{"case_id": "c", "inputs_fn": "_inputs", "expected_fn": "_expected",
          "methods": ["avail", "gated"], "tolerances": {"default_pct": 5.0}}],
    )
    app = App(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve", "c"))
    # Overall passes — gated is skipped, not failed.
    assert out["passed"] is True
    assert out["methods"]["avail"]["passed"] is True
    assert out["methods"]["gated"].get("skipped") is True


def test_list_conformance_returns_json_friendly():
    manifest = _make_manifest(
        [{"id": "a", "fn": "_solve_a", "default": True}],
        [{"case_id": "rt07", "label": "RT-07", "inputs_fn": "_inputs",
          "expected_fn": "_expected", "methods": ["a"],
          "tolerances": {"default_pct": 5.0},
          "references": ["[[some-note]]"]}],
    )
    app = FakeAppPasses(_FakeKernel(), manifest)
    cases = app.list_conformance("solve")
    assert len(cases) == 1
    assert cases[0]["case_id"] == "rt07"
    assert cases[0]["references"] == ["[[some-note]]"]


def test_run_conformance_no_case_id_runs_all():
    manifest = _make_manifest(
        [{"id": "a", "fn": "_solve_a", "default": True}],
        [
            {"case_id": "c1", "inputs_fn": "_load_inputs",
             "expected_fn": "_load_expected", "methods": ["a"],
             "tolerances": {"default_pct": 5.0}},
            {"case_id": "c2", "inputs_fn": "_load_inputs",
             "expected_fn": "_load_expected", "methods": ["a"],
             "tolerances": {"default_pct": 5.0}},
        ],
    )
    app = FakeAppPasses(_FakeKernel(), manifest)
    out = asyncio.run(app.run_conformance("solve"))
    assert isinstance(out, list)
    assert len(out) == 2
    assert {r["case_id"] for r in out} == {"c1", "c2"}
