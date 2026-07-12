"""Tests for the autopilot pivot (2026-06-07) — holds + decide().

Pure SDK tests against a tmp_path data_dir; no daemon required. Covers the
inverted default: a `stable`/eligible verb auto-runs by default when
``auto_stable`` is on (no grant needed), unless an active **hold** re-gates
it. With ``auto_stable`` off, ``decide`` is byte-for-byte the pre-pivot
grant-or-gate behaviour, proving the pivot ships dark.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.autopilot import (
    decide,
    is_held,
    load_holds,
    revoke_hold,
    revoke_hold_scope,
    save_grant,
    save_hold,
)


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


# task.add is in DEFAULT_ELIGIBLE_VERBS (the legacy floor used when
# eligible=None), so it is `stable`. note.create is not — it's `never`.
ELIGIBLE_VERB = "task.add"
NEVER_VERB = "note.create"
SCOPES = ["session:room-x", "room:room-x", "global"]


# ─── decide(): dark default (auto_stable off) == pre-pivot ──────────────


class TestDecideDarkDefault:
    def test_eligible_verb_gates_without_grant(self, data_dir):
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=False,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "no-grant"

    def test_eligible_verb_autos_with_grant(self, data_dir):
        save_grant(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:room-x",
        )
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=False,
        )
        assert d["action"] == "auto"
        assert d["reason"] == "grant"
        assert d["grant_id"]


# ─── decide(): pivot on (auto_stable) — stable verbs auto by default ────


class TestDecideAutoStable:
    def test_eligible_verb_autos_by_default_no_grant(self, data_dir):
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=True,
        )
        assert d["action"] == "auto"
        assert d["reason"] == "stable-default"
        assert d["grant_id"] is None

    def test_never_verb_still_gates(self, data_dir):
        # A non-eligible verb never auto-runs via the stable-default path,
        # and has no grant → gate.
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=NEVER_VERB, scope_candidates=SCOPES, auto_stable=True,
        )
        assert d["action"] == "gate"

    def test_hold_regates_a_stable_verb(self, data_dir):
        save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:room-x",
        )
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=True,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "held"
        assert d["hold_id"]

    def test_hold_beats_grant(self, data_dir):
        # Even with a grant present, an explicit hold gates.
        save_grant(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:room-x",
        )
        save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:room-x",
        )
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=True,
        )
        assert d["action"] == "gate"
        assert d["reason"] == "held"

    def test_hold_scoped_out_does_not_apply(self, data_dir):
        # A hold in a different scope doesn't gate.
        save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern=ELIGIBLE_VERB, scope="room:other-room",
        )
        d = decide(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb=ELIGIBLE_VERB, scope_candidates=SCOPES, auto_stable=True,
        )
        assert d["action"] == "auto"


# ─── holds store ───────────────────────────────────────────────────────


class TestHoldsStore:
    def test_save_and_load(self, data_dir):
        h = save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern="task.*", scope="global",
        )
        assert h["id"].startswith("hold-")
        assert len(load_holds(data_dir)) == 1

    def test_revoke(self, data_dir):
        h = save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern="task.*", scope="global",
        )
        assert revoke_hold(data_dir, h["id"]) is True
        assert load_holds(data_dir) == []
        assert revoke_hold(data_dir, h["id"]) is False

    def test_is_held_namespace_glob(self, data_dir):
        save_hold(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb_pattern="task.*", scope="global",
        )
        assert is_held(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb="task.add", scope_candidates=["global"],
        )
        # Different app — not held.
        assert not is_held(
            data_dir, actor_type="cli", actor_id="claude-cli",
            verb="kb.add", scope_candidates=["global"],
        )

    def test_hold_rejects_bare_wildcard(self, data_dir):
        with pytest.raises(ValueError):
            save_hold(
                data_dir, actor_type="cli", actor_id="claude-cli",
                verb_pattern="*", scope="global",
            )

    def test_hold_rejects_cross_app(self, data_dir):
        with pytest.raises(ValueError):
            save_hold(
                data_dir, actor_type="cli", actor_id="claude-cli",
                verb_pattern="*.send", scope="global",
            )


# ─── revoke_hold_scope — the rooms "pause auto" toggle-off (hold-side mirror
#     of revoke_scope). Used by the rooms ⏸ chip. ────────────────────────────


class TestRevokeHoldScope:
    def test_removes_only_matching_scope(self, data_dir):
        save_hold(data_dir, actor_type="agent", actor_id="",
                  verb_pattern="task.*", scope="session:room-x")
        save_hold(data_dir, actor_type="cli", actor_id="",
                  verb_pattern="capture.*", scope="session:room-x")
        save_hold(data_dir, actor_type="agent", actor_id="",
                  verb_pattern="task.*", scope="session:room-y")  # different scope
        n = revoke_hold_scope(data_dir, "session:room-x")
        assert n == 2
        remaining = load_holds(data_dir)
        assert len(remaining) == 1
        assert remaining[0]["scope"] == "session:room-y"

    def test_no_match_returns_zero(self, data_dir):
        assert revoke_hold_scope(data_dir, "session:nope") == 0

    def test_pause_then_resume_round_trip(self, data_dir):
        # auto_stable ON + eligible verb → auto by default.
        base = dict(actor_type="agent", actor_id="somebot", verb=ELIGIBLE_VERB,
                    scope_candidates=SCOPES, auto_stable=True)
        assert decide(data_dir, **base)["action"] == "auto"
        # Pause the room (scope hold, any actor of type) → gates as "held".
        save_hold(data_dir, actor_type="agent", actor_id="",
                  verb_pattern="task.*", scope="session:room-x")
        paused = decide(data_dir, **base)
        assert paused["action"] == "gate" and paused["reason"] == "held"
        # Resume the whole scope → auto again.
        assert revoke_hold_scope(data_dir, "session:room-x") == 1
        assert decide(data_dir, **base)["action"] == "auto"
