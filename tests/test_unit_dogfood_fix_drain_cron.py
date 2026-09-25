"""Pure-logic tests for the unattended fix-drain cron (2026-08-16).

Why this exists: the friction *generator* (`dogfood-agent:scheduled`) had been
on a cron since day one while the *consumer* (the drain) was manual-only. The
queue filled nightly and emptied only when a human ran it — measured that day
as 15 pending, oldest 34 days, and 17 of 34 closed items closed by hand.
`_register_fix_drain_cron` closes that throttle mismatch.

Two directions are pinned, per .claude/rules/audits.md — a registration test
that only ever asserts "a job appeared" proves nothing about the guards:

  * it stays DARK unless explicitly scheduled AND fix_agent_enabled, and
  * when it does fire it delegates to the shared `_start_fix_drain` guard core
    with `force_dirty=False` / `auto_stash=False` — an unattended drain must
    refuse on a dirty tree, never move a human's uncommitted work overnight.

Doesn't require a running daemon. Loads scheduled.py standalone and uses a
SimpleNamespace as a fake `self`, same shape as
tests/test_unit_dogfood_agent_drain_v2.py.

Run: python -m pytest tests/test_unit_dogfood_fix_drain_cron.py -v
"""
from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def scheduled_module():
    """Load apps/.../dogfood-agent/scheduled.py as a standalone module."""
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from helpers import app_path
    dogfood_dir = app_path("dogfood-agent")

    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.dogfood-agent" not in sys.modules:
        df_pkg = types.ModuleType("apps.dogfood-agent")
        df_pkg.__path__ = [str(dogfood_dir)]
        sys.modules["apps.dogfood-agent"] = df_pkg
    # scheduled.py does `from . import behavior as B` — preload it.
    if "apps.dogfood-agent.behavior" not in sys.modules:
        bspec = importlib.util.spec_from_file_location(
            "apps.dogfood-agent.behavior", dogfood_dir / "behavior.py",
        )
        bmod = importlib.util.module_from_spec(bspec)
        sys.modules["apps.dogfood-agent.behavior"] = bmod
        bspec.loader.exec_module(bmod)

    spec = importlib.util.spec_from_file_location(
        "apps.dogfood-agent.scheduled", dogfood_dir / "scheduled.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.dogfood-agent.scheduled"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.removed = []

    def add_job(self, fn, trigger=None, id=None, replace_existing=False):
        self.jobs[id] = fn

    def remove_job(self, jid):
        self.removed.append(jid)


def _fake_self(scheduled_module, config: dict, *, with_scheduler=True):
    """Minimal `self` carrying only what the cron registration reads."""
    sched_holder = types.SimpleNamespace(_scheduler=_FakeScheduler() if with_scheduler else None)
    calls: list[dict] = []
    activity: list[dict] = []

    async def _start_fix_drain(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "max_fixes": kwargs.get("max_fixes"), "pending_count": 4}

    async def _noop_tick():
        calls.append({"tick": True})

    ns = types.SimpleNamespace(
        kernel=types.SimpleNamespace(scheduler=sched_holder),
        app_config=lambda k, d=None: config.get(k, d),
        log_activity=activity.append,
        _fix_drain_job_id="dogfood-agent:fix-drain",
        _cron_job_id="dogfood-agent:scheduled",
        _reaper_job_id="dogfood-agent:reaper",
        _smoke_job_id="dogfood-agent:smoke",
        _journey_job_id="dogfood-agent:journey",
        _bug_audit_job_id="dogfood-agent:bug-audit",
        _start_fix_drain=_start_fix_drain,
        _scheduled_tick=_noop_tick,
        _smoke_tick=_noop_tick,
        _nightly_bug_audit=_noop_tick,
        _calls=calls,
        _activity=activity,
        _sched=sched_holder,
    )
    # The registrations delegate to the shared `_register_cron_job` skeleton
    # via self.X, so the fake has to bind it exactly as app.py does. Omit it
    # and every test fails at call time, not import time — the same binding
    # contract that governs the real class.
    ns._register_cron_job = functools.partial(scheduled_module._register_cron_job, ns)
    return ns


_ENABLED = {"enabled": True, "fix_agent_enabled": True, "fix_drain_schedule": "0 4 * * *"}


# ── The shared skeleton, across all four registrations ───────────────
#
# `_register_cron_job` was extracted 2026-08-16 from four near-identical
# copies. Three of those (main / smoke / bug-audit) had NO test coverage at
# the time, so the refactor rewrote live scheduler wiring on trust. These
# pin the per-job identity the skeleton must preserve: its own config key,
# its own job id, and its own log-event prefix — a swap between any two
# would silently register the wrong job or log under the wrong name.

_REGISTRATIONS = [
    # (register_fn, config_key, job_id, event_prefix)
    ("_register_cron", "schedule", "dogfood-agent:scheduled", ""),
    ("_register_smoke_cron", "smoke_schedule", "dogfood-agent:smoke", "smoke_"),
    ("_register_bug_audit_cron", "bug_audit_schedule", "dogfood-agent:bug-audit", "bug_audit_"),
    ("_register_fix_drain_cron", "fix_drain_schedule", "dogfood-agent:fix-drain", "fix_drain_"),
]


@pytest.mark.parametrize("fn_name,config_key,job_id,prefix", _REGISTRATIONS)
def test_registration_is_dark_without_its_own_key(
    scheduled_module, fn_name, config_key, job_id, prefix
):
    """Every one of the four stays off until ITS key is set — a shared
    skeleton must not let one job's schedule enable another's."""
    s = _fake_self(scheduled_module, {"enabled": True, "fix_agent_enabled": True})
    getattr(scheduled_module, fn_name)(s)
    assert s._sched._scheduler.jobs == {}


@pytest.mark.parametrize("fn_name,config_key,job_id,prefix", _REGISTRATIONS)
def test_registration_uses_its_own_id_and_event_prefix(
    scheduled_module, fn_name, config_key, job_id, prefix
):
    cfg = {"enabled": True, "fix_agent_enabled": True, config_key: "0 4 * * *"}
    s = _fake_self(scheduled_module, cfg)
    getattr(scheduled_module, fn_name)(s)
    assert list(s._sched._scheduler.jobs) == [job_id]
    assert any(e.get("event") == f"{prefix}cron_registered" for e in s._activity)


@pytest.mark.parametrize("fn_name,config_key,job_id,prefix", _REGISTRATIONS)
def test_registration_reports_a_bad_cron_under_its_prefix(
    scheduled_module, fn_name, config_key, job_id, prefix
):
    cfg = {"enabled": True, "fix_agent_enabled": True, config_key: "not a cron"}
    s = _fake_self(scheduled_module, cfg)
    getattr(scheduled_module, fn_name)(s)
    assert s._sched._scheduler.jobs == {}
    assert any(e.get("event") == f"{prefix}cron_invalid" for e in s._activity)


@pytest.mark.parametrize("fn_name,config_key,job_id,prefix", _REGISTRATIONS)
def test_registration_honours_the_app_kill_switch(
    scheduled_module, fn_name, config_key, job_id, prefix
):
    """`enabled = false` is the app-wide off switch — it must still win after
    the guards moved into the shared skeleton."""
    cfg = {"enabled": False, "fix_agent_enabled": True, config_key: "0 4 * * *"}
    s = _fake_self(scheduled_module, cfg)
    getattr(scheduled_module, fn_name)(s)
    assert s._sched._scheduler.jobs == {}


def test_only_fix_drain_requires_fix_agent_enabled(scheduled_module):
    """The extra guard belongs to ONE job. Hoisting it into the skeleton for
    everyone would silently disable the main persona tick."""
    cfg = {"enabled": True, "fix_agent_enabled": False, "schedule": "0 4 * * *"}
    s = _fake_self(scheduled_module, cfg)
    scheduled_module._register_cron(s)
    assert list(s._sched._scheduler.jobs) == ["dogfood-agent:scheduled"]


# ── Direction 1: stays dark ──────────────────────────────────────────


def test_no_schedule_registers_nothing(scheduled_module):
    """Dark by default — an unset schedule must not create a job."""
    s = _fake_self(scheduled_module, {"enabled": True, "fix_agent_enabled": True})
    scheduled_module._register_fix_drain_cron(s)
    assert s._sched._scheduler.jobs == {}


def test_fix_agent_disabled_registers_nothing(scheduled_module):
    """A schedule alone is not consent — the drain kill-switch still wins."""
    cfg = dict(_ENABLED, fix_agent_enabled=False)
    s = _fake_self(scheduled_module, cfg)
    scheduled_module._register_fix_drain_cron(s)
    assert s._sched._scheduler.jobs == {}


def test_app_disabled_registers_nothing(scheduled_module):
    s = _fake_self(scheduled_module, dict(_ENABLED, enabled=False))
    scheduled_module._register_fix_drain_cron(s)
    assert s._sched._scheduler.jobs == {}


def test_invalid_cron_registers_nothing_and_logs(scheduled_module):
    """A typo must fail loudly-but-safely, not crash setup()."""
    s = _fake_self(scheduled_module, dict(_ENABLED, fix_drain_schedule="not a cron"))
    scheduled_module._register_fix_drain_cron(s)
    assert s._sched._scheduler.jobs == {}
    assert any(e.get("event") == "fix_drain_cron_invalid" for e in s._activity)


def test_no_scheduler_is_a_noop(scheduled_module):
    """Kernel without a scheduler (CLI / local kernel) must not raise."""
    s = _fake_self(scheduled_module, _ENABLED, with_scheduler=False)
    scheduled_module._register_fix_drain_cron(s)  # must not raise


# ── Direction 2: registers, and fires with the safe contract ─────────


def test_registers_under_its_own_job_id(scheduled_module):
    s = _fake_self(scheduled_module, _ENABLED)
    scheduled_module._register_fix_drain_cron(s)
    assert "dogfood-agent:fix-drain" in s._sched._scheduler.jobs
    assert any(e.get("event") == "fix_drain_cron_registered" for e in s._activity)


def test_tick_never_forces_or_stashes(scheduled_module):
    """The unattended safety contract. force_dirty would merge onto a dirty
    tree; auto_stash would move the human's uncommitted work while they
    sleep. Both must stay off for the scheduled path specifically."""
    s = _fake_self(scheduled_module, _ENABLED)
    scheduled_module._register_fix_drain_cron(s)
    wrapper = s._sched._scheduler.jobs["dogfood-agent:fix-drain"]
    asyncio.run(wrapper())
    assert len(s._calls) == 1
    assert s._calls[0]["force_dirty"] is False
    assert s._calls[0]["auto_stash"] is False


def test_tick_uses_scheduled_batch_cap(scheduled_module):
    """Unattended batches are capped separately from the manual
    fix_drain_max so an overnight run stays reviewable."""
    s = _fake_self(scheduled_module, dict(_ENABLED, fix_drain_scheduled_max=2, fix_drain_max=10))
    scheduled_module._register_fix_drain_cron(s)
    asyncio.run(s._sched._scheduler.jobs["dogfood-agent:fix-drain"]())
    assert s._calls[0]["max_fixes"] == 2


def test_tick_logs_a_refusal_rather_than_swallowing_it(scheduled_module):
    """A refusal (dirty tree, drain active) is the normal healthy outcome —
    it must show up in the morning read, not vanish."""
    s = _fake_self(scheduled_module, _ENABLED)

    async def _refuse(**kwargs):
        return {"ok": False, "error": "working tree has uncommitted changes"}

    s._start_fix_drain = _refuse
    scheduled_module._register_fix_drain_cron(s)
    asyncio.run(s._sched._scheduler.jobs["dogfood-agent:fix-drain"]())
    ticks = [e for e in s._activity if e.get("event") == "fix_drain_tick"]
    assert len(ticks) == 1
    assert ticks[0]["ok"] is False
    assert "uncommitted" in ticks[0]["reason"]


def test_tick_crash_is_contained(scheduled_module):
    """A crashing drain must not take down the scheduler thread."""
    s = _fake_self(scheduled_module, _ENABLED)

    async def _boom(**kwargs):
        raise RuntimeError("kaboom")

    s._start_fix_drain = _boom
    scheduled_module._register_fix_drain_cron(s)
    asyncio.run(s._sched._scheduler.jobs["dogfood-agent:fix-drain"]())  # must not raise
    assert any(e.get("event") == "fix_drain_tick_crash" for e in s._activity)


# ── Teardown ─────────────────────────────────────────────────────────


def test_unregister_sweeps_the_fix_drain_job(scheduled_module):
    """Missing from the sweep, the job would survive teardown and fire
    against a torn-down app after a reload."""
    s = _fake_self(scheduled_module, _ENABLED)
    scheduled_module._unregister_cron(s)
    assert "dogfood-agent:fix-drain" in s._sched._scheduler.removed


# ── Feature-gap guard (`kind: missing`) ──────────────────────────────
#
# Scheduling the drain is what made this urgent: a `missing` finding is a
# feature gap that closes by a planning decision, never a pretended fix
# (loop-traceability). A human clicking Drain can see that; a 4am cron
# cannot. Measured 2026-08-16 — 3 of the 7 drainable prompts were `missing`,
# and one of them was third in the alphabetical pick order.


@pytest.fixture(scope="module")
def drain_module():
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from helpers import app_path
    dogfood_dir = app_path("dogfood-agent")
    if "apps" not in sys.modules:
        apps_pkg = types.ModuleType("apps")
        apps_pkg.__path__ = [str(repo_root / "apps")]
        sys.modules["apps"] = apps_pkg
    if "apps.dogfood-agent" not in sys.modules:
        df_pkg = types.ModuleType("apps.dogfood-agent")
        df_pkg.__path__ = [str(dogfood_dir)]
        sys.modules["apps.dogfood-agent"] = df_pkg
    spec = importlib.util.spec_from_file_location(
        "apps.dogfood-agent.drain", dogfood_dir / "drain.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apps.dogfood-agent.drain"] = mod
    spec.loader.exec_module(mod)
    return mod


def _prompt(tmp_path, name, *, kind=None, source=None, frontmatter=True):
    p = tmp_path / name
    if not frontmatter:
        p.write_text("# Friction: no frontmatter at all\n", encoding="utf-8")
        return p
    lines = ["---"]
    if kind is not None:
        lines.append(f"kind: {kind}")
    if source is not None:
        lines.append(f"source: {source}")
    lines += ["---", "", "# Friction: body"]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class _FM:
    """Only the frontmatter readers, bound as the app binds them."""


def _fm_self(drain_module):
    obj = _FM()
    for n in ("_fm_block", "_prompt_frontmatter_kind", "_prompt_frontmatter_source"):
        setattr(_FM, n, getattr(drain_module, n))
    return obj


def test_kind_missing_is_read(drain_module, tmp_path):
    s = _fm_self(drain_module)
    assert s._prompt_frontmatter_kind(_prompt(tmp_path, "a.md", kind="missing")) == "missing"


def test_kind_bug_and_confusing_are_not_gaps(drain_module, tmp_path):
    """The guard must not swallow real defects — those are the drain's job."""
    s = _fm_self(drain_module)
    assert s._prompt_frontmatter_kind(_prompt(tmp_path, "b.md", kind="bug")) == "bug"
    assert s._prompt_frontmatter_kind(_prompt(tmp_path, "c.md", kind="confusing")) == "confusing"


def test_absent_kind_is_empty_not_missing(drain_module, tmp_path):
    """Legacy prompts carry no `kind:`. They must stay drainable — treating an
    absent field as `missing` would silently freeze the whole legacy queue."""
    s = _fm_self(drain_module)
    assert s._prompt_frontmatter_kind(_prompt(tmp_path, "d.md", source="x")) == ""
    assert s._prompt_frontmatter_kind(_prompt(tmp_path, "e.md", frontmatter=False)) == ""


def test_unreadable_file_is_empty(drain_module, tmp_path):
    s = _fm_self(drain_module)
    assert s._prompt_frontmatter_kind(tmp_path / "nope.md") == ""


def test_queue_filter_leaves_only_actionable_defects(drain_module, tmp_path):
    """End-to-end shape of the two guards over a realistic queue — the exact
    2026-08-16 mix that motivated this."""
    s = _fm_self(drain_module)
    queue = [
        _prompt(tmp_path, "assistant-research.md", frontmatter=False),
        _prompt(tmp_path, "confusing-turn-46.md", kind="confusing"),
        _prompt(tmp_path, "ui-walk-substation-s2.md", kind="bug", source="ui-walk"),
        _prompt(tmp_path, "usecase-cad-corridor.md", kind="missing"),
        _prompt(tmp_path, "usecase-earthing-500.md", kind="bug"),
    ]
    pending = [p for p in queue if s._prompt_frontmatter_source(p) != "ui-walk"]
    pending = [p for p in pending if s._prompt_frontmatter_kind(p) != "missing"]
    assert [p.name for p in pending] == [
        "assistant-research.md",
        "confusing-turn-46.md",
        "usecase-earthing-500.md",
    ]


# ── Orchestrator-dirty reconciliation ────────────────────────────────
#
# Found 2026-08-17, the morning after the drain was first scheduled. The very
# first unattended run refused: `orchestrator_dirty` had been set on
# 2026-07-12 and the flag is only clearable by hand-editing fix-drain.json.
# `_start_fix_drain` refuses on it BEFORE `_drain_queue`, so no history entry
# is written — the block was both permanent and silent. The halt means "this
# PROCESS has stale code", so a restart resolves it.


class _DrainSelf:
    pass


def _drain_self(drain_module, tmp_path, state: dict):
    obj = _DrainSelf()
    for n in ("_load_fix_drain_state", "_save_fix_drain_state",
              "_fix_drain_state_path", "_reconcile_drain_state"):
        setattr(_DrainSelf, n, getattr(drain_module, n))
    obj.data_dir = tmp_path
    obj._activity = []
    obj.log_activity = obj._activity.append
    (tmp_path / "fix-drain.json").write_text(json.dumps(state), encoding="utf-8")
    return obj


def test_reconcile_is_noop_when_not_dirty(drain_module, tmp_path):
    s = _drain_self(drain_module, tmp_path, {"active": False})
    out = s._reconcile_drain_state()
    assert out.get("orchestrator_dirty") in (None, False)
    assert s._activity == []


def test_reconcile_keeps_halt_within_the_same_process(drain_module, tmp_path):
    """Same PID means the running code really IS the superseded code — the
    halt must survive, or the drain would drive stale orchestrator logic."""
    s = _drain_self(drain_module, tmp_path, {
        "orchestrator_dirty": True,
        "orchestrator_dirty_pid": os.getpid(),
        "orchestrator_paths": ["plugins/agent-runtime/plugin.py"],
    })
    out = s._reconcile_drain_state()
    assert out["orchestrator_dirty"] is True
    assert s._activity == []


def test_reconcile_clears_after_a_restart(drain_module, tmp_path):
    s = _drain_self(drain_module, tmp_path, {
        "orchestrator_dirty": True,
        "orchestrator_dirty_pid": os.getpid() + 1,   # a process that isn't us
        "orchestrator_paths": ["plugins/agent-runtime/plugin.py"],
    })
    out = s._reconcile_drain_state()
    assert out["orchestrator_dirty"] is False
    assert "stale_state_cleared" in out
    assert any(e.get("event") == "drain_state_reconciled" for e in s._activity)
    # Persisted, not just returned — the next call must see it too.
    on_disk = json.loads((tmp_path / "fix-drain.json").read_text(encoding="utf-8"))
    assert on_disk["orchestrator_dirty"] is False


def test_reconcile_clears_a_legacy_halt_with_no_pid(drain_module, tmp_path):
    """The real 2026-08-17 case. No PID recorded means the flag predates this
    reconciliation, and running this code required a restart — so the stale
    process is provably gone."""
    s = _drain_self(drain_module, tmp_path, {
        "orchestrator_dirty": True,
        "orchestrator_paths": ["plugins/agent-runtime/plugin.py"],
    })
    out = s._reconcile_drain_state()
    assert out["orchestrator_dirty"] is False


def test_reconcile_tolerates_a_malformed_pid(drain_module, tmp_path):
    s = _drain_self(drain_module, tmp_path, {
        "orchestrator_dirty": True,
        "orchestrator_dirty_pid": "not-a-pid",
    })
    out = s._reconcile_drain_state()
    assert out["orchestrator_dirty"] is False


# ── `active` — the same class, found 2026-08-17 by review ────────────
#
# `active` is set at drain start and cleared ONLY in _drain_queue's finally.
# A scheduled drain runs up to 75 min from 04:00; restart.bat kills every
# python and the watchdog respawns a wedged daemon, either of which skips the
# finally and strands the flag. Then every future run refuses "drain already
# in progress" — silently, since the refusal precedes any history write.


def test_reconcile_never_stomps_a_live_drain(drain_module, tmp_path):
    """The dangerous direction. Clearing `active` for a drain running in THIS
    process would let a second drain start concurrently — two fix-agents
    merging to main at once."""
    s = _drain_self(drain_module, tmp_path, {
        "active": True,
        "active_pid": os.getpid(),
        "current": {"started": "2026-08-17T04:00:00+00:00", "result": "in_progress"},
    })
    out = s._reconcile_drain_state()
    assert out["active"] is True
    assert out["current"] is not None
    assert s._activity == []


def test_reconcile_clears_an_orphaned_active_flag(drain_module, tmp_path):
    s = _drain_self(drain_module, tmp_path, {
        "active": True,
        "active_pid": os.getpid() + 1,
        "current": {"started": "2026-08-17T04:00:00+00:00", "result": "in_progress"},
    })
    out = s._reconcile_drain_state()
    assert out["active"] is False
    assert out["current"] is None
    assert any(e.get("event") == "drain_state_reconciled" for e in s._activity)


def test_reconcile_retires_the_stranded_run_as_interrupted(drain_module, tmp_path):
    """The half-written entry must land in history, not vanish and not be
    resumed — its in-flight fix-agent run died with the daemon."""
    s = _drain_self(drain_module, tmp_path, {
        "active": True,
        "active_pid": os.getpid() + 1,
        "current": {"started": "2026-08-17T04:00:00+00:00", "result": "in_progress"},
        "history": [],
    })
    out = s._reconcile_drain_state()
    assert out["history"][0]["result"] == "interrupted_daemon_gone"
    assert out["history"][0]["started"] == "2026-08-17T04:00:00+00:00"


def test_reconcile_clears_both_flags_in_one_pass(drain_module, tmp_path):
    s = _drain_self(drain_module, tmp_path, {
        "orchestrator_dirty": True,
        "active": True,
        "current": None,
    })
    out = s._reconcile_drain_state()
    assert out["orchestrator_dirty"] is False
    assert out["active"] is False
    ev = [e for e in s._activity if e.get("event") == "drain_state_reconciled"]
    assert len(ev) == 1 and set(ev[0]["cleared"]) == {"orchestrator_dirty", "active"}


def test_reconcile_active_survives_a_missing_current(drain_module, tmp_path):
    """Legacy state may have `active` without `current` — must not KeyError."""
    s = _drain_self(drain_module, tmp_path, {"active": True})
    out = s._reconcile_drain_state()
    assert out["active"] is False


def test_fm_block_shared_by_both_readers(drain_module, tmp_path):
    """One block reader, so source and kind can never disagree about where
    the frontmatter ends."""
    s = _fm_self(drain_module)
    p = _prompt(tmp_path, "f.md", kind="missing", source="ui-walk")
    assert s._prompt_frontmatter_kind(p) == "missing"
    assert s._prompt_frontmatter_source(p) == "ui-walk"
    assert s._fm_block(tmp_path / "absent.md") is None
