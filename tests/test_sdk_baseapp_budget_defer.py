"""BaseApp budget-awareness (`budget_remaining`/`over_budget`) + one-shot
scheduling (`add_once_job`). Thin wrappers over tested primitives; here we
verify the wiring (actor-id resolution, data-root, DateTrigger, fail-soft).

Run: python -m pytest tests/test_sdk_baseapp_budget_defer.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from emptyos.kernel.app_loader import AppManifest
from emptyos.sdk import autopilot
from emptyos.sdk.base_app import BaseApp

pytestmark = pytest.mark.unit


class _Cfg:
    def __init__(self, d):
        self.data_dir = Path(d)


class _Kernel:
    class _Services:
        def get_optional(self, _):
            return None

    services = _Services()

    def __init__(self, d, scheduler=None):
        self.config = _Cfg(d)
        self.scheduler = scheduler


class _App(BaseApp):
    pass


def _make_app(tmp_path, scheduler=None):
    manifest = AppManifest(
        id="budtest", name="b", version="0", description="", path=Path(".")
    )
    return _App(_Kernel(tmp_path, scheduler), manifest)


# ── budget_remaining / over_budget ────────────────────────────────────────────


def test_budget_remaining_none_when_uncapped(tmp_path):
    app = _make_app(tmp_path)
    assert app.budget_remaining() is None  # no cap set for actor 'budtest'
    assert app.over_budget() is False       # uncapped => never over


def test_budget_remaining_reflects_cap_and_spend(tmp_path):
    app = _make_app(tmp_path)
    # autopilot_root == kernel.config.data_dir == tmp_path
    autopilot.set_budget(tmp_path, "budtest", 10.0)
    assert app.budget_remaining() == 10.0
    autopilot.record_spend(tmp_path, "budtest", 3.0)
    assert app.budget_remaining() == pytest.approx(7.0)
    assert app.over_budget() is False


def test_over_budget_true_at_cap(tmp_path):
    app = _make_app(tmp_path)
    autopilot.set_budget(tmp_path, "budtest", 5.0)
    autopilot.record_spend(tmp_path, "budtest", 5.0)  # spent >= cap
    assert app.over_budget() is True
    assert app.budget_remaining() == pytest.approx(0.0)


def test_budget_actor_override(tmp_path):
    app = _make_app(tmp_path)
    autopilot.set_budget(tmp_path, "other-actor", 2.0)
    assert app.budget_remaining("other-actor") == 2.0
    assert app.budget_remaining() is None  # default actor 'budtest' still uncapped


# ── add_once_job ──────────────────────────────────────────────────────────────


class _FakeAPS:
    def __init__(self):
        self.jobs = []

    def add_job(self, callable_, trigger=None, id=None, replace_existing=False):
        self.jobs.append({"id": id, "trigger": trigger, "callable": callable_})


class _FakeScheduler:
    def __init__(self):
        self._scheduler = _FakeAPS()


def test_add_once_job_registers_date_trigger(tmp_path):
    from datetime import datetime

    sched = _FakeScheduler()
    app = _make_app(tmp_path, scheduler=sched)
    ok = app.add_once_job("job-1", lambda: None, run_at=datetime(2026, 1, 1, 9, 0))
    assert ok is True
    assert len(sched._scheduler.jobs) == 1
    from apscheduler.triggers.date import DateTrigger
    assert isinstance(sched._scheduler.jobs[0]["trigger"], DateTrigger)
    assert sched._scheduler.jobs[0]["id"] == "job-1"


def test_add_once_job_accepts_iso_string(tmp_path):
    sched = _FakeScheduler()
    app = _make_app(tmp_path, scheduler=sched)
    assert app.add_once_job("job-2", lambda: None, run_at="2026-06-01T14:30:00") is True
    assert len(sched._scheduler.jobs) == 1


def test_add_once_job_no_scheduler_returns_false(tmp_path):
    app = _make_app(tmp_path, scheduler=None)
    assert app.add_once_job("job-x", lambda: None, run_at="2026-06-01T14:30:00") is False


def test_add_once_job_bad_run_at_returns_false(tmp_path):
    sched = _FakeScheduler()
    app = _make_app(tmp_path, scheduler=sched)
    assert app.add_once_job("job-3", lambda: None, run_at="not-a-date") is False
    assert sched._scheduler.jobs == []
