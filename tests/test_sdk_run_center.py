"""Tests for emptyos.sdk.run_center.RunCenterMixin — the shared run-center HTTP surface.

Pure unit tests; no daemon required. Guards the contract two dev apps (app-builder,
fix-agent) depend on: the four run-center handlers carry the right @web_route
metadata and are discoverable through the MRO (the same walk BaseApp._get_decorated
does), and each handler's body behaves correctly against a minimal fake host.
"""

import asyncio

import pytest

from emptyos.sdk import RunCenterMixin

ROUTES = {
    "api_runs": ("GET", "/api/runs"),
    "api_run_get": ("GET", "/api/runs/{run_id}"),
    "api_run_discard": ("POST", "/api/runs/{run_id}/discard"),
    "api_status": ("GET", "/api/status"),
}


def test_handlers_carry_web_route_metadata():
    for name, (method, path) in ROUTES.items():
        meta = getattr(getattr(RunCenterMixin, name), "_eos_web", None)
        assert meta == {"method": method, "path": path}, name


def test_handlers_discoverable_through_mro():
    """Inherited @web_route methods must surface via the class-__dict__ MRO walk
    that BaseApp._get_decorated uses — otherwise the routes never mount."""
    class FakeBase:  # stands in for BaseApp
        pass

    class Host(FakeBase, RunCenterMixin):
        pass

    found = {}
    seen: set[str] = set()
    for cls in Host.__mro__:
        for attr, raw in cls.__dict__.items():
            if attr in seen or not callable(raw):
                continue
            seen.add(attr)
            if hasattr(raw, "_eos_web"):
                found[attr] = raw._eos_web["path"]
    assert set(found) == set(ROUTES)


class _FakeRunRegistry:
    def __init__(self, store):
        self._store = store

    def read_with_tail(self, rid):
        return self._store.get(rid)

    def update_state(self, rid, **kw):
        rec = self._store.get(rid)
        if rec is None:
            return None
        rec.update(kw)
        return rec


class _Req:
    def __init__(self, **params):
        self.path_params = params


class _Host(RunCenterMixin):
    """Minimal host satisfying the mixin's contract, with git_run stubbed."""

    def __init__(self, store, branch_deletes):
        self._store = store
        self._branch_deletes = branch_deletes  # records git branch -D calls

    def runs(self, kind="runs"):
        return _FakeRunRegistry(self._store)

    def _list_runs(self):
        return list(self._store.values())


def _run(coro):
    return asyncio.run(coro)


def test_api_runs_lists():
    host = _Host({"a": {"id": "a"}}, [])
    assert _run(host.api_runs(_Req())) == {"runs": [{"id": "a"}]}


def test_api_run_get_found_and_missing():
    host = _Host({"a": {"id": "a", "log": "..."}}, [])
    assert _run(host.api_run_get(_Req(run_id="a"))) == {"id": "a", "log": "..."}
    assert _run(host.api_run_get(_Req(run_id="nope"))) == {"error": "not found"}


def test_api_run_discard_deletes_branch(monkeypatch):
    import emptyos.sdk.run_center as rc

    deletes = []
    monkeypatch.setattr(rc, "git_run", lambda args, cwd=None: deletes.append(args))

    host = _Host({"a": {"id": "a", "branch": "fix/a"}}, deletes)
    host.repo_root = "/fake/repo"
    out = _run(host.api_run_discard(_Req(run_id="a")))
    assert out == {"ok": True, "branch": "fix/a"}
    assert host._store["a"]["status"] == "discarded"
    assert deletes == [["branch", "-D", "fix/a"]]


def test_api_run_discard_missing():
    host = _Host({}, [])
    assert _run(host.api_run_discard(_Req(run_id="nope"))) == {"error": "not found"}
