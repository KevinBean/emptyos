"""Unit tests for the proactive-dispatch gate (emptyos/sdk/proactive.py).

Pure — no daemon, no kernel. Runs as: python -m pytest tests/test_unit_proactive.py
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

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


# --- own_budget: a separate allowance, NOT an exemption ---------------------
#
# Added 2026-09-16 for the telegram `file` kind. A clip the user asked for was
# refused because eight ordinary reminders had already spent the day's cap, and
# a per-kind `daily_cap` alone could not fix it: `decide` checks the per-kind cap
# first but then the GLOBAL cap, so the kind was still blocked. These pin both
# halves — the exemptions that were added, and the gates that must still bite.

def own_policy(**kind_cfg):
    cfg = {"own_budget": True}
    cfg.update(kind_cfg)
    return base_policy(kinds={"file": cfg})


def spent_day(total=100, **kinds):
    st = empty_state()
    st["day"] = {"date": pro._local_date(), "total": total, "kinds": dict(kinds)}
    return st


def test_own_budget_delivers_when_the_global_cap_is_spent():
    d = pro.decide(own_policy(), spent_day(total=100), kind="file")
    assert d.deliver, d.reason


def test_a_kind_without_own_budget_is_still_held_by_the_global_cap():
    """Control: proves the test above measures own_budget, not a broken cap."""
    d = pro.decide(base_policy(), spent_day(total=100), kind="file")
    assert not d.deliver and d.reason == "daily-cap"


def test_own_budget_still_obeys_its_own_daily_cap():
    d = pro.decide(own_policy(daily_cap=3), spent_day(total=0, file=3), kind="file")
    assert not d.deliver and d.reason == "daily-cap:file"


def test_own_budget_still_obeys_quiet_hours():
    p = own_policy()
    p.update({"quiet_start": "22:00", "quiet_end": "07:00"})
    d = pro.decide(p, empty_state(), kind="file",
                   now_dt=datetime(2026, 9, 16, 3, 0))
    assert not d.deliver and d.reason == "quiet-hours"


def test_own_budget_still_dedupes():
    st = empty_state()
    pro.record_sent(st, "file", "same-file", own_budget=True)
    d = pro.decide(own_policy(), st, kind="file", dedup_key="same-file")
    assert not d.deliver and d.reason.startswith("dup:")


def test_own_budget_is_not_held_by_the_global_gap():
    st = empty_state()
    pro.record_sent(st, "reminder", None)  # spends the global gap
    p = own_policy()
    p["min_gap_sec"] = 3600
    assert pro.decide(p, st, kind="file").deliver


def test_own_budget_still_obeys_its_own_gap():
    st = empty_state()
    pro.record_sent(st, "file", None, own_budget=True)
    d = pro.decide(own_policy(min_gap_sec=3600), st, kind="file")
    assert not d.deliver and d.reason == "gap:file"


def test_an_own_budget_delivery_does_not_spend_the_global_allowance():
    """The half that makes it a separate budget rather than a free pass: a file
    push must not consume the allowance the reminders are queued against."""
    st = empty_state()
    pro.record_sent(st, "file", None, own_budget=True)
    assert st["day"]["total"] == 0
    assert st["day"]["kinds"]["file"] == 1
    assert pro._ANY not in st["last_sent"]
    assert "file" in st["last_sent"]


def test_an_ordinary_delivery_still_spends_the_global_allowance():
    st = empty_state()
    pro.record_sent(st, "reminder", None)
    assert st["day"]["total"] == 1 and pro._ANY in st["last_sent"]


def test_has_own_budget_reads_the_policy():
    assert pro.has_own_budget(own_policy(), "file") is True
    assert pro.has_own_budget(own_policy(), "reminder") is False
    assert pro.has_own_budget(base_policy(), "file") is False


def test_own_budget_does_not_reset_the_global_day_total():
    """The reset branch runs for every kind; only the increment is skipped. If
    the reset moved inside the own_budget guard — or the increment out of it —
    a file push would hand the user a fresh set of reminder slots."""
    st = spent_day(total=8, reminder=8)
    pro.record_sent(st, "file", None, own_budget=True)
    assert st["day"]["total"] == 8
    assert st["day"]["kinds"]["file"] == 1
    assert st["day"]["kinds"]["reminder"] == 8


def test_own_budget_without_its_own_cap_borrows_the_global_number():
    """A separate budget, never an unbounded one: with no per-kind cap the kind
    gets an allowance the same SIZE as the global one, measured on its own
    counter. Without this, `own_budget: true` alone would deliver 40 clips."""
    p = own_policy()  # no daily_cap of its own
    p["daily_cap"] = 3
    assert pro.decide(p, spent_day(total=0, file=2), kind="file").deliver
    d = pro.decide(p, spent_day(total=0, file=3), kind="file")
    assert not d.deliver and d.reason == "daily-cap:file"


def test_own_budget_without_its_own_gap_borrows_the_global_number():
    st = empty_state()
    pro.record_sent(st, "file", None, own_budget=True)
    p = own_policy()
    p["min_gap_sec"] = 3600
    d = pro.decide(p, st, kind="file")
    assert not d.deliver and d.reason == "gap:file"


def test_an_own_budget_send_still_rolls_the_day():
    """The reviewer's scenario: the first nudge of a new day is a file push.
    The date must roll and yesterday's total must clear, or the stale total
    keeps refusing today's reminders (or, mutated the other way, an own_budget
    send silently resets a total it should not touch — see the test above)."""
    st = empty_state()
    st["day"] = {"date": "2026-09-15", "total": 8, "kinds": {"reminder": 8}}
    pro.record_sent(st, "file", None, own_budget=True)
    assert st["day"]["date"] == pro._local_date()
    assert st["day"]["total"] == 0
    assert st["day"]["kinds"] == {"file": 1}


# --- default per-kind config must survive a stored policy -------------------

def test_a_fresh_store_carries_the_default_kind_config(tmp_path):
    """DEFAULT_POLICY['kinds'] was {} for a long time, so nothing noticed that a
    stored `kinds` map REPLACED it. The moment a default appeared — `test` on its
    own allowance — any user who had ever muted anything would have lost it."""
    pol = pro.load_policy(tmp_path)
    assert pol["kinds"]["test"]["own_budget"] is True


def test_a_stored_kinds_map_does_not_erase_the_defaults(tmp_path):
    pro.save_policy(tmp_path, {"enabled": True, "kinds": {"reminder": {"mute": True}}})
    pol = pro.load_policy(tmp_path)
    assert pol["kinds"]["reminder"] == {"mute": True}
    assert pol["kinds"]["test"]["own_budget"] is True, (
        "a stored kinds map replaced the defaults — the probe kind would start "
        "spending the user's daily allowance again")


def test_a_stored_key_overrides_the_default_for_that_kind(tmp_path):
    pro.save_policy(tmp_path, {"kinds": {"test": {"daily_cap": 2}}})
    pol = pro.load_policy(tmp_path)
    assert pol["kinds"]["test"]["daily_cap"] == 2       # user's value wins
    assert pol["kinds"]["test"]["own_budget"] is True   # untouched default stays


def test_a_probe_does_not_spend_the_daily_allowance(tmp_path):
    """The behaviour all of the above exists for: running the sys suite (or
    clicking 'send a test nudge') must not consume the day's real budget."""
    pol = pro.load_policy(tmp_path)
    pol.update({"enabled": True, "quiet_start": "", "quiet_end": "", "min_gap_sec": 0})
    st = spent_day(total=8, reminder=8)
    assert pro.decide(pol, st, kind="test").deliver
    pro.record_sent(st, "test", None, own_budget=pro.has_own_budget(pol, "test"))
    assert st["day"]["total"] == 8


