"""Permanent-auth-failure classification + latch (emptyos/sdk/google_auth.py).

Both directions are pinned, per .claude/rules/audits.md — a latch that never
releases is as wrong as one that never engages, and a classifier that calls
everything permanent would mute real transient outages.
"""

from __future__ import annotations

from emptyos.sdk import PermanentAuthLatch, is_permanent_auth_error

INVALID_GRANT = "('invalid_grant: Bad Request', {'error': 'invalid_grant'})"


# ── classification ───────────────────────────────────────────────────────────


def test_token_endpoint_errors_are_permanent():
    assert is_permanent_auth_error(Exception(INVALID_GRANT))
    assert is_permanent_auth_error(Exception("invalid_client: client was not found"))
    assert is_permanent_auth_error(Exception("invalid_scope"))


def test_value_error_is_permanent_by_type_not_by_wording():
    """load_credentials raises ValueError for the scope guard, a malformed token
    file, and an illegal profile name — all permanent. Classifying by type means
    rewording any of those messages cannot silently un-latch them."""
    assert is_permanent_auth_error(ValueError("scope beyond what we pin — "
                                              "refusing to use it"))
    assert is_permanent_auth_error(ValueError("Expecting value: line 1 column 1"))
    assert is_permanent_auth_error(ValueError("wording nobody has written yet"))
    assert is_permanent_auth_error(ValueError(""))


def test_transient_failures_stay_retryable():
    """The whole point of classifying: a network blip must NOT latch, or one bad
    minute would mute the connector until its token file happens to change."""
    assert not is_permanent_auth_error(Exception("Connection reset by peer"))
    assert not is_permanent_auth_error(Exception("503 Service Unavailable"))
    assert not is_permanent_auth_error(TimeoutError("timed out"))
    assert not is_permanent_auth_error(OSError("[WinError 10061] refused"))
    assert not is_permanent_auth_error(Exception(""))


def test_marker_match_is_case_sensitive_and_substring():
    """Documents the matching contract rather than asserting it is ideal:
    Google emits these tokens lowercase, so an uppercase variant does not match."""
    assert not is_permanent_auth_error(Exception("INVALID_GRANT"))
    assert is_permanent_auth_error(Exception("prefix invalid_grant suffix"))


# ── latch ────────────────────────────────────────────────────────────────────


def test_unmarked_key_is_never_suppressed():
    latch = PermanentAuthLatch()
    assert not latch.suppressed("", 123.0)
    assert not latch.latched("")


def test_marked_key_is_suppressed_at_the_same_mtime():
    latch = PermanentAuthLatch()
    latch.mark("", 123.0)
    assert latch.suppressed("", 123.0)
    assert latch.suppressed("", 123.0)   # idempotent, stays suppressed
    assert latch.latched("")


def test_changed_mtime_releases_the_latch():
    """The release signal is the token file being rewritten — i.e. a re-auth."""
    latch = PermanentAuthLatch()
    latch.mark("", 123.0)
    assert not latch.suppressed("", 124.0)
    assert not latch.latched(""), "release must clear state, not just return False"
    assert not latch.suppressed("", 123.0), "the old mtime must not re-suppress"


def test_keys_are_independent():
    latch = PermanentAuthLatch()
    latch.mark("music", 1.0)
    assert latch.suppressed("music", 1.0)
    assert not latch.suppressed("", 1.0)
    assert not latch.latched("")


def test_zero_mtime_is_a_real_value_not_absent():
    """A missing/unreadable token file reports mtime 0.0; that must still latch,
    or an unreadable token would retry against the network forever."""
    latch = PermanentAuthLatch()
    latch.mark("", 0.0)
    assert latch.suppressed("", 0.0)
    assert not latch.suppressed("", 1.0)


def test_latched_ignores_mtime():
    """latched() answers 'is this key known-dead' for phrasing a status message;
    suppressed() is the one that decides whether to skip work."""
    latch = PermanentAuthLatch()
    latch.mark("", 5.0)
    assert latch.latched("")   # true even though the file may have moved on
