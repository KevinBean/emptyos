"""Autopilot grant re-review (定期复审) — `review_grants` in emptyos/sdk/autopilot.py.

Read-only classification of the grant store against the audit chain:
- persistent + aged + unfired → stale
- persistent + aged + recently fired → active
- persistent + new + unfired → fresh
- expiring soon → expiring
- 7-day roll-up counts every audit entry (including grant_id=None)
- corrupt audit lines are skipped, not fatal

Run: python -m pytest tests/test_sdk_autopilot_review.py -v
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from emptyos.sdk.autopilot import (
    AUDIT_FILE,
    append_audit,
    review_grants,
    save_grant,
    set_budget,
    record_spend,
)

# Real clock, not a fixed date — load_grants() reaps expired grants against
# the real clock on read, so a fixed NOW would make expiry tests time-of-day
# flaky. All offsets are relative to NOW.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Fresh data root per test — autopilot subdir is lazily created."""
    return tmp_path


def _grant(data_dir: Path, *, created_days_ago: float = 0.0,
           ttl_seconds: int | None = None, verb: str = "task.add") -> dict:
    """Save a grant, then backdate its created_at/expires_at relative to NOW."""
    g = save_grant(
        data_dir,
        actor_type="cli", actor_id="claude-cli",
        verb_pattern=verb, scope="global",
        ttl_seconds=ttl_seconds,
    )
    grants_path = data_dir / "autopilot" / "grants.json"
    grants = json.loads(grants_path.read_text(encoding="utf-8"))
    for rec in grants:
        if rec["id"] == g["id"]:
            rec["created_at"] = (
                NOW - timedelta(days=created_days_ago)).isoformat()
            if ttl_seconds:
                rec["expires_at"] = (
                    NOW + timedelta(seconds=ttl_seconds)).isoformat()
    grants_path.write_text(json.dumps(grants), encoding="utf-8")
    g.update(next(r for r in grants if r["id"] == g["id"]))
    return g


def _fire(data_dir: Path, *, grant_id: str | None, days_ago: float = 0.0,
          ok: bool = True, app: str = "task", method: str = "add") -> None:
    """append_audit, then rewrite the entry's ts to NOW - days_ago.

    review_grants doesn't verify the HMAC chain, so editing ts after append
    is fine for these tests.
    """
    append_audit(
        data_dir,
        actor={"type": "cli", "id": "claude-cli"},
        app=app, method=method, args={}, grant_id=grant_id, ok=ok,
    )
    path = data_dir / "autopilot" / AUDIT_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[-1])
    e["ts"] = (NOW - timedelta(days=days_ago)).isoformat()
    lines[-1] = json.dumps(e, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_empty_store(data_dir):
    rep = review_grants(data_dir, now=NOW)
    assert rep["grants"] == []
    assert rep["stale_ids"] == []
    assert rep["expiring_ids"] == []
    assert rep["week"]["fired"] == 0
    assert rep["audit_lines"] == 0
    assert rep["budgets_over"] == []


def test_persistent_aged_unfired_is_stale(data_dir):
    g = _grant(data_dir, created_days_ago=30)
    rep = review_grants(data_dir, now=NOW)
    assert rep["stale_ids"] == [g["id"]]
    assert rep["grants"][0]["status"] == "stale"
    assert rep["grants"][0]["fire_count"] == 0


def test_recent_fire_makes_aged_grant_active(data_dir):
    g = _grant(data_dir, created_days_ago=30)
    _fire(data_dir, grant_id=g["id"], days_ago=2)
    rep = review_grants(data_dir, now=NOW)
    assert rep["stale_ids"] == []
    row = rep["grants"][0]
    assert row["status"] == "active"
    assert row["fire_count"] == 1
    assert row["last_fired_at"] is not None


def test_old_fire_does_not_rescue_aged_grant(data_dir):
    g = _grant(data_dir, created_days_ago=60)
    _fire(data_dir, grant_id=g["id"], days_ago=40)
    rep = review_grants(data_dir, now=NOW)
    assert rep["stale_ids"] == [g["id"]]
    assert rep["grants"][0]["fire_count"] == 1  # counted, but outside window


def test_new_persistent_grant_is_fresh_not_stale(data_dir):
    _grant(data_dir, created_days_ago=1)
    rep = review_grants(data_dir, now=NOW)
    assert rep["stale_ids"] == []
    assert rep["grants"][0]["status"] == "fresh"


def test_short_lived_grant_is_expiring(data_dir):
    g = _grant(data_dir, ttl_seconds=3600)  # expires in 1h < 72h window
    rep = review_grants(data_dir, now=NOW)
    assert rep["expiring_ids"] == [g["id"]]
    assert rep["grants"][0]["status"] == "expiring"


def test_far_expiry_unfired_grant_is_active(data_dir):
    # Expires well past the 72h window; short-lived grants aren't judged stale.
    _grant(data_dir, created_days_ago=30, ttl_seconds=30 * 24 * 3600)
    rep = review_grants(data_dir, now=NOW)
    assert rep["grants"][0]["status"] == "active"
    assert rep["stale_ids"] == []


def test_stale_days_knob(data_dir):
    g = _grant(data_dir, created_days_ago=10)
    assert review_grants(data_dir, now=NOW)["stale_ids"] == []  # default 14d
    rep = review_grants(data_dir, now=NOW, stale_after_days=7)
    assert rep["stale_ids"] == [g["id"]]


def test_week_rollup_counts_grantless_entries(data_dir):
    g = _grant(data_dir, created_days_ago=1)
    _fire(data_dir, grant_id=g["id"], days_ago=1)
    _fire(data_dir, grant_id=None, days_ago=2)            # stable-default fire
    _fire(data_dir, grant_id=None, days_ago=3, ok=False)  # denial
    _fire(data_dir, grant_id=None, days_ago=20)           # outside the week
    rep = review_grants(data_dir, now=NOW)
    assert rep["week"]["fired"] == 3
    assert rep["week"]["ok"] == 2
    assert rep["week"]["failed"] == 1
    assert rep["week"]["by_verb"]["task.add"] == 3
    assert rep["week"]["by_actor"]["cli/claude-cli"] == 3
    assert rep["audit_lines"] == 4
    # per-grant counter only sees the grant_id-bearing entry
    assert rep["grants"][0]["fire_count"] == 1


def test_corrupt_audit_line_skipped(data_dir):
    g = _grant(data_dir, created_days_ago=1)
    _fire(data_dir, grant_id=g["id"], days_ago=1)
    path = data_dir / "autopilot" / AUDIT_FILE
    with path.open("a", encoding="utf-8") as f:
        f.write("{not json at all\n")
    _fire(data_dir, grant_id=g["id"], days_ago=0.5)
    rep = review_grants(data_dir, now=NOW)
    assert rep["audit_lines"] == 2  # corrupt line not counted, not fatal
    assert rep["grants"][0]["fire_count"] == 2


def test_over_budget_actor_surfaces(data_dir):
    set_budget(data_dir, "staff", 1.00)
    record_spend(data_dir, "staff", 2.50)
    rep = review_grants(data_dir, now=datetime.now(timezone.utc))
    assert "staff" in rep["budgets_over"]
