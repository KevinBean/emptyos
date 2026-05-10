"""Reactor handlers for cables events — daemon-free unit tests.

Stubs `_log_action` and `_journal_ripple` to capture calls, then fires
each cables event handler with a representative event payload. Asserts:
  - one journal ripple per run (no per-cable spam)
  - the breadcrumb mentions the project + key counters
  - the dimension is wellbeing-wheel-correct (occupational)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.reactor.reactions_work import WorkReactionsMixin


class _Stub(WorkReactionsMixin):
    """Concrete instance with the two helpers stubbed for capture."""

    def __init__(self):
        self.logs: list[tuple[str, str]] = []
        self.ripples: list[dict] = []

    def _log_action(self, kind, msg):
        self.logs.append((kind, msg))

    async def _journal_ripple(self, icon, text, *, dim=None, **kw):
        self.ripples.append({"icon": icon, "text": text, "dim": dim, **kw})


def _ev(**data):
    return SimpleNamespace(data=data)


@pytest.mark.asyncio
async def test_schedule_run_emits_one_ripple():
    s = _Stub()
    await s.on_cables_schedule_run(_ev(
        project="demo-mv", method="native",
        n_cables=12, n_ok=10, n_errors=1, n_skipped=1,
    ))
    assert len(s.ripples) == 1, "exactly one journal line per run"
    r = s.ripples[0]
    assert r["icon"] == "🔌"
    assert "demo-mv" in r["text"]
    assert "10/12 ok" in r["text"]
    assert "1 err" in r["text"]
    assert "1 skip" in r["text"]
    assert r["dim"] == "occupational"


@pytest.mark.asyncio
async def test_schedule_run_clean_run_no_err_no_skip():
    s = _Stub()
    await s.on_cables_schedule_run(_ev(
        project="proj-a", method="fem", n_cables=5, n_ok=5, n_errors=0, n_skipped=0,
    ))
    text = s.ripples[0]["text"]
    assert "5/5 ok" in text
    assert "err" not in text
    assert "skip" not in text


@pytest.mark.asyncio
async def test_load_flow_run_converged():
    s = _Stub()
    await s.on_cables_load_flow_run(_ev(
        project="grid-1", n_nodes=8, n_edges=7,
        converged=True, iterations=4, n_violations=0,
    ))
    assert len(s.ripples) == 1
    text = s.ripples[0]["text"]
    assert "grid-1" in text
    assert "converged" in text
    assert "8n/7e" in text


@pytest.mark.asyncio
async def test_load_flow_run_with_violations():
    s = _Stub()
    await s.on_cables_load_flow_run(_ev(
        project="overloaded", n_nodes=4, n_edges=3,
        converged=True, iterations=6, n_violations=2,
    ))
    text = s.ripples[0]["text"]
    assert "2 sizing violations" in text


@pytest.mark.asyncio
async def test_load_flow_run_diverged():
    s = _Stub()
    await s.on_cables_load_flow_run(_ev(
        project="bad", n_nodes=2, n_edges=1,
        converged=False, iterations=20, n_violations=0,
    ))
    assert "did not converge" in s.ripples[0]["text"]


@pytest.mark.asyncio
async def test_cable_routed_emits_route_summary():
    s = _Stub()
    await s.on_cables_cable_routed(_ev(
        project="windy", id="cbl-007",
        path=["sub-A", "j-1", "j-2", "wt-3"], edges=["e1", "e2", "e3"],
        n_edge_links_set=3,
    ))
    text = s.ripples[0]["text"]
    assert "cbl-007" in text
    assert "windy" in text
    assert "3 hop" in text


@pytest.mark.asyncio
async def test_handlers_dont_crash_on_missing_data():
    """Empty event.data must not raise — handlers ship with sensible
    defaults so a malformed emit never blows up the reactor loop."""
    s = _Stub()
    await s.on_cables_schedule_run(_ev())
    await s.on_cables_load_flow_run(_ev())
    await s.on_cables_cable_routed(_ev())
    assert len(s.ripples) == 3


@pytest.mark.asyncio
async def test_all_handlers_log_action():
    """Every handler should write a syslog row regardless of journal."""
    s = _Stub()
    await s.on_cables_schedule_run(_ev(project="p", method="native"))
    await s.on_cables_load_flow_run(_ev(project="p", converged=True))
    await s.on_cables_cable_routed(_ev(project="p", id="c"))
    kinds = [k for k, _ in s.logs]
    assert kinds == ["cables:schedule_run", "cables:load_flow_run", "cables:cable_routed"]
