"""Tests for the autopilot per-actor budget cap (borrow #3, 2026-06-09).

Pure SDK tests against a tmp_path data_dir; no daemon required. Covers the
budget ledger (set/record/within/status, monthly window roll) and its
integration into ``decide()`` behind the ``enforce_budget`` flag — which, like
``auto_stable``, ships dark: with it off, ``decide`` is byte-for-byte the
pre-budget behaviour even for an over-spent actor.
"""

from __future__ import annotations

import json

import pytest

from emptyos.sdk.autopilot import (
    all_budgets,
    budget_status,
    decide,
    record_spend,
    save_grant,
    set_budget,
    within_budget,
)

ELIGIBLE_VERB = "task.add"  # in DEFAULT_ELIGIBLE_VERBS (the legacy floor)
SCOPES = ["session:room-x", "room:room-x", "global"]
ACTOR = "agent-jobscout"


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


def _write_budgets(data_dir, payload):
    root = data_dir / "autopilot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "budgets.json").write_text(json.dumps(payload), encoding="utf-8")


# ─── ledger basics ──────────────────────────────────────────────────────


class TestBudgetLedger:
    def test_untracked_actor_is_within_budget(self, data_dir):
        assert within_budget(data_dir, "nobody") is True

    def test_no_cap_never_gates_however_much_spent(self, data_dir):
        record_spend(data_dir, ACTOR, 9999.0)
        assert within_budget(data_dir, ACTOR) is True
        s = budget_status(data_dir, ACTOR)
        assert s["monthly_cap_usd"] is None
        assert s["remaining_usd"] is None
        assert s["over"] is False
        assert s["spent_usd"] == pytest.approx(9999.0)

    def test_spend_accrues_then_crosses_cap(self, data_dir):
        set_budget(data_dir, ACTOR, 10.0)
        record_spend(data_dir, ACTOR, 4.0)
        assert within_budget(data_dir, ACTOR) is True
        record_spend(data_dir, ACTOR, 6.0)  # now at exactly the cap
        assert within_budget(data_dir, ACTOR) is False  # spent >= cap
        s = budget_status(data_dir, ACTOR)
        assert s["spent_usd"] == pytest.approx(10.0)
        assert s["remaining_usd"] == pytest.approx(0.0)
        assert s["over"] is True

    def test_set_budget_preserves_accrued_spend(self, data_dir):
        record_spend(data_dir, ACTOR, 7.0)
        set_budget(data_dir, ACTOR, 20.0)  # cap set AFTER spend
        s = budget_status(data_dir, ACTOR)
        assert s["spent_usd"] == pytest.approx(7.0)
        assert s["remaining_usd"] == pytest.approx(13.0)
        assert within_budget(data_dir, ACTOR) is True

    def test_clearing_cap_reopens(self, data_dir):
        set_budget(data_dir, ACTOR, 5.0)
        record_spend(data_dir, ACTOR, 6.0)
        assert within_budget(data_dir, ACTOR) is False
        set_budget(data_dir, ACTOR, None)  # clear
        assert within_budget(data_dir, ACTOR) is True
        # zero / negative also clears
        set_budget(data_dir, ACTOR, 5.0)
        set_budget(data_dir, ACTOR, 0)
        assert within_budget(data_dir, ACTOR) is True

    def test_negative_spend_is_noop(self, data_dir):
        set_budget(data_dir, ACTOR, 10.0)
        record_spend(data_dir, ACTOR, -5.0)
        assert budget_status(data_dir, ACTOR)["spent_usd"] == pytest.approx(0.0)

    def test_all_budgets_lists_every_actor(self, data_dir):
        set_budget(data_dir, "a", 10.0)
        record_spend(data_dir, "b", 1.0)
        ids = {s["actor_id"] for s in all_budgets(data_dir)}
        assert ids == {"a", "b"}

    def test_empty_actor_id_rejected(self, data_dir):
        with pytest.raises(ValueError):
            set_budget(data_dir, "", 10.0)
        with pytest.raises(ValueError):
            record_spend(data_dir, "", 1.0)


# ─── monthly window roll ────────────────────────────────────────────────


class TestWindowRoll:
    def test_stale_window_resets_spend_on_read(self, data_dir):
        # An over-cap spend recorded in a past month rolls to 0 on the first
        # read in the new month — no cron needed.
        _write_budgets(data_dir, {"actors": {ACTOR: {
            "monthly_cap_usd": 10.0,
            "spent_usd": 50.0,
            "window_start": "2020-01-15T00:00:00+00:00",
        }}})
        assert within_budget(data_dir, ACTOR) is True
        s = budget_status(data_dir, ACTOR)
        assert s["spent_usd"] == pytest.approx(0.0)
        assert s["monthly_cap_usd"] == pytest.approx(10.0)  # cap survives the roll

    def test_record_in_new_month_starts_fresh(self, data_dir):
        _write_budgets(data_dir, {"actors": {ACTOR: {
            "monthly_cap_usd": 10.0,
            "spent_usd": 50.0,
            "window_start": "2020-01-15T00:00:00+00:00",
        }}})
        record_spend(data_dir, ACTOR, 3.0)
        assert budget_status(data_dir, ACTOR)["spent_usd"] == pytest.approx(3.0)


# ─── decide() integration ───────────────────────────────────────────────


class TestDecideBudget:
    def _over_budget(self, data_dir):
        set_budget(data_dir, "claude-cli", 5.0)
        record_spend(data_dir, "claude-cli", 6.0)

    def test_over_budget_gates_a_grant_auto(self, data_dir):
        save_grant(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:room-x",
        )
        self._over_budget(data_dir)
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES,
            enforce_budget=True,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "over-budget"
        # the grant that *would* have fired is preserved for the audit trail
        assert d["grant_id"]

    def test_over_budget_gates_a_stable_default_auto(self, data_dir):
        self._over_budget(data_dir)
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES,
            auto_stable=True, enforce_budget=True,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "over-budget"

    def test_within_budget_leaves_auto_untouched(self, data_dir):
        set_budget(data_dir, "claude-cli", 100.0)
        record_spend(data_dir, "claude-cli", 1.0)
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES,
            auto_stable=True, enforce_budget=True,
        )
        assert d["action"] == "auto"
        assert d["reason"] == "stable-default"

    def test_enforce_off_is_byte_for_byte_even_when_over(self, data_dir):
        # Dark by default: an over-budget actor still autos when the flag is off.
        self._over_budget(data_dir)
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES,
            auto_stable=True, enforce_budget=False,
        )
        assert d["action"] == "auto"
        assert d["reason"] == "stable-default"

    def test_budget_does_not_resurrect_a_gate(self, data_dir):
        # No grant, no stable-default → gate "no-grant"; budget never flips a
        # gate to auto, only auto to gate.
        self._over_budget(data_dir)
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES,
            enforce_budget=True,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "no-grant"
