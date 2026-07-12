"""Unit tests for the proactive-dispatch gate (emptyos/sdk/proactive.py).

Pure — no daemon, no kernel. Runs as: python -m pytest tests/test_unit_proactive.py
"""

from __future__ import annotations

from datetime import datetime

import emptyos.sdk.proactive as pro


def base_policy(**over):
    """Enabled, no quiet window, no gap, generous cap — a clean 'would deliver'
    baseline so each test isolates the one gate it exercises."""
    p = dict(pro.DEFAULT_POLICY)
    p.update({
        "enabled": True,
        "quiet_start": "00:00",
        "quiet_end": "00:00",  # start==end → no quiet window
        "min_gap_sec": 0,
        "daily_cap": 100,
        "kinds": {},
    })
    p.update(over)
    return p


def empty_state():
    return {"last_sent": {}, "day": {"date": "", "total": 0, "kinds": {}}, "dedup": {}}


NOON = datetime(2026, 1, 15, 12, 0, 0)


# ─── master / dark default ───────────────────────────────────────────────────
def test_dark_default_decide():
    pol = dict(pro.DEFAULT_POLICY)  # enabled = False
    d = pro.decide(pol, empty_state(), kind="deadline")
    assert d.deliver is False
    assert d.reason == "disabled"


def test_load_policy_is_dark_on_fresh_store(tmp_path):
    pol = pro.load_policy(tmp_path)
    assert pol["enabled"] is False
    assert "kinds" in pol and isinstance(pol["kinds"], dict)


def test_basic_deliver():
    d = pro.decide(base_policy(), empty_state(), kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is True
    assert d.reason == "ok"
    assert "voice" in d.channels and "notify" in d.channels


# ─── quiet hours ─────────────────────────────────────────────────────────────
def test_quiet_hours_suppresses():
    pol = base_policy(quiet_start="08:00", quiet_end="18:00")
    d = pro.decide(pol, empty_state(), kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "quiet-hours"


def test_critical_bypasses_quiet():
    pol = base_policy(quiet_start="08:00", quiet_end="18:00")
    d = pro.decide(pol, empty_state(), kind="deadline", urgency="critical", now=1000.0, now_dt=NOON)
    assert d.deliver is True


def test_in_quiet_hours_midnight_crossing():
    assert pro.in_quiet_hours("22:00", "07:00", datetime(2026, 1, 1, 23, 0)) is True
    assert pro.in_quiet_hours("22:00", "07:00", datetime(2026, 1, 1, 6, 0)) is True
    assert pro.in_quiet_hours("22:00", "07:00", datetime(2026, 1, 1, 12, 0)) is False
    assert pro.in_quiet_hours("00:00", "00:00", datetime(2026, 1, 1, 12, 0)) is False
    assert pro.in_quiet_hours("garbage", "07:00") is False


# ─── daily cap ───────────────────────────────────────────────────────────────
def test_daily_cap_global():
    pol = base_policy(daily_cap=2)
    st = empty_state()
    st["day"] = {"date": NOON.date().isoformat(), "total": 2, "kinds": {}}
    d = pro.decide(pol, st, kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "daily-cap"


def test_daily_cap_per_kind():
    pol = base_policy(daily_cap=100, kinds={"deadline": {"daily_cap": 1}})
    st = empty_state()
    st["day"] = {"date": NOON.date().isoformat(), "total": 1, "kinds": {"deadline": 1}}
    d = pro.decide(pol, st, kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "daily-cap:deadline"


# ─── min gap ─────────────────────────────────────────────────────────────────
def test_min_gap_global():
    pol = base_policy(min_gap_sec=100)
    st = empty_state()
    st["last_sent"]["_any"] = 950.0
    d = pro.decide(pol, st, kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "gap"


def test_min_gap_elapsed_delivers():
    pol = base_policy(min_gap_sec=100)
    st = empty_state()
    st["last_sent"]["_any"] = 800.0  # 200s ago > 100s gap
    d = pro.decide(pol, st, kind="deadline", now=1000.0, now_dt=NOON)
    assert d.deliver is True


# ─── dedup ───────────────────────────────────────────────────────────────────
def test_dedup_suppresses_recent():
    st = empty_state()
    st["dedup"]["journaling-gap"] = 1000.0 - 100
    d = pro.decide(base_policy(), st, kind="journaling-gap",
                   dedup_key="journaling-gap", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "dup:journaling-gap"


def test_dedup_expired_delivers():
    st = empty_state()
    st["dedup"]["k"] = 1000.0 - pro.DEDUP_TTL_SEC - 1
    d = pro.decide(base_policy(), st, kind="x", dedup_key="k", now=1000.0, now_dt=NOON)
    assert d.deliver is True


# ─── companion channel (Phase 7 — proactive rail) ────────────────────────────
def test_companion_channel_is_valid():
    assert "companion" in pro.VALID_CHANNELS


def test_companion_channel_not_default():
    # Opt-in only — a plain proactive_notify must not push into the rail.
    assert "companion" not in pro.DEFAULT_CHANNELS
    d = pro.decide(base_policy(), empty_state(), kind="deadline", now=1000.0, now_dt=NOON)
    assert "companion" not in d.channels


def test_companion_channel_honored_when_requested():
    d = pro.decide(base_policy(), empty_state(), kind="deadline",
                   requested_channels=["companion"], now=1000.0, now_dt=NOON)
    assert d.deliver is True
    assert d.channels == ("companion",)


# ─── mute ────────────────────────────────────────────────────────────────────
def test_mute_suppresses():
    pol = base_policy(kinds={"today-load": {"mute": True}})
    d = pro.decide(pol, empty_state(), kind="today-load", now=1000.0, now_dt=NOON)
    assert d.deliver is False
    assert d.reason == "muted:today-load"


def test_mute_not_bypassed_by_critical():
    pol = base_policy(kinds={"today-load": {"mute": True}})
    d = pro.decide(pol, empty_state(), kind="today-load", urgency="critical", now=1000.0, now_dt=NOON)
    assert d.deliver is False  # mute is intentional; critical must not override it


# ─── record_sent ─────────────────────────────────────────────────────────────
def test_record_sent_rolls_and_counts():
    st = empty_state()
    pro.record_sent(st, "deadline", "dk", now=1000.0, now_dt=NOON)
    assert st["last_sent"]["deadline"] == 1000.0
    assert st["last_sent"]["_any"] == 1000.0
    assert st["day"]["date"] == NOON.date().isoformat()
    assert st["day"]["total"] == 1
    assert st["day"]["kinds"]["deadline"] == 1
    assert "dk" in st["dedup"]


def test_record_sent_new_day_resets_total():
    st = empty_state()
    st["day"] = {"date": "2020-01-01", "total": 9, "kinds": {"x": 9}}
    pro.record_sent(st, "deadline", None, now=1000.0, now_dt=NOON)
    assert st["day"]["total"] == 1  # rolled to today


# ─── audit log ───────────────────────────────────────────────────────────────
def test_log_roundtrip_newest_first(tmp_path):
    pro.append_log(tmp_path, {"iso": "2026-01-01T00:00:00", "text": "one", "delivered": True})
    pro.append_log(tmp_path, {"iso": "2026-01-02T00:00:00", "text": "two", "delivered": False})
    rows = pro.read_log(tmp_path, 10)
    assert [r["text"] for r in rows] == ["two", "one"]  # newest first
    delivered = pro.read_log(tmp_path, 10, delivered_only=True)
    assert [r["text"] for r in delivered] == ["one"]