# --- the merge must not freeze today's defaults into the user's file --------

def test_saving_a_loaded_policy_does_not_persist_the_defaults(tmp_path):
    """Every writer does load -> mutate -> save, and load returns the defaults
    MERGED IN. Writing that view back froze a copy of DEFAULT_POLICY into the
    user's store; because a stored key wins per key, a later default change could
    then never reach them — the merge added FOR migration would have destroyed
    migration for exactly the keys it was added to carry. One mute did it."""
    pol = pro.load_policy(tmp_path)          # defaults merged in
    pol["kinds"].setdefault("reminder", {})["mute"] = True   # simulate api_mute
    pro.save_policy(tmp_path, pol)

    on_disk = json.loads((tmp_path / "proactive" / "policy.json").read_text(encoding="utf-8"))
    assert "test" not in on_disk["kinds"], (
        "the probe default was written into the user's file — a later change to "
        "DEFAULT_POLICY['kinds']['test'] could never reach this user again")
    assert on_disk["kinds"]["reminder"] == {"mute": True}


def test_a_later_default_change_still_reaches_a_user_who_has_saved(tmp_path):
    """The property the strip exists to protect, asserted end to end."""
    pol = pro.load_policy(tmp_path)
    pol["kinds"].setdefault("reminder", {})["mute"] = True
    pro.save_policy(tmp_path, pol)

    with patch.dict(pro.DEFAULT_POLICY["kinds"]["test"], {"daily_cap": 99}):
        assert pro.load_policy(tmp_path)["kinds"]["test"]["daily_cap"] == 99


