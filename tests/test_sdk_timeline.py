"""Unit tests for BaseApp.timeline (4D aggregator) + BaseApp.git_log.

Pure in-process — no daemon, no vault, no git required. Every data source
is mocked; the aggregator's contract is what's under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from emptyos.sdk import BaseApp


def _mk_app(*, fm=None, tags=None, timeline_section="", syslog_rows=None,
            scheduler_jobs=None, pending=None, reminders=None,
            call_app_handler=None):
    """Build a BaseApp with mocked kernel + mocked vault index for timeline tests."""
    config = MagicMock()
    config.notes_path = Path("/fake/vault")
    config.data_dir = Path("./data")

    vault_index = MagicMock()
    vault_index.get_properties.return_value = fm or {}
    vault_index.get_tags.return_value = tags or []

    services = MagicMock()
    services.get_optional.side_effect = lambda name: vault_index if name == "vault_index" else None

    syslog = MagicMock()
    syslog.query.return_value = syslog_rows or []

    scheduler = SimpleNamespace(jobs=scheduler_jobs or [])

    kernel = SimpleNamespace(
        config=config,
        services=services,
        syslog=syslog,
        scheduler=scheduler,
        vault_map=MagicMock(),
        events=MagicMock(),
    )
    manifest = SimpleNamespace(id="testapp")

    app = BaseApp.__new__(BaseApp)
    app.kernel = kernel
    app.manifest = manifest

    # Mock vault_read_section and call_app at the instance level so the
    # aggregator's real code paths exercise them.
    def _read_section(path, section):
        if section == "Timeline":
            return timeline_section
        return ""
    app.vault_read_section = _read_section  # type: ignore[method-assign]

    async def _call_app(app_id, method, **kwargs):
        if call_app_handler:
            return await call_app_handler(app_id, method, **kwargs)
        if app_id == "rooms" and method == "list_pending":
            return pending or []
        if app_id == "reminders" and method == "upcoming_for":
            return reminders or []
        return None
    app.call_app = _call_app  # type: ignore[method-assign]

    # git_log returns empty unless overridden — git invocation isn't testable
    # in a unit test
    async def _git_log(path, limit=20):
        return []
    app.git_log = _git_log  # type: ignore[method-assign]

    return app


def _run(coro):
    return asyncio.run(coro)


# ── Now snapshot ────────────────────────────────────────────────────────

def test_now_carries_frontmatter_tags_and_path():
    app = _mk_app(
        fm={"status": "active", "lifecycle": "living", "company": "Acme"},
        tags=["job-application", "career"],
    )
    out = _run(app.timeline("20_Areas/Career/job-acme.md"))
    assert out["now"]["path"] == "20_Areas/Career/job-acme.md"
    assert out["now"]["status"] == "active"
    assert out["now"]["lifecycle"] == "living"
    assert out["now"]["tags"] == ["job-application", "career"]
    assert out["now"]["frontmatter"]["company"] == "Acme"


# ── Past sources ────────────────────────────────────────────────────────

def test_past_from_created_and_updated_frontmatter():
    app = _mk_app(fm={"created": "2026-04-01", "updated": "2026-05-10"})
    out = _run(app.timeline("note.md"))
    kinds = [e["kind"] for e in out["past"]]
    assert "created" in kinds
    assert "updated" in kinds


def test_past_omits_updated_when_same_as_created():
    app = _mk_app(fm={"created": "2026-04-01", "updated": "2026-04-01"})
    out = _run(app.timeline("note.md"))
    kinds = [e["kind"] for e in out["past"]]
    assert "created" in kinds
    assert "updated" not in kinds


def test_past_parses_timeline_section_bullets():
    section = """
