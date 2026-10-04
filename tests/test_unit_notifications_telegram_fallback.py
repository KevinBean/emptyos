"""Notifications plugin — reaches Telegram with the telegram plugin's settings.

Found 2026-09-15: a machine that configured only ``[plugins.telegram]`` (bot
token + chat id) had no ``[plugins.notifications]`` section, so the
notifications service never sent to Telegram. Every nudge the proactive gate
delivered through its ``notify`` channel — reminders included — landed in the
vault inbox and never reached the phone.

Also pinned: the fallback follows the telegram plugin's own resolution order,
test daemons (sandbox-pool members, the :9001 dogfood daemon) never use it,
``telegram=False`` keeps a message in the inbox, and the send is bounded and
plain text.

Kernel-free: the plugin module is loaded from its file, config is stubbed, and
``aiohttp`` is replaced so no session or network call is ever made.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "notifications_plugin_under_test", REPO / "plugins" / "notifications" / "plugin.py")
notif_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notif_mod)


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    status = 200

    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        return _FakeResponse(self.status)

    async def close(self):
        pass


@pytest.fixture
def make_plugin(monkeypatch):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EOS_SANDBOX_POOL_MEMBER",
                 "EOS_DEMO_INSTANCE", "EOS_LAB_HOST_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(notif_mod, "_HAS_AIOHTTP", True)
    monkeypatch.setattr(notif_mod, "aiohttp", SimpleNamespace(
        ClientSession=_FakeSession, ClientTimeout=lambda total: ("timeout", total)),
        raising=False)

    def _make(*, own=None, telegram=None, demo=False):
        data = {"plugins.telegram": telegram} if telegram is not None else {}

        async def _emit(*args, **kwargs):
            pass

        kernel = SimpleNamespace(
            config=SimpleNamespace(get=lambda key, default=None: data.get(key, default),
                                   notes_path=None, demo_enabled=demo),
            events=SimpleNamespace(emit=_emit),
        )
        plugin = notif_mod.NotificationsPlugin(kernel, {})
        own = own or {}
        plugin.config = lambda key, default=None: own.get(key, default)
        return plugin

    return _make


def _connect(plugin):
    asyncio.run(plugin.connect())
    return plugin


# ── where the token and chat come from ───────────────────────────────


def test_falls_back_to_the_telegram_plugin_settings(make_plugin):
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert plugin._telegram_available is True
    assert (plugin._token, plugin._chat_id) == ("tg-token", "42")


def test_env_token_combines_with_the_telegram_chat_id(make_plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    plugin = _connect(make_plugin(telegram={"chat_id": "42"}))
    assert (plugin._token, plugin._chat_id) == ("env-token", "42")


def test_falling_back_prefers_the_telegram_plugin_token_over_env(make_plugin, monkeypatch):
    # The telegram plugin reads its config token before the environment; the
    # fallback must pick the same bot, or it sends from one the chat never started.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert plugin._token == "tg-token"


def test_the_telegram_plugin_chat_beats_the_env_chat(make_plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert plugin._chat_id == "42"


def test_env_chat_is_the_last_resort(make_plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    plugin = _connect(make_plugin(telegram={}))
    assert plugin._telegram_available is True and plugin._chat_id == "99"


def test_its_own_settings_win_over_the_telegram_plugin(make_plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    plugin = _connect(make_plugin(own={"chat_id": "7"},
                                  telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert (plugin._token, plugin._chat_id) == ("env-token", "7")


def test_its_own_chat_id_survives_a_token_fallback(make_plugin):
    # Own chat id, no token of its own: the token comes from the telegram
    # plugin, but the chat must stay the one this plugin was configured with.
    plugin = _connect(make_plugin(own={"chat_id": "7"},
                                  telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert (plugin._token, plugin._chat_id) == ("tg-token", "7")


def test_bot_token_env_names_the_variable_to_read(make_plugin, monkeypatch):
    monkeypatch.setenv("MY_BOT_TOKEN", "mine")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "other")
    plugin = _connect(make_plugin(own={"bot_token_env": "MY_BOT_TOKEN", "chat_id": "7"}))
    assert (plugin._token, plugin._chat_id) == ("mine", "7")


@pytest.mark.parametrize("marker", [
    "EOS_SANDBOX_POOL_MEMBER", "EOS_DEMO_INSTANCE", "EOS_LAB_HOST_INSTANCE",
])
def test_test_daemons_never_fall_back_to_the_real_phone(make_plugin, monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    assert plugin._telegram_available is False


def test_a_demo_daemon_never_falls_back_to_the_real_phone(make_plugin, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}, demo=True))
    assert plugin._telegram_available is False


def test_the_send_timeout_matches_the_telegram_plugin_send():
    text = (REPO / "plugins" / "telegram" / "plugin.py").read_text(encoding="utf-8")
    body = text[text.index("    async def send("):]
    body = body[: body.index("\n    async def ", 1)]
    total = int(re.search(r"ClientTimeout\(total=(\d+)\)", body).group(1))
    assert notif_mod.TELEGRAM_TIMEOUT_S == total


def test_nothing_configured_means_no_telegram(make_plugin):
    plugin = _connect(make_plugin())
    assert plugin._telegram_available is False
    assert plugin._session is None


# ── the send ─────────────────────────────────────────────────────────


def test_send_is_bounded_plain_text_to_the_resolved_chat(make_plugin):
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    asyncio.run(plugin._send_telegram("under_score *star*", "🔔"))
    (url, body, timeout), = plugin._session.posts
    assert url == "https://api.telegram.org/bottg-token/sendMessage"
    assert body == {"chat_id": "42", "text": "🔔 under_score *star*"}  # no parse_mode
    assert timeout == ("timeout", notif_mod.TELEGRAM_TIMEOUT_S)


def test_telegram_false_keeps_a_message_in_the_inbox(make_plugin):
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    asyncio.run(plugin.send("connector_down: blender", priority="warning", telegram=False))
    assert plugin._session.posts == []
    asyncio.run(plugin.send("Reminder: call mom"))
    assert len(plugin._session.posts) == 1


def test_a_rejected_message_is_logged(make_plugin, capsys, monkeypatch):
    plugin = _connect(make_plugin(telegram={"bot_token": "tg-token", "chat_id": "42"}))
    monkeypatch.setattr(plugin._session, "status", 400)
    asyncio.run(plugin._send_telegram("hello", "🔔"))
    assert "HTTP 400" in capsys.readouterr().out
