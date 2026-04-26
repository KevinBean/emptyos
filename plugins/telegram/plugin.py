"""Telegram plugin — push notifications + two-way bot.

Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
Provides: send(text), send_photo(path), get_updates(), set_commands()
"""

from __future__ import annotations

import os

import aiohttp

from emptyos.sdk import BasePlugin


class TelegramPlugin(BasePlugin):
    name = "telegram"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None
        self._token = ""
        self._chat_id = ""

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def connect(self):
        self._token = (
            self.config("bot_token", "")
            or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        )
        self._chat_id = (
            self.config("chat_id", "")
            or os.environ.get("TELEGRAM_CHAT_ID", "")
        )
        self._session = aiohttp.ClientSession()

        if not self._token:
            print("[Telegram] No bot token configured (TELEGRAM_BOT_TOKEN)")
            return

        is_up = await self.available()
        if is_up:
            print(f"[Telegram] Connected — bot ready, chat_id={self._chat_id}")
        else:
            print("[Telegram] Bot token invalid or API unreachable")

    async def disconnect(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def available(self) -> bool:
        if not self._token or not self._session:
            return False
        try:
            async with self._session.get(
                self._api("getMe"),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                return data.get("ok", False)
        except Exception:
            return False

    # --- Send messages ---

    async def send(self, text: str, chat_id: str = "", parse_mode: str = "Markdown") -> dict:
        """Send a text message."""
        cid = chat_id or self._chat_id
        if not cid or not self._token:
            return {"error": "no chat_id or token"}
        async with self._session.post(
            self._api("sendMessage"),
            json={"chat_id": cid, "text": text, "parse_mode": parse_mode},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return await resp.json()

    async def send_photo(self, photo_path: str, caption: str = "", chat_id: str = "") -> dict:
        """Send a photo."""
        cid = chat_id or self._chat_id
        if not cid or not self._token:
            return {"error": "no chat_id or token"}
        data = aiohttp.FormData()
        data.add_field("chat_id", cid)
        data.add_field("photo", open(photo_path, "rb"), filename="photo.jpg")
        if caption:
            data.add_field("caption", caption)
        async with self._session.post(
            self._api("sendPhoto"),
            data=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            return await resp.json()

    # --- Receive messages ---

    async def get_updates(self, offset: int = 0, limit: int = 10) -> list[dict]:
        """Get recent messages sent to the bot."""
        async with self._session.get(
            self._api("getUpdates"),
            params={"offset": offset, "limit": limit, "timeout": 1},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json()
            return data.get("result", [])

    # --- Bot commands ---

    async def set_commands(self, commands: list[dict]) -> bool:
        """Set bot menu commands. Each: {command, description}."""
        async with self._session.post(
            self._api("setMyCommands"),
            json={"commands": commands},
        ) as resp:
            data = await resp.json()
            return data.get("ok", False)

    async def get_bot_info(self) -> dict:
        """Get bot username and info."""
        async with self._session.get(self._api("getMe")) as resp:
            data = await resp.json()
            return data.get("result", {})
