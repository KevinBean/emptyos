"""Unit tests for emptyos.sdk.delegation.gate_or_dispatch.

Pure unit test — no daemon. The autopilot decision itself has its own tests
(test_unit_autopilot_*); here we monkeypatch ``decide`` to pin a verdict and
assert gate_or_dispatch's three behaviours: auto-dispatch + audit, dispatch
error capture, and gate → on_gate callback. Async tests run via asyncio.run so
no pytest-asyncio config is required.
"""

from __future__ import annotations

import asyncio
import json

import emptyos.sdk.delegation as delegation


class _Config:
    def __init__(self, data_dir):
        self.data_dir = data_dir

    def get(self, key, default=None):
        return False  # all feature flags off; decide is monkeypatched anyway


class _Apps:
    def __init__(self, instances):
        self.instances = instances
        self.loaded = []

    async def load(self, app_id):
        self.loaded.append(app_id)
        inst = _App()
        self.instances[app_id] = inst
        return inst


class _Kernel:
    def __init__(self, data_dir, instances=None):
        self.config = _Config(data_dir)
        self.apps = _Apps(instances or {})

    def autopilot_eligible_set(self):
        return None


class _App:
    def __init__(self):
        self.calls = []

    async def add(self, **kw):
        self.calls.append(kw)
        return {"created": kw.get("text")}

    async def boom(self, **kw):
        raise RuntimeError("kaboom")


def _patch_decide(monkeypatch, verdict):
    monkeypatch.setattr(delegation, "decide", lambda *a, **k: verdict)


def _audit_lines(tmp_path):
    p = tmp_path / "autopilot" / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_auto_dispatches_and_audits(tmp_path, monkeypatch):
    _patch_decide(monkeypatch, {"action": "auto", "reason": "grant",
                                "grant_id": "grant-abc", "hold_id": None})
    app = _App()
    kernel = _Kernel(tmp_path, {"task": app})

    res = asyncio.run(delegation.gate_or_dispatch(
        kernel, actor={"type": "agent", "id": "finance-bot"},
        app="task", method="add", args={"text": "hi"},
        scope_candidates=["global"],
    ))

    assert res["action"] == "auto"
    assert res["ok"] is True
    assert res["grant_id"] == "grant-abc"
    assert app.calls == [{"text": "hi"}]

    audit = _audit_lines(tmp_path)
    assert audit and audit[-1]["actor"] == {"type": "agent", "id": "finance-bot"}
    assert audit[-1]["ok"] is True and audit[-1]["grant_id"] == "grant-abc"


def test_auto_dispatch_error_is_captured(tmp_path, monkeypatch):
    _patch_decide(monkeypatch, {"action": "auto", "reason": "stable-default",
                                "grant_id": None, "hold_id": None})
    kernel = _Kernel(tmp_path, {"task": _App()})

    res = asyncio.run(delegation.gate_or_dispatch(
        kernel, actor={"type": "agent", "id": "x"},
        app="task", method="boom", args={},
        scope_candidates=["global"],
    ))

    assert res["action"] == "auto"
    assert res["ok"] is False
    assert "kaboom" in res["error"]
    audit = _audit_lines(tmp_path)
    assert audit[-1]["ok"] is False


def test_gate_calls_on_gate_and_does_not_dispatch(tmp_path, monkeypatch):
    _patch_decide(monkeypatch, {"action": "gate", "reason": "no-grant",
                                "grant_id": None, "hold_id": None})
    app = _App()
    kernel = _Kernel(tmp_path, {"task": app})
    seen = []

    async def on_gate(record):
        seen.append(record)

    res = asyncio.run(delegation.gate_or_dispatch(
        kernel, actor={"type": "agent", "id": "other-bot"},
        app="task", method="add", args={"text": "nope"},
        scope_candidates=["global"], on_gate=on_gate,
    ))

    assert res["action"] == "gate"
    assert res["reason"] == "no-grant"
    assert app.calls == []                      # never dispatched
    assert len(seen) == 1
    assert seen[0]["gate_reason"] == "no-grant"
    assert seen[0]["actor"] == {"type": "agent", "id": "other-bot"}
    audit = _audit_lines(tmp_path)
    assert audit[-1]["ok"] is False and audit[-1]["error"] == "gated:no-grant"


def test_auto_loads_app_when_not_instantiated(tmp_path, monkeypatch):
    _patch_decide(monkeypatch, {"action": "auto", "reason": "grant",
                                "grant_id": "g1", "hold_id": None})
    kernel = _Kernel(tmp_path, {})             # task not loaded yet

    res = asyncio.run(delegation.gate_or_dispatch(
        kernel, actor={"type": "agent", "id": "x"},
        app="task", method="add", args={"text": "lazy"},
        scope_candidates=["global"],
    ))

    assert res["action"] == "auto" and res["ok"] is True
    assert kernel.apps.loaded == ["task"]      # load() was used


def _capture_decide(monkeypatch):
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return {"action": "gate", "reason": "no-grant", "grant_id": None, "hold_id": None}
    monkeypatch.setattr(delegation, "decide", fake)
    return seen


def test_decide_receives_the_kernel_floor(tmp_path, monkeypatch):
    seen = _capture_decide(monkeypatch)
    kernel = _Kernel(tmp_path, {"task": _App()})
    kernel.autopilot_eligible_set = lambda: {"task.add"}
    asyncio.run(delegation.gate_or_dispatch(
        kernel, actor={"type": "agent", "id": "a"}, app="task", method="add",
        scope_candidates=["global"],
    ))
    assert seen["eligible"] == {"task.add"}


class _MinimalKernel:
    """A kernel stand-in that has no ``autopilot_eligible_set`` at all."""

    def __init__(self, data_dir, instances):
        self.config = _Config(data_dir)
        self.apps = _Apps(instances)


def test_a_kernel_without_the_floor_method_gets_the_legacy_floor(tmp_path, monkeypatch):
    seen = _capture_decide(monkeypatch)
    asyncio.run(delegation.gate_or_dispatch(
        _MinimalKernel(tmp_path, {"task": _App()}), actor={"type": "agent", "id": "a"},
        app="task", method="add", scope_candidates=["global"],
    ))
    assert seen["eligible"] is None
