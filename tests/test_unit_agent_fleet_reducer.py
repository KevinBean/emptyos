"""Unit tests for the pure Agent Fleet session-state reducer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REDUCER = (
    Path(__file__).resolve().parents[1]
    / "apps/extension/dev/agent_fleet/reducer.py"
)
_SPEC = importlib.util.spec_from_file_location("agent_fleet_reducer_under_test", _REDUCER)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)

reduce_session_state = _MOD.reduce_session_state
ttl_transition = _MOD.ttl_transition


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"event": "SessionStart"}, "idle"),
        ({"event": "UserPromptSubmit"}, "working"),
        ({"event": "PermissionRequest"}, "blocked"),
        ({"event": "Notification", "notification_type": "permission_prompt"}, "blocked"),
        ({"event": "Stop"}, "idle"),
        ({"event": "SessionEnd"}, "ended"),
    ],
)
def test_exact_hook_state_table(event, expected):
    assert reduce_session_state("unknown", event) == expected


@pytest.mark.unit
def test_non_permission_notification_is_only_a_heartbeat():
    event = {"event": "Notification", "notification_type": "idle_prompt"}
    assert reduce_session_state("working", event) == "working"


@pytest.mark.unit
def test_unknown_hook_event_does_not_change_state():
    assert reduce_session_state("idle", {"event": "SubagentStart"}) == "idle"


@pytest.mark.unit
def test_multi_turn_stop_never_means_ended():
    state = "unknown"
    ended_edges = 0
    for event_name in (
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
        "UserPromptSubmit",
        "Stop",
    ):
        previous = state
        state = reduce_session_state(state, {"event": event_name})
        ended_edges += int(previous != "ended" and state == "ended")
    assert state == "idle"
    assert ended_edges == 0


@pytest.mark.unit
def test_repeated_permission_request_has_one_blocked_edge():
    state = "working"
    blocked_edges = 0
    for _ in range(2):
        previous = state
        state = reduce_session_state(state, {"event": "PermissionRequest"})
        blocked_edges += int(previous != "blocked" and state == "blocked")
    assert state == "blocked"
    assert blocked_edges == 1


@pytest.mark.unit
def test_ttl_is_unknown_then_ended_with_one_ended_edge():
    transitions = ttl_transition("working")
    assert transitions == ("unknown", "ended")
    assert sum(state == "ended" for state in transitions) == 1


# ── phase-mapping parity (added by agent-diff-review, 2026-07-17) ────────────
#
# agent_fleet added five words to the GLOBAL _PHASE_BY_STATUS map in
# emptyos/sdk/run_status.py, which every harness shares. One of them ("unknown")
# shadowed normalize_run_status's `finished`-aware default and reported a LIVE
# session as "done" — hiding it from the run-center panel the fleet exists to
# fill. These pin the contract in both directions.

from emptyos.sdk.run_status import _PHASE_BY_STATUS, normalize_run_status


def test_live_unknown_session_is_running_not_done():
    """REGRESSION. A session with no `finished` has NOT finished. The fleet
    meets every already-running session mid-stream the moment the feature flag
    is enabled, so this is the common case at the moment of first evaluation —
    not an edge case."""
    assert normalize_run_status("unknown", finished="") == "running"


def test_ttl_ended_session_is_done():
    """The other meaning of the TTL path still resolves terminal."""
    assert normalize_run_status("ended", finished="2026-07-17T00:00:00Z") == "done"
    assert normalize_run_status("unknown", finished="2026-07-17T00:00:00Z") == "done"


def test_unknown_is_not_statically_mapped():
    """The fix IS the absence of this key — a future 'tidy-up' that re-adds it
    silently re-hides live sessions. Fail here instead."""
    assert "unknown" not in _PHASE_BY_STATUS, (
        "'unknown' must resolve via the finished-aware default, not a static entry"
    )


def test_fleet_states_do_not_collide_with_other_harness_vocabularies():
    """agent_fleet put five generic English words into a map shared by every
    harness. If another harness ever emits one natively, its phase silently
    changes. Pin the ones we added."""
    assert _PHASE_BY_STATUS["idle"] == "running"
    assert _PHASE_BY_STATUS["working"] == "running"
    assert _PHASE_BY_STATUS["blocked"] == "needs-review"
    assert _PHASE_BY_STATUS["ended"] == "done"