- 2026-04-02 — applied
- 2026-04-08 — recruiter screen
- 2026-04-15 - first interview
""".strip()
    app = _mk_app(timeline_section=section)
    out = _run(app.timeline("job.md"))
    milestones = [e for e in out["past"] if e["kind"] == "milestone"]
    assert len(milestones) == 3
    texts = [m["text"] for m in milestones]
    assert "applied" in texts
    assert "recruiter screen" in texts


def test_past_from_syslog_filters_by_entity_mention():
    rows = [
        {"ts": 1700000000.0, "level": "info", "source": "reactor",
         "message": "ripple for 20_Areas/Career/job-acme.md", "data": {}},
        {"ts": 1700001000.0, "level": "info", "source": "other",
         "message": "unrelated", "data": {}},
        {"ts": 1700002000.0, "level": "info", "source": "tasks",
         "message": "stem mention job-acme done", "data": {}},
    ]
    app = _mk_app(syslog_rows=rows)
    out = _run(app.timeline("20_Areas/Career/job-acme.md"))
    syslog_entries = [e for e in out["past"] if e["source"] in ("reactor", "tasks")]
    assert len(syslog_entries) == 2


def test_past_sorted_descending_by_ts():
    section = "- 2026-04-01 — early\n- 2026-05-01 — middle\n- 2026-06-01 — late"
    app = _mk_app(
        fm={"created": "2026-03-01"},
        timeline_section=section,
    )
    out = _run(app.timeline("note.md"))
    ts_list = [e["ts"] for e in out["past"]]
    assert ts_list == sorted(ts_list, reverse=True)


# ── Future sources ──────────────────────────────────────────────────────

def test_future_picks_up_due_and_expires_at():
    app = _mk_app(
        fm={"due": "2026-06-01", "expires_at": "2026-07-01", "next_review": "2026-05-20"},
    )
    out = _run(app.timeline("note.md"))
    kinds = [e["kind"] for e in out["future"]]
    assert "due" in kinds
    assert "expires_at" in kinds
    assert "next_review" in kinds


def test_future_includes_scheduler_jobs_mentioning_entity_stem():
    jobs = [
        {"id": "agent-jobsearch:scan-job-acme", "next_run": "2026-05-20 09:00",
         "trigger": "cron"},
        {"id": "unrelated:cron", "next_run": "2026-05-21 09:00", "trigger": "cron"},
    ]
    app = _mk_app(scheduler_jobs=jobs)
    out = _run(app.timeline("20_Areas/Career/job-acme.md"))
    scheduler_future = [e for e in out["future"] if e["source"] == "scheduler"]
    assert len(scheduler_future) == 1
    assert "job-acme" in scheduler_future[0]["text"]


def test_future_pulls_pending_actions_when_args_mention_entity():
    pending = [
        {"ts": "2026-05-15T10:00", "app": "task", "method": "add",
         "args": {"text": "follow up on job-acme"}},
        {"ts": "2026-05-15T11:00", "app": "task", "method": "add",
         "args": {"text": "unrelated"}},
    ]
    app = _mk_app(pending=pending)
    out = _run(app.timeline("20_Areas/Career/job-acme.md"))
    rooms_future = [e for e in out["future"] if e["source"] == "rooms"]
    assert len(rooms_future) == 1


def test_future_sorted_ascending_by_ts():
    app = _mk_app(
        fm={"due": "2026-08-01", "next_review": "2026-05-20", "expires_at": "2026-07-01"},
    )
    out = _run(app.timeline("note.md"))
    ts_list = [e["ts"] for e in out["future"]]
    assert ts_list == sorted(ts_list)


# ── Fail-soft semantics ─────────────────────────────────────────────────

def test_aggregator_survives_syslog_raise():
    app = _mk_app(fm={"created": "2026-04-01"})
    app.kernel.syslog.query.side_effect = RuntimeError("db locked")
    out = _run(app.timeline("note.md"))
    assert out["now"]["frontmatter"]["created"] == "2026-04-01"
    # Past from frontmatter still lands even though syslog blew up
    assert any(e["kind"] == "created" for e in out["past"])


def test_aggregator_survives_scheduler_missing():
    app = _mk_app(fm={"due": "2026-06-01"})
    app.kernel.scheduler = None  # type: ignore[assignment]
    out = _run(app.timeline("note.md"))
    assert any(e["kind"] == "due" for e in out["future"])


def test_aggregator_returns_empty_buckets_when_everything_is_empty():
    app = _mk_app()
    out = _run(app.timeline("nonexistent.md"))
    assert out["past"] == []
    assert out["future"] == []
    assert out["now"]["frontmatter"] == {}
    assert out["now"]["tags"] == []


# ── git_log (degraded environment) ──────────────────────────────────────

def test_git_log_returns_empty_list_when_vault_unset():
    # Build a fresh app whose git_log isn't mocked
    config = MagicMock()
    config.notes_path = None
    config.data_dir = Path("./data")
    services = MagicMock()
    services.get_optional.return_value = None
    kernel = SimpleNamespace(config=config, services=services, vault_map=MagicMock())
    app = BaseApp.__new__(BaseApp)
    app.kernel = kernel
    app.manifest = SimpleNamespace(id="testapp")
    assert _run(app.git_log("anywhere.md")) == []
