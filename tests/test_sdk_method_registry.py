"""Tests for emptyos.sdk.method_registry — calculator method dispatch.

Covers manifest parsing, list/get/default/resolve, requires_engines gating,
provenance recording on app, and BaseApp.compare_methods runner.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from emptyos.sdk.method_registry import MethodRegistry, MethodSpec


# ── Minimal fake app — implements just what registry needs ────────────


class FakeApp:
    """Stand-in for BaseApp: exposes engine() lookup + the methods being
    dispatched. Avoids booting the full kernel."""

    def __init__(self, manifest_provides: dict, *, engines: dict | None = None):
        from emptyos.kernel.app_loader import AppManifest
        from pathlib import Path
        self.manifest = AppManifest(
            id="fake", name="fake", version="0", description="",
            path=Path("."), provides=manifest_provides,
        )
        self._engines = engines or {}
        self._compute_provenance_by_endpoint: dict = {}

    def engine(self, eid: str) -> Any | None:
        return self._engines.get(eid)

    # Methods the manifest references
    async def _solve_a(self, payload: dict) -> dict:
        return {"value": payload.get("x", 0) * 2, "warnings": ["a-warned"]}

    async def _solve_b(self, payload: dict) -> dict:
        return {"value": payload.get("x", 0) * 3, "kcl_residual_max": 1e-9}

    async def _solve_failing(self, payload: dict) -> dict:
        raise RuntimeError("boom")


def _manifest(*method_blocks: dict) -> dict:
    return {"methods": {"solve": list(method_blocks)}}


# ── Manifest parsing ─────────────────────────────────────────────────


def test_registry_parses_manifest_blocks():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "label": "A", "fn": "_solve_a", "default": True, "version": "1.0"},
        {"id": "b", "label": "B", "fn": "_solve_b", "requires_engines": ["sim"]},
    ))
    assert reg.endpoints() == ["solve"]
    items = reg.list("solve")
    assert [s.id for s in items] == ["a", "b"]
    assert items[0].default is True
    assert items[1].requires_engines == ["sim"]


def test_registry_skips_malformed_entries():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "good", "fn": "_solve_a"},
        {"label": "no id"},                   # missing id
        "not a dict",                          # type: ignore
        {"id": "no_fn"},                       # missing fn
    ))
    assert [s.id for s in reg.list("solve")] == ["good"]


def test_registry_default_falls_back_to_first_when_unspecified():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "fn": "_solve_a"},
        {"id": "b", "fn": "_solve_b"},
    ))
    assert reg.default("solve").id == "a"


# ── Resolution ───────────────────────────────────────────────────────


def test_resolve_picks_explicit_id():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "fn": "_solve_a", "default": True},
        {"id": "b", "fn": "_solve_b"},
    ))
    assert reg.resolve("solve", "b").id == "b"


def test_resolve_falls_back_to_default_when_id_missing():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "fn": "_solve_a", "default": True},
        {"id": "b", "fn": "_solve_b"},
    ))
    assert reg.resolve("solve", None).id == "a"
    assert reg.resolve("solve", "no-such-method").id == "a"


# ── Availability gating ──────────────────────────────────────────────


def test_requires_engines_gates_availability():
    spec = MethodSpec(endpoint="solve", id="b", label="B", fn="_solve_b",
                      requires_engines=["sim"])
    app_no_sim = FakeApp(_manifest(), engines={})
    app_with_sim = FakeApp(_manifest(), engines={"sim": object()})
    ok1, reason1 = spec.is_available(app_no_sim)
    ok2, reason2 = spec.is_available(app_with_sim)
    assert ok1 is False and "sim" in reason1
    assert ok2 is True and reason2 == ""


def test_to_listing_marks_unavailable_methods():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "fn": "_solve_a", "default": True},
        {"id": "b", "fn": "_solve_b", "requires_engines": ["sim"]},
    ))
    app = FakeApp(_manifest(), engines={})  # no sim
    listing = reg.to_listing(app, "solve")
    by_id = {x["id"]: x for x in listing}
    assert by_id["a"]["available"] is True
    assert by_id["b"]["available"] is False
    assert "sim" in by_id["b"]["disabled_reason"]


# ── Run + provenance recording ───────────────────────────────────────


def test_run_records_provenance(monkeypatch):
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "a", "fn": "_solve_a", "default": True, "version": "1.0"},
    ))
    app = FakeApp(_manifest(
        {"id": "a", "fn": "_solve_a", "default": True, "version": "1.0"},
    ))
    spec = reg.resolve("solve", "a")
    result = asyncio.run(spec.run(app, {"x": 5}))
    assert result["value"] == 10
    prov = app._compute_provenance_by_endpoint["solve"]
    assert prov["method"] == "a"
    assert prov["method_version"] == "1.0"
    assert prov["warnings"] == ["a-warned"]
    assert "inputs_hash" in prov
    assert prov["runtime_s"] >= 0


def test_run_pulls_extras_from_dict_result():
    reg = MethodRegistry.from_manifest(_manifest(
        {"id": "b", "fn": "_solve_b", "default": True},
    ))
    app = FakeApp(_manifest({"id": "b", "fn": "_solve_b", "default": True}))
    spec = reg.resolve("solve", "b")
    asyncio.run(spec.run(app, {"x": 1}))
    prov = app._compute_provenance_by_endpoint["solve"]
    assert prov["extras"]["kcl_residual_max"] == 1e-9


# ── Compare runner via real BaseApp.compare_methods ──────────────────


@pytest.mark.asyncio
async def test_compare_methods_aligns_scalar_diffs():
    """Use BaseApp.compare_methods through a fake-ish app with a manifest."""
    from emptyos.sdk.base_app import BaseApp
    from emptyos.kernel.app_loader import AppManifest
    from pathlib import Path

    class App(BaseApp):
        async def _solve_a(self, payload):
            return {"epr_v": 100.0, "split": 0.5, "label": "A"}
        async def _solve_b(self, payload):
            return {"epr_v": 102.0, "split": 0.51, "label": "B"}

    manifest = AppManifest(
        id="fake", name="fake", version="0", description="",
        path=Path("."),
        provides={"methods": {"solve": [
            {"id": "a", "fn": "_solve_a", "default": True},
            {"id": "b", "fn": "_solve_b"},
        ]}},
    )

    class FakeKernel:
        class _Services:
            def get_optional(self, _): return None
        services = _Services()

    app = App(FakeKernel(), manifest)
    out = await app.compare_methods("solve", {"x": 1}, reference={"epr_v": 99.0, "split": 0.49})
    assert "epr_v" in out["comparison"]["matched_fields"]
    assert "split" in out["comparison"]["matched_fields"]
    # The "label" field is non-numeric in result — should NOT appear in scalar diff
    assert "label" not in out["comparison"]["matched_fields"]
    diff_epr = next(d for d in out["comparison"]["diffs"] if d["field"] == "epr_v")
    assert diff_epr["values"] == {"a": 100.0, "b": 102.0, "reference": 99.0}
    # max_rel_pct ≈ |102 − 99|/99 = 3.03%
    assert 2.0 < diff_epr["max_rel_pct"] < 4.0
