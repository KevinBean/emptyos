"""Tests for emptyos.sdk.autopilot — per-actor + per-verb + per-scope grants.

Pure SDK tests against a tmp_path data_dir; no daemon required. Covers the
verb-glob grammar extension borrowed from Foundry's ApprovalPolicy
(see 2026-05-24 repo review): exact, namespace wildcard, verb-prefix glob.
"""

from __future__ import annotations

import pytest

from emptyos.sdk.autopilot import (
    DEFAULT_ELIGIBLE_VERBS,
    _verb_matches,
    load_grants,
    load_policy,
    match,
    revoke_grant,
    revoke_scope,
    save_grant,
)


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


# ─── _verb_matches grammar ─────────────────────────────────────────


class TestVerbMatchesExact:
    def test_exact_match(self):
        assert _verb_matches("task.add", "task.add")

    def test_exact_no_match(self):
        assert not _verb_matches("task.add", "task.delete")

    def test_different_app_no_match(self):
        assert not _verb_matches("task.add", "kb.add")


class TestVerbMatchesNamespaceWildcard:
    def test_app_wide_matches_any_verb(self):
        assert _verb_matches("task.*", "task.add")
        assert _verb_matches("task.*", "task.list_today")
        assert _verb_matches("task.*", "task.foo_bar_baz")

    def test_app_wide_rejects_other_app(self):
        assert not _verb_matches("task.*", "kb.add")


class TestVerbMatchesPrefixGlob:
    def test_send_prefix_glob(self):
        assert _verb_matches("email.send_*", "email.send_html")
        assert _verb_matches("email.send_*", "email.send_text")

    def test_send_prefix_does_not_match_other_verb(self):
        assert not _verb_matches("email.send_*", "email.delete_account")

    def test_send_prefix_does_not_match_other_app(self):
        assert not _verb_matches("email.send_*", "notifications.send_html")

    def test_suffix_glob(self):
        assert _verb_matches("aura.*_memory", "aura.clear_memory")
        assert _verb_matches("aura.*_memory", "aura.long_term_memory")

    def test_suffix_glob_no_match_when_suffix_differs(self):
        assert not _verb_matches("aura.*_memory", "aura.remember")


class TestVerbMatchesForbidden:
    def test_cross_app_pattern_rejected(self):
        # *.send is forbidden by the spec — the matcher returns False because
        # the app half "*" doesn't equal any concrete app. (save_grant also
        # refuses these at the API surface.)
        assert not _verb_matches("*.send", "email.send")

    def test_pattern_without_dot_rejected(self):
        assert not _verb_matches("task", "task.add")

    def test_verb_without_dot_rejected(self):
        # Defensive: a malformed "verb" with no dot can't match anything
        # other than a literal-equal pattern.
        assert not _verb_matches("task.add", "task")


# ─── save_grant validation ────────────────────────────────────────


class TestSaveGrantEligibility:
    def test_exact_eligible_verb_accepted(self, data_dir):
        g = save_grant(
            data_dir,
            actor_type="agent",
            actor_id="claude-cli",
            verb_pattern="task.add",
            scope="room:abc",
        )
        assert g["verb_pattern"] == "task.add"
        assert g["actor"] == {"type": "agent", "id": "claude-cli"}
        assert g["id"].startswith("grant-")

    def test_exact_non_eligible_verb_rejected(self, data_dir):
        with pytest.raises(ValueError, match="not autopilot-eligible"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="rooms.write_note",  # NOT in default eligibility
                scope="room:abc",
            )

    def test_namespace_glob_with_matching_eligible_accepted(self, data_dir):
        g = save_grant(
            data_dir,
            actor_type="agent",
            actor_id="claude-cli",
            verb_pattern="task.*",  # task.add etc. are eligible
            scope="room:abc",
        )
        assert g["verb_pattern"] == "task.*"

    def test_verb_prefix_glob_with_matching_eligible_accepted(self, data_dir):
        # aura.remember + aura.recall are eligible; glob aura.re* matches both
        g = save_grant(
            data_dir,
            actor_type="agent",
            actor_id="claude-cli",
            verb_pattern="aura.re*",
            scope="room:abc",
        )
        assert g["verb_pattern"] == "aura.re*"

    def test_glob_matching_no_eligible_verb_rejected(self, data_dir):
        with pytest.raises(ValueError, match="matches no eligible verbs"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="email.send_*",  # no email.send_* in defaults
                scope="room:abc",
            )

    def test_bare_star_rejected(self, data_dir):
        with pytest.raises(ValueError, match="bare"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="*",
                scope="global",
            )

    def test_empty_pattern_rejected(self, data_dir):
        with pytest.raises(ValueError, match="required"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="",
                scope="global",
            )

    def test_cross_app_pattern_rejected(self, data_dir):
        with pytest.raises(ValueError, match="cross-app"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="*.send",
                scope="global",
            )

    def test_pattern_without_app_rejected(self, data_dir):
        with pytest.raises(ValueError, match="must name an app"):
            save_grant(
                data_dir,
                actor_type="agent",
                actor_id="claude-cli",
                verb_pattern="send_email",  # no dot
                scope="global",
            )


