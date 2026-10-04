"""Tests for autopilot.replace_namespace_grants — the ⚡ auto-accept toggle body.

Pure SDK tests against a tmp_path data_dir; no daemon required. Extracted
2026-08-05 from the two identical copies in rooms/autopilot.py and
voice-assistant/pending.py; these cases pin the behaviour both callers had.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.autopilot import (
    load_grants,
    match,
    replace_namespace_grants,
    save_grant,
)

ELIGIBLE = {"task.add", "task.list_today", "capture.add", "journal.add_entry"}
ROOM_ACTORS = [("agent", ""), ("cli", "")]
VOICE_ACTORS = [("voice", "aura")]


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


class TestIssuesOnePerActorPerNamespace:
    def test_voice_shape_one_actor(self, data_dir):
        issued = replace_namespace_grants(
            data_dir, scope="session:aura", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600, rationale="voice toolbar toggle",
        )
        # 3 namespaces (task, capture, journal) x 1 actor
        assert len(issued) == 3
        assert {g["verb_pattern"] for g in issued} == {"task.*", "capture.*", "journal.*"}
        assert {g["actor"]["type"] for g in issued} == {"voice"}
        assert {g["actor"]["id"] for g in issued} == {"aura"}

    def test_rooms_shape_two_actors(self, data_dir):
        issued = replace_namespace_grants(
            data_dir, scope="session:r1", actors=ROOM_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600, rationale="rooms auto-accept toggle",
        )
        assert len(issued) == 6  # 3 namespaces x 2 actor types
        assert {g["actor"]["type"] for g in issued} == {"agent", "cli"}
        # Empty actor_id == "any participant of this type"
        assert {g["actor"]["id"] for g in issued} == {""}

    def test_namespaces_are_sorted(self, data_dir):
        issued = replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600,
        )
        assert [g["verb_pattern"] for g in issued] == ["capture.*", "journal.*", "task.*"]

    def test_metadata_carried_through(self, data_dir):
        issued = replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=60, rationale="why",
        )
        assert all(g["scope"] == "session:r1" for g in issued)
        assert all(g["rationale"] == "why" for g in issued)
        assert all(g["expires_at"] for g in issued)  # ttl -> real expiry


class TestReplacesRatherThanStacks:
    def test_second_call_replaces_scope(self, data_dir):
        replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600,
        )
        replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600,
        )
        assert len(load_grants(data_dir)) == 3  # not 6

    def test_other_scopes_survive(self, data_dir):
        save_grant(
            data_dir, actor_type="voice", actor_id="aura", verb_pattern="task.add",
            scope="global", eligible=ELIGIBLE,
        )
        replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600,
        )
        assert any(g["scope"] == "global" for g in load_grants(data_dir))


class TestEligibilityFloorHolds:
    def test_non_eligible_namespace_is_skipped_not_fatal(self, data_dir):
        """A namespace with no eligible verb raises in save_grant; the sweep
        skips it and keeps going — the behaviour both callers relied on."""
        issued = replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible={"task.add", "publish.deploy"} - {"publish.deploy"},
            ttl_seconds=3600,
        )
        assert [g["verb_pattern"] for g in issued] == ["task.*"]

    def test_empty_eligible_issues_nothing(self, data_dir):
        issued = replace_namespace_grants(
            data_dir, scope="session:r1", actors=VOICE_ACTORS,
            eligible=set(), ttl_seconds=3600,
        )
        assert issued == []
        assert load_grants(data_dir) == []

    def test_issued_grants_actually_fire(self, data_dir):
        """End-to-end: a swept grant is what match() finds at fire time."""
        replace_namespace_grants(
            data_dir, scope="session:r1", actors=ROOM_ACTORS,
            eligible=ELIGIBLE, ttl_seconds=3600,
        )
        assert match(
            data_dir, actor_type="cli", actor_id="claude-cli", verb="task.add",
            scope_candidates=["session:r1"], eligible=ELIGIBLE,
        )
        assert not match(
            data_dir, actor_type="cli", actor_id="claude-cli", verb="task.add",
            scope_candidates=["session:other"], eligible=ELIGIBLE,
        )