def test_a_deliberate_override_equal_to_the_default_is_not_persisted(tmp_path):
    """Dropping it is safe precisely because the default re-supplies it on load."""
    pro.save_policy(tmp_path, {"kinds": {"test": {"own_budget": True}}})
    on_disk = json.loads((tmp_path / "proactive" / "policy.json").read_text(encoding="utf-8"))
    assert on_disk["kinds"] == {}
    assert pro.load_policy(tmp_path)["kinds"]["test"]["own_budget"] is True


# --- malformed stored values must not destroy the defaults ------------------

def test_a_stored_null_kinds_map_does_not_wipe_the_defaults(tmp_path):
    """`isinstance(None, dict)` is False, so a stored null fell through to the
    plain branch, replaced the merged map, and the trailing guard reset it to {}
    — defaults gone and the probe back on the user's daily budget."""
    (tmp_path / "proactive").mkdir(parents=True, exist_ok=True)
    (tmp_path / "proactive" / "policy.json").write_text(
        '{"enabled": true, "kinds": null}', encoding="utf-8")
    assert pro.load_policy(tmp_path)["kinds"]["test"]["own_budget"] is True


def test_a_stored_non_dict_kind_config_keeps_its_default(tmp_path):
    (tmp_path / "proactive").mkdir(parents=True, exist_ok=True)
    (tmp_path / "proactive" / "policy.json").write_text(
        '{"kinds": {"test": "garbage"}}', encoding="utf-8")
    assert pro.load_policy(tmp_path)["kinds"]["test"]["own_budget"] is True


# --- the probe's own allowance must actually be reachable -------------------

def test_two_probes_in_a_row_both_deliver(tmp_path):
    """An own_budget kind with no min_gap_sec borrows the GLOBAL 1800s as its own
    gap, so a second click inside 30 min was refused `gap:test` and the per-kind
    daily_cap was unreachable (a hard ceiling of 48/day sat under it). The gap
    exists to stop the system talking unprompted; a probe is a button press."""
    pol = pro.load_policy(tmp_path)
    pol.update({"enabled": True, "quiet_start": "", "quiet_end": ""})
    st = empty_state()
    assert pro.decide(pol, st, kind="test").deliver
    pro.record_sent(st, "test", None, own_budget=True)
    d = pro.decide(pol, st, kind="test")
    assert d.deliver, f"a second probe was refused: {d.reason}"