# ─── match() — end-to-end ──────────────────────────────────────────


class TestMatchEndToEnd:
    def test_exact_grant_matches_exact_verb(self, data_dir):
        save_grant(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb_pattern="task.add", scope="room:abc",
        )
        g = match(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb="task.add", scope_candidates=["room:abc"],
        )
        assert g is not None
        assert g["verb_pattern"] == "task.add"

    def test_verb_prefix_glob_matches_only_glob_verbs(self, data_dir):
        # aura.re* covers aura.remember + aura.recall (both eligible),
        # but not aura.forget (also eligible — proves the glob's selectivity).
        save_grant(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb_pattern="aura.re*", scope="room:abc",
        )
        scope_cands = ["room:abc"]

        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="aura.remember", scope_candidates=scope_cands) is not None
        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="aura.recall", scope_candidates=scope_cands) is not None
        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="aura.forget", scope_candidates=scope_cands) is None

    def test_match_refuses_non_eligible_verb_even_via_glob(self, data_dir):
        # Sneak case: an eligible verb shares a prefix with a hypothetically
        # non-eligible one. The per-call is_eligible() floor in match()
        # must reject the non-eligible verb regardless of glob coverage.
        save_grant(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb_pattern="task.*", scope="room:abc",
        )
        # task.add is eligible
        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="task.add", scope_candidates=["room:abc"]) is not None
        # task.delete_everything is NOT in eligible_verbs — must not match
        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="task.delete_everything",
                     scope_candidates=["room:abc"]) is None

    def test_scope_must_be_in_candidates(self, data_dir):
        save_grant(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb_pattern="task.add", scope="room:abc",
        )
        assert match(data_dir, actor_type="agent", actor_id="claude-cli",
                     verb="task.add",
                     scope_candidates=["room:xyz"]) is None

    def test_actor_id_empty_in_grant_matches_any_actor_of_type(self, data_dir):
        # Empty actor_id in the saved grant means "anyone of this type" —
        # the "auto-accept everyone in this session" shape from the spec.
        # Constructed by writing the grant directly since save_grant always
        # sets the actor.id.
        grants_file = data_dir / "autopilot" / "grants.json"
        grants_file.parent.mkdir(parents=True, exist_ok=True)
        grants_file.write_text('[{"id":"grant-test","actor":{"type":"cli","id":""},'
                                '"verb_pattern":"task.add","scope":"session:room-x",'
                                '"created_at":"2026-05-24T00:00:00+00:00",'
                                '"expires_at":null,"granted_by":"user","rationale":""}]',
                                encoding="utf-8")
        g = match(data_dir, actor_type="cli", actor_id="claude-cli",
                  verb="task.add", scope_candidates=["session:room-x"])
        assert g is not None
        assert g["id"] == "grant-test"


# ─── revoke ─────────────────────────────────────────────────────────


class TestRevoke:
    def test_revoke_by_id(self, data_dir):
        g = save_grant(
            data_dir, actor_type="agent", actor_id="claude-cli",
            verb_pattern="task.add", scope="room:abc",
        )
        assert revoke_grant(data_dir, g["id"]) is True
        assert revoke_grant(data_dir, g["id"]) is False  # idempotent
        assert load_grants(data_dir) == []

    def test_revoke_scope_removes_all_in_that_scope(self, data_dir):
        save_grant(data_dir, actor_type="agent", actor_id="claude-cli",
                   verb_pattern="task.add", scope="session:room-x")
        save_grant(data_dir, actor_type="agent", actor_id="claude-cli",
                   verb_pattern="capture.add", scope="session:room-x")
        save_grant(data_dir, actor_type="agent", actor_id="claude-cli",
                   verb_pattern="journal.add_entry", scope="room:other")
        removed = revoke_scope(data_dir, "session:room-x")
        assert removed == 2
        remaining = load_grants(data_dir)
        assert len(remaining) == 1
        assert remaining[0]["scope"] == "room:other"


# ─── policy seeding ────────────────────────────────────────────────


class TestPolicy:
    def test_first_read_seeds_defaults(self, data_dir):
        pol = load_policy(data_dir)
        assert set(pol["eligible_verbs"]) == set(DEFAULT_ELIGIBLE_VERBS)

    def test_existing_policy_gets_new_defaults_merged(self, data_dir):
        # Write a stripped-down policy; reload should re-add missing defaults.
        from emptyos.sdk.autopilot import _store_root, _write_json
        _write_json(_store_root(data_dir) / "policy.json",
                    {"eligible_verbs": ["task.add"]})
        pol = load_policy(data_dir)
        assert "task.add" in pol["eligible_verbs"]
        assert "capture.add" in pol["eligible_verbs"]  # merged from defaults
