"""Notifications plugin — the system speaks first.

Default plugin. Writes to vault by default, sends via Telegram when a bot token
and chat id are set — its own [plugins.notifications] settings, else the
telegram plugin's.
Apps call: self.require("notifications").send("message")
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from emptyos.sdk import BasePlugin

try:
    import aiohttp

    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

# A sandbox-pool member, the :9001 dogfood daemon and an external lab host set
# these — the same set external-lab-host's own guard checks. Each inherits the
# user's environment, Telegram credentials included.
_TEST_DAEMON_ENV = ("EOS_SANDBOX_POOL_MEMBER", "EOS_DEMO_INSTANCE", "EOS_LAB_HOST_INSTANCE")


# Matches the telegram plugin's own send timeout: long enough for a slow API
# round trip, short enough that a stall cannot hold the event bus.
TELEGRAM_TIMEOUT_S = 10


def _is_test_daemon() -> bool:
    return any(os.environ.get(name) == "1" for name in _TEST_DAEMON_ENV)


class NotificationsPlugin(BasePlugin):
    name = "notifications"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._telegram_available = False
        self._session = None
        self._token = ""
        self._chat_id = ""

    async def connect(self):
        token = os.environ.get(self.config("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        chat_id = self.config("chat_id", "")
        test_daemon = _is_test_daemon() or bool(getattr(self.kernel.config, "demo_enabled", False))
        if not (token and chat_id) and not test_daemon:
            # Fall back to the telegram plugin's settings, resolved in its own
            # order (config, then env), so one chat is configured once. Without
            # this, a machine that set up only [plugins.telegram] got every
            # gated nudge in the vault inbox and none on the phone (found
            # 2026-09-15). Test daemons skip the fallback, so unless one has its
            # own [plugins.notifications] chat id it never texts the phone.
            tg = self.kernel.config.get("plugins.telegram", {})
            tg = tg if isinstance(tg, dict) else {}
            token = str(tg.get("bot_token") or "") or token
            chat_id = (chat_id or str(tg.get("chat_id") or "")
                       or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self._token, self._chat_id = token, chat_id
        self._telegram_available = bool(token and chat_id and _HAS_AIOHTTP)
        if self._telegram_available:
            self._session = aiohttp.ClientSession()

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def available(self) -> bool:
        return True

    async def send(
        self, message: str, priority: str = "info", source: str = "system",
        telegram: bool = True,
    ):
        """Send a notification. Writes to vault and, when Telegram is configured
        and ``telegram`` is true, to the phone. Pass ``telegram=False`` for
        high-volume operational noise that belongs in the inbox only."""
        emoji = {"info": "🔔", "success": "✅", "warning": "⚠️", "error": "❌"}.get(priority, "🔔")
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        line = f"- **{now}** | {emoji} {message}\n"

        self._append_to_vault(line)

        await self.kernel.events.emit(
            "notification:sent",
            {"message": message, "priority": priority, "source": source},
            source="notifications",
        )

        if telegram and self._telegram_available:
            await self._send_telegram(message, emoji)

    def _append_to_vault(self, line: str):
        """Append notification to vault file (O(1), no read required)."""
        vault_path = self.kernel.config.notes_path
        if not vault_path:
            return
        notif_file = vault_path / "00_Inbox" / "_eos-notifications.md"
        try:
            notif_file.parent.mkdir(parents=True, exist_ok=True)
            if not notif_file.exists():
                notif_file.write_text("# EmptyOS Notifications\n\n", encoding="utf-8")
            with open(notif_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            print(f"[Notifications] Failed to write vault: {e}")

    async def _send_telegram(self, message: str, emoji: str):
        """Send via Telegram using the reused session.

        Bounded by ``TELEGRAM_TIMEOUT_S``: this runs inside event handlers
        (reactor → gate → here), and the event bus awaits handlers one at a
        time, so an unbounded post would stall it. Plain text, no ``parse_mode``: free text with an
        unmatched ``_`` or ``*`` is rejected as Markdown. The gate has already
        reported the nudge delivered by the time this runs, so a failure can
        only be logged."""
        if not self._session:
            return
        try:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            async with self._session.post(
                url,
                json={"chat_id": self._chat_id, "text": f"{emoji} {message}"},
                timeout=aiohttp.ClientTimeout(total=TELEGRAM_TIMEOUT_S),
            ) as resp:
                if resp.status >= 400:
                    print(f"[Notifications] Telegram rejected the message: HTTP {resp.status}")
        except Exception as e:
            print(f"[Notifications] Telegram failed: {e}")
