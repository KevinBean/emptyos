"""Unit tests for the public-app auth-presence classifier (pure, no daemon).

Covers emptyos.sdk.base_app._request_is_public — the reusable core of the
"one app, two faces" pattern (.claude/rules/public-app-pattern.md).
"""

from __future__ import annotations

from types import SimpleNamespace

from emptyos.sdk.base_app import _request_is_public


class _Headers(dict):
    """Case-insensitive .get, like a real request header map."""

    def get(self, key, default=""):
        return super().get(key.lower(), default)


def _req(headers=None, cookies=None, public_face=None):
    """A request. `public_face` mirrors the flag the auth middleware sets on
    `request.state` after it VALIDATES credentials on a public route
    (None = middleware never classified this request)."""
    h = _Headers({k.lower(): v for k, v in (headers or {}).items()})
    state = SimpleNamespace()
    if public_face is not None:
        state.public_face = public_face
    return SimpleNamespace(headers=h, cookies=(cookies or {}), state=state)


def test_x_eos_public_header_forces_public_face():
    # The trusted front proxy (control plane) marks anonymous public traffic.
    assert _request_is_public(_req(headers={"X-EOS-Public": "1"}), auth_configured=True)
    # Works even with auth off — the header is the explicit signal either way.
    assert _request_is_public(_req(headers={"X-EOS-Public": "1"}), auth_configured=False)


def test_cloud_authed_request_gets_full_face():
    # Behind the proxy, an owner's request carries the inner-token bearer.
    assert not _request_is_public(
        _req(headers={"Authorization": "Bearer inner"}), auth_configured=True
    )


def test_direct_daemon_anonymous_is_public():
    # Auth configured, but no credentials -> came via a public_routes bypass.
    assert _request_is_public(_req(), auth_configured=True)


def test_direct_daemon_cookie_is_full_face():
    assert not _request_is_public(_req(cookies={"eos_session": "abc"}), auth_configured=True)


def test_local_mode_is_never_public():
    # No auth gate -> everything already open -> serve the full face.
    assert not _request_is_public(_req(), auth_configured=False)


def test_header_can_only_restrict_never_escalate():
    # A browser-supplied X-EOS-Public only ever drops the caller TO the public
    # (lesser) face — it cannot grant access. Confirm it returns public (True),
    # which is the restrict direction, not full-face access.
    assert _request_is_public(_req(headers={"X-EOS-Public": "1"}), auth_configured=True) is True


# --- Forged-bearer escalation (regression) ---------------------------------
# On a directly-exposed daemon, an anonymous caller can put ANY string in an
# Authorization header. Presence detection alone reads that as "authenticated"
# and hands back the full face. The auth middleware already validated the
# credential and recorded the verdict on request.state.public_face — honour it.


def test_forged_bearer_on_public_route_does_not_escalate():
    # Middleware validated the bogus bearer, found it invalid -> public_face.
    assert _request_is_public(
        _req(headers={"Authorization": "Bearer totally-bogus"}, public_face=True),
        auth_configured=True,
    )


def test_forged_cookie_on_public_route_does_not_escalate():
    assert _request_is_public(
        _req(cookies={"eos_session": "bogus"}, public_face=True), auth_configured=True
    )


def test_validated_owner_on_public_route_keeps_full_face():
    # Middleware validated a real credential -> leaves public_face unset ->
    # presence detection correctly yields the full face.
    assert not _request_is_public(
        _req(headers={"Authorization": "Bearer real-token"}), auth_configured=True
    )
    # And an explicit False flag is honoured as full-face too.
    assert not _request_is_public(
        _req(headers={"Authorization": "Bearer real-token"}, public_face=False),
        auth_configured=True,
    )


def test_validated_flag_never_overrides_the_proxy_header():
    # Header still wins: the trusted proxy's restrict-only marker.
    assert _request_is_public(
        _req(headers={"X-EOS-Public": "1"}, public_face=False), auth_configured=True
    )
