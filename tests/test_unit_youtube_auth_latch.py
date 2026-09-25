"""Permanent-auth-failure latch on the YouTube connector.

An expired/revoked refresh token answers `invalid_grant` forever — retrying
cannot fix it. The health watchdog probes every plugin's `available()` on a 60s
loop and, after three failures, additionally tries disconnect+connect+available,
so an unlatched permanent failure becomes thousands of failing calls a day to
Google's token endpoint (observed 2026-08-16: both youtube profiles dead since
mid-July, spamming the daemon log ever since).

Both directions are pinned, per .claude/rules/audits.md — a latch that never
releases is as wrong as one that never engages:
  - permanent  -> one network call, then silence
  - re-authed  -> latch clears with no daemon restart (token mtime changed)
  - transient  -> never latches (a network blip must stay retryable)
  - profiles   -> latched independently ('music' dying can't mute the default)
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
    """Load plugins/youtube/plugin.py standalone (parent packages registered so
    its `from . import client` relative import can resolve)."""
    for name, path in (("plugins", ROOT / "plugins"),
                       ("plugins.youtube", ROOT / "plugins" / "youtube")):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(path)]
            sys.modules[name] = pkg
    spec = importlib.util.spec_from_file_location(
        "plugins.youtube.plugin", ROOT / "plugins" / "youtube" / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Stand-in for plugins.youtube.client that counts refresh attempts."""

    def __init__(self, token: Path, message: str):
        self.token = token
        self.message = message
        self.calls = 0

    def token_path(self, secrets_dir, profile: str = ""):
        return self.token if not profile else self.token.with_name(
            f"youtube-{profile}-token.json")

    def load_credentials(self, secrets_dir, profile: str = ""):
        self.calls += 1
        raise Exception(self.message)


@pytest.fixture()
def plugin(tmp_path):
    """A YouTubePlugin wired to a recorder whose refresh always fails."""
    module = _plugin_module()
    token = tmp_path / "youtube-token.json"
    token.write_text("{}", encoding="utf-8")
    (tmp_path / "youtube-music-token.json").write_text("{}", encoding="utf-8")

    p = module.YouTubePlugin.__new__(module.YouTubePlugin)
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
    os.utime(plugin._client.token, None)  # scripts/youtube_auth.py rewrote it

    asyncio.run(plugin._creds())
    assert plugin._client.calls == 2, "re-auth must clear the latch without a daemon restart"


def test_transient_failure_never_latches(plugin):
    plugin._client.message = "Connection reset by peer"

    async def probe():
        for _ in range(5):
            await plugin._creds()

    asyncio.run(probe())
    assert plugin._client.calls == 5, "a network blip must stay retryable"


def test_profiles_latch_independently(plugin):
    async def probe():
        for _ in range(4):
            await plugin._creds("")
            await plugin._creds("music")

    asyncio.run(probe())
    assert plugin._client.calls == 2, "each profile owns its own latch"
    assert plugin._auth_latch.latched("") and plugin._auth_latch.latched("music")
