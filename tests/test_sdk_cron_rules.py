"""Unit tests for CronRuleScheduler (daemon-free).

Uses a fake app exposing only add_cron_job / remove_cron_job / log_warn — the
three surfaces the scheduler touches — to pin the register/sync/unregister
skeleton shared by staff + mail routines.
"""

from __future__ import annotations

from emptyos.sdk.cron_rules import CronRuleScheduler


class FakeApp:
    def __init__(self, add_ok: bool = True):
        self.jobs: dict[str, tuple] = {}
        self.removed: list[str] = []
        self.warns: list[str] = []
        self._add_ok = add_ok

    def add_cron_job(self, jid, fn, *, cron=None, interval_seconds=None):
        if not self._add_ok:
            return False
        self.jobs[jid] = (fn, cron)
        return True

    def remove_cron_job(self, jid):
        self.removed.append(jid)
        return self.jobs.pop(jid, None) is not None

    def log_warn(self, msg):
        self.warns.append(msg)


async def _noop():
    return None


def _sched(app, **over):
    kw = dict(
        prefix="t:",
        id_of=lambda r: r["id"],
        cron_of=lambda r: r.get("cron") or "0 9 * * *",
        should_schedule=lambda r: bool(r.get("enabled")),
        make_job=lambda r: _noop,
    )
    kw.update(over)
    return CronRuleScheduler(app, **kw)


def test_job_id_prefixed():
    s = _sched(FakeApp())
    assert s.job_id({"id": "abc"}) == "t:abc"


def test_register_all_only_enabled():
    app = FakeApp()
    s = _sched(app)
    s.register_all([
        {"id": "a", "enabled": True, "cron": "0 1 * * *"},
        {"id": "b", "enabled": False},
        {"id": "c", "enabled": True},
    ])
    assert set(app.jobs) == {"t:a", "t:c"}
    assert s.job_ids == {"t:a", "t:c"}
    assert app.jobs["t:a"][1] == "0 1 * * *"
    assert app.jobs["t:c"][1] == "0 9 * * *"  # default cron


def test_sync_toggle_off_drops_job():
    app = FakeApp()
    s = _sched(app)
    rec = {"id": "a", "enabled": True}
    s.sync(rec)
    assert "t:a" in app.jobs and s.job_ids == {"t:a"}
    rec["enabled"] = False
    s.sync(rec)
    assert "t:a" not in app.jobs and s.job_ids == set()


def test_sync_is_remove_then_add():
    app = FakeApp()
    s = _sched(app)
    rec = {"id": "a", "enabled": True, "cron": "0 1 * * *"}
    s.sync(rec)
    rec["cron"] = "0 2 * * *"
    s.sync(rec)
    assert app.jobs["t:a"][1] == "0 2 * * *"  # updated
    assert app.removed.count("t:a") == 2      # removed before each add


def test_unregister_one():
    app = FakeApp()
    s = _sched(app)
    s.register_all([{"id": "a", "enabled": True}, {"id": "b", "enabled": True}])
    s.unregister({"id": "a"})
    assert set(app.jobs) == {"t:b"} and s.job_ids == {"t:b"}


def test_unregister_all():
    app = FakeApp()
    s = _sched(app)
    s.register_all([{"id": "a", "enabled": True}, {"id": "b", "enabled": True}])
    s.unregister_all()
    assert app.jobs == {} and s.job_ids == set()


def test_add_failure_warns_and_untracked():
    app = FakeApp(add_ok=False)
    s = _sched(app)
    s.sync({"id": "a", "enabled": True, "cron": "not a cron"})
    assert s.job_ids == set()
    assert len(app.warns) == 1 and "not a cron" in app.warns[0]
