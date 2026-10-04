"""Tests for emptyos.sdk.conformance — manifest-declared regression cases."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from emptyos.kernel.app_loader import AppManifest
from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.conformance import CalculatorRoutesMixin, ConformanceRegistry


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
    assert c.source == ""  # legacy case: no source declared


def test_registry_keeps_the_source_locator():
    """`source` says where the expected numbers were read; it must survive
    parsing (stripped) and reach the listing/run envelope by name."""
    reg = ConformanceRegistry.from_manifest({
        "conformance": {"solve": [
            {"case_id": "t3", "inputs_fn": "a", "expected_fn": "b",
             "source": "  IEC 60949:1988 Table III, p. 19  "}
        ]}
    })
    assert reg.list("solve")[0].source == "IEC 60949:1988 Table III, p. 19"


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


def _run_with_expected(result, expected) -> dict:
    """Run one case whose method returns `result` against `expected`.

    Both are passed through as given, so a test can hand in a value that is not
    a mapping at all.
    """
    class App(BaseApp):
        async def _solve(self, p): return result
        async def _inputs(self): return {}
        async def _expected(self): return expected

    manifest = _make_manifest(
        [{"id": "x", "fn": "_solve", "default": True}],
        [{"case_id": "c", "inputs_fn": "_inputs", "expected_fn": "_expected",
          "methods": ["x"], "tolerances": {"default_pct": 5.0}}],
    )
    return asyncio.run(App(_FakeKernel(), manifest).run_conformance("solve", "c"))


# A bool or string anchor used to be skipped, so it was declared and never
# evaluated, and a case whose anchors were all non-numeric passed with nothing
# compared. Each of these would have passed before the fix.

def test_a_bool_anchor_that_matches_passes():
    out = _run_with_expected({"met": True, "u": 100.0}, {"met": True, "u": 100.0})
    assert out["passed"] is True
    diff = next(d for d in out["methods"]["x"]["diffs"] if d["field"] == "met")
    assert diff["passed"] is True and diff["tolerance_pct"] is None


def test_a_bool_anchor_that_does_not_match_fails():
    out = _run_with_expected({"met": False}, {"met": True})
    assert out["passed"] is False
    diff = out["methods"]["x"]["diffs"][0]
    assert diff["passed"] is False and diff["reason"] == "exact match required"


def test_a_bool_anchor_is_not_matched_by_a_number():
    # True == 1 in Python; an anchor saying "met" must not pass on a count of 1.
    out = _run_with_expected({"met": 1}, {"met": True})
    assert out["passed"] is False
    assert out["methods"]["x"]["diffs"][0]["reason"] == "exact match required"


def test_a_string_anchor_is_not_matched_by_a_number():
    out = _run_with_expected({"code": 3}, {"code": "3"})
    assert out["passed"] is False
    assert out["methods"]["x"]["diffs"][0]["reason"] == "exact match required"


def test_a_result_that_is_not_a_mapping_fails_and_says_why():
    out = _run_with_expected([1.0], {"u": 1.0})
    assert out["passed"] is False
    assert "not a mapping of results" in out["methods"]["x"]["diffs"][0]["reason"]


def test_an_expected_loader_that_returns_no_mapping_raises():
    """A broken loader is an error, as a missing one is — not a silent failure
    with no diffs, which the drift scan would report as zero findings."""
    with pytest.raises(RuntimeError, match="not a mapping of expected values"):
        _run_with_expected({"u": 1.0}, [1.0])


def test_a_string_anchor_compares_exactly():
    assert _run_with_expected({"verdict": "exclude"}, {"verdict": "exclude"})["passed"] is True
    out = _run_with_expected({"verdict": "marginal"}, {"verdict": "exclude"})
    assert out["passed"] is False
    assert out["methods"]["x"]["diffs"][0]["got"] == "marginal"


def test_a_missing_bool_anchor_field_fails_as_missing():
    out = _run_with_expected({"u": 1.0}, {"met": True})
    assert out["passed"] is False
    assert out["methods"]["x"]["diffs"][0]["reason"] == "field missing in result"


@pytest.mark.parametrize("anchor", [None, [1, 2], {"a": 1}])
def test_an_anchor_that_cannot_be_compared_fails_and_says_why(anchor):
    out = _run_with_expected({"u": 1.0, "v": anchor}, {"u": 1.0, "v": anchor})
    assert out["passed"] is False
    diff = next(d for d in out["methods"]["x"]["diffs"] if d["field"] == "v")
    assert "cannot be compared" in diff["reason"]


def test_a_case_with_no_expected_values_fails():
    out = _run_with_expected({"u": 1.0}, {})
    assert out["passed"] is False
    assert "nothing would be compared" in out["methods"]["x"]["diffs"][0]["reason"]


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


# ── algorithm_doc_response ───────────────────────────────────────────


def _doc_app(tmp: Path):
    class _App(CalculatorRoutesMixin):
        manifest = AppManifest(id="fakeapp", name="fake", version="0",
                               description="", path=tmp)
    return _App()


def test_algorithm_doc_served_as_markdown(tmp_path):
    (tmp_path / "ALGORITHM.md").write_text("# Spec — Ω\n", encoding="utf-8")
    r = _doc_app(tmp_path).algorithm_doc_response()
    assert r.status_code == 200
    assert r.media_type == "text/markdown"
    assert r.body.decode("utf-8") == "# Spec — Ω\n"


def test_algorithm_doc_missing_is_404(tmp_path):
    r = _doc_app(tmp_path).algorithm_doc_response()
    assert r.status_code == 404
    assert r.body == b"algorithm document not found"
    assert r.media_type == "text/plain"


def test_algorithm_doc_ignores_a_directory_of_that_name(tmp_path):
    (tmp_path / "ALGORITHM.md").mkdir()
    assert _doc_app(tmp_path).algorithm_doc_response().status_code == 404
