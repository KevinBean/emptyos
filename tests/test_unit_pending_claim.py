"""Unit tests for the shared atomic claim (emptyos/sdk/pending_claim.py).

Pure — no daemon, no kernel, no app instance. The in-process consumers are
covered end-to-end by tests/test_unit_rooms_logic.py::TestApplyPendingConcurrency.

Run: python -m pytest tests/test_unit_pending_claim.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from emptyos.sdk.pending_claim import (
    CLAIMED,
    claim_pending,
    is_unknown_effect,
)


def _store(*actions: dict):
    """Dict-backed load/save pair standing in for the JSON-file helpers."""
    db = {a["id"]: dict(a) for a in actions}
    return db, (lambda aid: (dict(db[aid]) if aid in db else None)), \
        (lambda a: db.__setitem__(a["id"], dict(a)))


def _pending(aid="a1", **extra):
    return {"id": aid, "status": "pending", "app": "task", "method": "add", **extra}


class TestClaimPending:
    @pytest.mark.asyncio
    async def test_winner_gets_action_and_persists_claim(self):
        db, load, save = _store(_pending())
        action, err = await claim_pending(asyncio.Lock(), load, save, "a1")
        assert err is None
        assert action["status"] == CLAIMED
        assert action["attempt"] == 1
        assert action["claimed_ts"]
        # Persisted, not just returned — that is what survives a crash.
        assert db["a1"]["status"] == CLAIMED
        assert db["a1"]["attempt"] == 1

    @pytest.mark.asyncio
    async def test_missing_action(self):
        _, load, save = _store()
        action, err = await claim_pending(asyncio.Lock(), load, save, "nope")
        assert action is None
        assert err == {"error": "action not found"}

    @pytest.mark.asyncio
    async def test_already_resolved_is_refused_with_current_status(self):
        _, load, save = _store(_pending(status="applied"))
        action, err = await claim_pending(asyncio.Lock(), load, save, "a1")
        assert action is None
        assert err == {"error": "already applied"}

    @pytest.mark.asyncio
    async def test_exactly_one_of_many_concurrent_claims_wins(self):
        db, load, save = _store(_pending())
        lock = asyncio.Lock()
        results = await asyncio.gather(*[
            claim_pending(lock, load, save, "a1") for _ in range(8)
        ])
        winners = [a for a, e in results if e is None]
        losers = [e for a, e in results if e is not None]
        assert len(winners) == 1
        assert len(losers) == 7
        assert all(e == {"error": f"already {CLAIMED}"} for e in losers)

    @pytest.mark.asyncio
    async def test_reject_is_a_claim_so_apply_and_reject_cannot_both_win(self):
        db, load, save = _store(_pending())
        lock = asyncio.Lock()
        (a1, e1), (a2, e2) = await asyncio.gather(
            claim_pending(lock, load, save, "a1"),
            claim_pending(lock, load, save, "a1", claim_status="rejected"),
        )
        assert [e1, e2].count(None) == 1, "both verbs won"
        assert db["a1"]["status"] in (CLAIMED, "rejected")

    @pytest.mark.asyncio
    async def test_lock_is_actually_held_across_the_transition(self):
        """A load that yields must not let a second caller in mid-claim."""
        db, _, save = _store(_pending())
        order: list[str] = []

        def slow_load(aid):
            order.append("load")
            return dict(db[aid]) if aid in db else None

        def tracking_save(a):
            order.append("save")
            db[a["id"]] = dict(a)

        lock = asyncio.Lock()
        await asyncio.gather(*[
            claim_pending(lock, slow_load, tracking_save, "a1") for _ in range(3)
        ])
        # The winner's load+save are adjacent; losers only load. Never
        # load,load,...,save — which is the interleaving that double-executes.
        assert order[:2] == ["load", "save"], order

    @pytest.mark.asyncio
    async def test_custom_from_status_gates_the_transition(self):
        _, load, save = _store(_pending(status="held"))
        _, err = await claim_pending(asyncio.Lock(), load, save, "a1")
        assert err == {"error": "already held"}
        action, err2 = await claim_pending(
            asyncio.Lock(), load, save, "a1", from_status="held",
        )
        assert err2 is None and action["status"] == CLAIMED

    @pytest.mark.asyncio
    async def test_injected_clock_is_used(self):
        _, load, save = _store(_pending())
        action, _ = await claim_pending(
            asyncio.Lock(), load, save, "a1", now=lambda: "FIXED",
        )
        assert action["claimed_ts"] == "FIXED"


class TestUnknownEffect:
    def test_stuck_at_claim_marker_is_unknown_effect(self):
        assert is_unknown_effect({"status": CLAIMED}) is True

    def test_resolved_states_are_not(self):
        for st in ("pending", "applied", "rejected", "failed"):
            assert is_unknown_effect({"status": st}) is False

    def test_missing_status_is_not(self):
        assert is_unknown_effect({}) is False


# ── is_past_ttl (extracted on its 2nd consumer) ───────────────────────


class TestIsPastTtl:
    """The birth-timestamp + TTL expiry shape, shared by context/store
    originals and telegram Apply cards. Its predecessors each hand-rolled
    the parsing; the naive-timestamp case is why that mattered."""

    def _f(self):
        from emptyos.sdk.utils import is_past_ttl
        return is_past_ttl

    def test_fresh_is_not_expired(self):
        assert self._f()("2026-07-25T09:00:00+00:00", 3600,
                         "2026-07-25T09:30:00+00:00") is False

    def test_aged_is_expired(self):
        assert self._f()("2026-07-25T09:00:00+00:00", 3600,
                         "2026-07-25T11:00:00+00:00") is True

    def test_exact_boundary_counts_as_expired(self):
        assert self._f()("2026-07-25T09:00:00+00:00", 3600,
                         "2026-07-25T10:00:00+00:00") is True

    def test_zero_or_negative_ttl_disables(self):
        for ttl in (0, -1):
            assert self._f()("2020-01-01T00:00:00+00:00", ttl,
                             "2026-07-25T09:00:00+00:00") is False

    def test_unparseable_born_fails_open(self):
        for bad in ("nonsense", "", None):
            assert self._f()(bad, 3600, "2026-07-25T09:00:00+00:00") is False

    def test_naive_stored_timestamp_still_expires(self):
        """The bug the extraction fixes. A naive `ts` compared against an aware
        `now` raises TypeError, which the old local try/except turned into
        'never expires' — and several writers here emit naive isoformat()."""
        assert self._f()("2026-07-25T09:00:00", 3600,
                         "2026-07-25T11:00:00+00:00") is True

    def test_date_only_stored_timestamp_parses(self):
        assert self._f()("2026-07-20", 3600, "2026-07-25T09:00:00+00:00") is True

    def test_trailing_z_parses(self):
        assert self._f()("2026-07-25T09:00:00Z", 3600,
                         "2026-07-25T11:00:00+00:00") is True

    def test_accepts_a_datetime_now(self):
        from datetime import datetime, timezone
        assert self._f()("2026-07-25T09:00:00+00:00", 3600,
                         datetime(2026, 7, 25, 11, tzinfo=timezone.utc)) is True

    def test_unparseable_now_falls_back_to_real_clock(self):
        """A bad reference clock must not silently freeze time at the epoch."""
        assert self._f()("2020-01-01T00:00:00+00:00", 3600, "garbage") is True

    def test_future_timestamp_is_not_expired(self):
        assert self._f()("2027-01-01T00:00:00+00:00", 3600,
                         "2026-07-25T09:00:00+00:00") is False
