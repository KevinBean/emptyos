"""Shared OAuth failure handling for Google connector plugins.

An expired or revoked refresh token answers `invalid_grant` on every attempt —
retrying cannot fix it, only re-running the auth flow can. That matters because
the health watchdog probes each plugin's `available()` every 60s and, after
three failures, additionally tries disconnect+connect+available. Each of those
reaches a live token refresh, so an unlatched permanent failure turns into
thousands of failing calls a day against Google's token endpoint and the same
line thousands of times in the daemon log (observed 2026-08-16 on the youtube
connector, dead for five weeks).

USE THIS when a plugin loads Google OAuth credentials on a path that something
polls. NOT for one-shot standalone scripts (scripts/*_auth.py, the push
drivers) — they run once and exit, so there is no retry loop to suppress and
the latch would just be ceremony.

Deliberately holds no credentials, no scopes, no file paths and no google-auth
import: those differ per connector and stay in the plugin. This module owns
only the two things both connectors need identically — deciding whether a
failure is permanent, and remembering that it was.
"""

from __future__ import annotations

# Token-endpoint errors that RETRYING CANNOT FIX — the refresh token is revoked,
# expired (a consent screen left in "Testing" expires them after 7 days), or was
# issued to a client that no longer exists. Everything else — a network blip, a
# 5xx at Google — stays retryable.
_PERMANENT_AUTH_MARKERS = ("invalid_grant", "invalid_client", "invalid_scope")


def is_permanent_auth_error(exc: BaseException) -> bool:
    """True when re-running the same credential load cannot succeed.

    A ValueError is permanent BY CONSTRUCTION for these connectors: the token
    JSON is malformed, its stored scope is wider than the connector pins, or the
    profile name is illegal. Matched by type rather than by message so that
    rewording any of those errors cannot silently stop them latching.
    """
    if isinstance(exc, ValueError):
        return True
    return any(marker in str(exc) for marker in _PERMANENT_AUTH_MARKERS)


class PermanentAuthLatch:
    """Remembers that a key's credentials are unrecoverable, until they change.

    Keyed on the token file's mtime rather than a timer or a retry count: the
    only event that can fix a permanent auth failure is the token file being
    rewritten, which is exactly what re-running the auth flow does. So the latch
    releases on precisely the right signal and needs no daemon restart, no TTL to
    tune, and no way to get stuck.

    `key` is whatever namespaces one credential set within a connector — the
    profile name for multi-profile connectors, "" for single-token ones.

    A plugin instance owns one of these. Do NOT reset it in `connect()`: health's
    recovery path calls disconnect+connect on every failing cycle, so clearing it
    there hands the retry storm straight back.
    """

    def __init__(self) -> None:
        self._dead: dict[str, float] = {}

    def suppressed(self, key: str, mtime: float) -> bool:
        """True when `key` latched and its token file is still the dead one.

        Releases the latch as a side effect when the mtime has moved, so the
        caller's next load attempt goes through.
        """
        dead_at = self._dead.get(key)
        if dead_at is None:
            return False
        if dead_at == mtime:
            return True
        del self._dead[key]      # token was replaced — let the caller retry
        return False

    def mark(self, key: str, mtime: float) -> None:
        """Latch `key` at the token file's current mtime."""
        self._dead[key] = mtime

    def latched(self, key: str) -> bool:
        """True when `key` is currently latched — for phrasing status messages.

        Does not consider mtime; use `suppressed` to decide whether to skip work.
        """
        return key in self._dead
