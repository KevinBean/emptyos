"""Permanent-auth-failure latch on the Gmail connector.

Gmail's token is healthy today; this pins the behaviour so it stays that way
when the token eventually dies the same way youtube's did (both connectors sit
behind the same 60s health probe + 3-failure reconnect, so an unlatched
permanent failure becomes thousands of failing token-endpoint calls a day).

The classifier and the latch state machine are covered in
tests/test_sdk_google_auth.py — this file pins only the plugin's wiring of them.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import pytest

from emptyos.sdk import PermanentAuthLatch

ROOT = Path(__file__).resolve().parent.parent
INVALID_GRANT = "('invalid_grant: Bad Request', {'error': 'invalid_grant'})"


def _plugin_module():
    for name, path in (("plugins", ROOT / "plugins"),
                       ("plugins.gmail", ROOT / "plugins" / "gmail")):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(path)]
            sys.modules[name] = pkg
    spec = importlib.util.spec_from_file_location(
        "plugins.gmail.plugin", ROOT / "plugins" / "gmail" / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Stand-in for plugins.gmail.client that counts refresh attempts."""

    def __init__(self, token: Path, message: str):
        self.token = token
        self.message = message
        self.calls = 0

    def token_path(self, secrets_dir):
        return self.token

    def load_credentials(self, secrets_dir):
        self.calls += 1
        raise Exception(self.message)


@pytest.fixture()
def plugin(tmp_path):
    module = _plugin_module()
    token = tmp_path / "gmail-token.json"
    token.write_text("{}", encoding="utf-8")

    p = module.GmailPlugin.__new__(module.GmailPlugin)
    p._secrets_dir = tmp_path
    p._client = _Recorder(token, INVALID_GRANT)
    p._auth_latch = PermanentAuthLatch()
    return p


def test_invalid_grant_hits_the_network_once(plugin):
    async def probe():
        for _ in range(10):
            await plugin._creds()

    asyncio.run(probe())
    assert plugin._client.calls == 1, "permanent auth failure must latch after one attempt"


def test_latch_clears_when_the_token_is_re_authed(plugin):
    asyncio.run(plugin._creds())
    assert plugin._client.calls == 1

    time.sleep(0.01)
    os.utime(plugin._client.token, None)  # scripts/gmail_auth.py rewrote it

    asyncio.run(plugin._creds())
    assert plugin._client.calls == 2, "re-auth must clear the latch without a daemon restart"


def test_transient_failure_never_latches(plugin):
    plugin._client.message = "Connection reset by peer"

    async def probe():
        for _ in range(5):
            await plugin._creds()

    asyncio.run(probe())
    assert plugin._client.calls == 5, "a network blip must stay retryable"


def test_healthy_credentials_are_returned_untouched(plugin):
    """The latch must be invisible on the happy path — no suppression, no
    swallowing of a perfectly good Credentials object."""
    sentinel = object()
    plugin._client.load_credentials = lambda secrets_dir: sentinel

    assert asyncio.run(plugin._creds()) is sentinel
    assert not plugin._auth_latch.latched(plugin._LATCH_KEY)
