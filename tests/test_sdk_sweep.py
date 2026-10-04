"""Tests for the parameter-sweep primitive — sweep_values + BaseApp.sweep_method.

Offline / no-daemon: exercises the SDK helper directly with a FakeApp(BaseApp)
over a fake method registry, the same pattern as test_sdk_conformance.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from emptyos.kernel.app_loader import AppManifest
from emptyos.sdk.base_app import BaseApp
from emptyos.sdk.compute_cache import clear
from emptyos.sdk.utils import sweep_values


# ── sweep_values (pure helper) ────────────────────────────────────────


def test_sweep_values_linear():
    assert sweep_values(0, 10, 5) == [0.0, 2.5, 5.0, 7.5, 10.0]


def test_sweep_values_descending():
    assert sweep_values(50, 10, 5) == [50.0, 40.0, 30.0, 20.0, 10.0]


def test_sweep_values_endpoints_exact():
    vals = sweep_values(1, 3, 7)
    assert vals[0] == 1.0 and vals[-1] == 3.0
    assert len(vals) == 7


def test_sweep_values_log():
    vals = sweep_values(1, 1000, 4, scale="log")
    assert vals == pytest.approx([1.0, 10.0, 100.0, 1000.0])


def test_sweep_values_rejects_one_step():
    with pytest.raises(ValueError):
        sweep_values(0, 10, 1)


def test_sweep_values_log_rejects_nonpositive():
    with pytest.raises(ValueError):
        sweep_values(0, 10, 5, scale="log")


def test_sweep_values_rejects_unknown_scale():
    with pytest.raises(ValueError):
        sweep_values(0, 10, 5, scale="quadratic")


# ── sweep_method (BaseApp) ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear()
    yield
    clear()


def _make_manifest(method_blocks):
    return AppManifest(
        id="sweepapp", name="sweep", version="0", description="",
        path=Path("."),
        provides={"methods": {"amp": method_blocks}},
    )


class _FakeKernel:
    class _Services:
        def get_optional(self, _):
            return None

    services = _Services()


class FakeApp(BaseApp):
    async def _calc(self, payload):
        x = payload["x"]
        if x < 0:
            raise ValueError("negative x")
        out = {"ampacity_a": x * 10.0}
        if x > 100:
            out["warnings"] = ["hot"]
        return out


def _app():
    return FakeApp(
        _FakeKernel(),
        _make_manifest([{"id": "native", "fn": "_calc", "default": True, "version": "1.2.3"}]),
    )


def test_sweep_method_monotonic_series():
    app = _app()
    out = asyncio.run(app.sweep_method(
        "amp", None,
        values=sweep_values(1, 5, 5),
        payload_for=lambda x: {"x": x},
        extract="ampacity_a",
        x_label="X", y_label="Amps",
    ))
    assert out["method"] == "native"
    assert out["method_version"] == "1.2.3"
    assert out["x"] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert out["y"] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert out["n_points"] == 5
    assert out["errors"] == []
    assert out["x_label"] == "X" and out["y_label"] == "Amps"


def test_sweep_method_callable_extract():
    app = _app()
    out = asyncio.run(app.sweep_method(
        "amp", "native",
        values=[1.0, 2.0],
        payload_for=lambda x: {"x": x},
        extract=lambda r: r["ampacity_a"] / 10.0,
    ))
    assert out["y"] == [1.0, 2.0]


def test_sweep_method_per_point_error_isolated():
    app = _app()
    out = asyncio.run(app.sweep_method(
        "amp", "native",
        values=[-1.0, 2.0, 3.0],
        payload_for=lambda x: {"x": x},
        extract="ampacity_a",
    ))
    # The failing point keeps its x, gets a None y, and lands in errors.
    assert out["x"] == [-1.0, 2.0, 3.0]
    assert out["y"] == [None, 20.0, 30.0]
    assert len(out["errors"]) == 1
    assert out["errors"][0]["x"] == -1.0
    assert "negative" in out["errors"][0]["reason"]


def test_sweep_method_dedupes_warnings():
    app = _app()
    out = asyncio.run(app.sweep_method(
        "amp", "native",
        values=[150.0, 200.0],  # both > 100 → "hot" warning each point
        payload_for=lambda x: {"x": x},
        extract="ampacity_a",
    ))
    assert out["warnings"] == ["hot"]


def test_sweep_method_cache_off_matches_cache_on():
    app = _app()
    kw = dict(
        values=[1.0, 2.0, 3.0],
        payload_for=lambda x: {"x": x},
        extract="ampacity_a",
    )
    cached = asyncio.run(app.sweep_method("amp", "native", cache=True, **kw))
    raw = asyncio.run(app.sweep_method("amp", "native", cache=False, **kw))
    assert cached["y"] == raw["y"] == [10.0, 20.0, 30.0]


def test_sweep_method_unavailable_method_raises():
    app = FakeApp(
        _FakeKernel(),
        _make_manifest([{"id": "ghost", "fn": "_does_not_exist", "default": True}]),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(app.sweep_method(
            "amp", None,
            values=[1.0, 2.0],
            payload_for=lambda x: {"x": x},
            extract="ampacity_a",
        ))


def test_sweep_method_missing_extract_key_is_none_not_error():
    class NoKeyApp(BaseApp):
        async def _calc(self, payload):
            return {"other": 1.0}

    app = NoKeyApp(
        _FakeKernel(),
        _make_manifest([{"id": "native", "fn": "_calc", "default": True}]),
    )
    out = asyncio.run(app.sweep_method(
        "amp", None,
        values=[1.0, 2.0],
        payload_for=lambda x: {"x": x},
        extract="ampacity_a",
    ))
    assert out["y"] == [None, None]
    assert out["errors"] == []  # missing field is not a failure
