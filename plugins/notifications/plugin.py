"""Notifications plugin — the system speaks first.

Default plugin. Writes to vault by default, sends via Telegram when configured.
Apps call: self.require("notifications").send("message")
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from emptyos.sdk import BasePlugin

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False


class NotificationsPlugin(BasePlugin):
    name = "notifications"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._telegram_available = False
        self._session = None

    async def connect(self):
        token = os.environ.get(self.config("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        chat_id = self.config("chat_id", "")
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

    async def send(self, message: str, priority: str = "info", source: str = "system"):
        """Send a notification. Writes to vault and optionally Telegram."""
        emoji = {"info": "🔔", "success": "✅", "warning": "⚠️", "error": "❌"}.get(priority, "🔔")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        line = f"- **{now}** | {emoji} {message}\n"

        self._append_to_vault(line)

        await self.kernel.events.emit(
            "notification:sent",
            {"message": message, "priority": priority, "source": source},
            source="notifications",
        )

        if self._telegram_available:
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
        """Send via Telegram using reused session."""
        if not self._session:
            return
        token = os.environ.get(self.config("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        chat_id = self.config("chat_id", "")
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            await self._session.post(url, json={
                "chat_id": chat_id,
                "text": f"{emoji} {message}",
                "parse_mode": "Markdown",
            })
        except Exception as e:
            print(f"[Notifications] Telegram failed: {e}")
