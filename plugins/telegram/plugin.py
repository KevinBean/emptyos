"""Telegram plugin — push notifications + (dark-flagged) two-way rooms bridge.

Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment or
[plugins.telegram] config. Provides: send(text), send_photo(path),
get_updates(), set_commands().

Two-way bridge (feature.telegram-two-way.enabled, default OFF): a long-poll
loop feeds messages from the ONE configured chat_id into the `telegram-bridge`
rooms room (gate_mode="gate" agent), sends the reply back, and renders any
gated [DO:] proposals as Apply/Reject inline-button cards. Pure helpers live
in bridge.py; the security model is a hard chat_id allowlist + the rooms
review gate (see .claude/rules/room-review-gate.md) + a periodic session
password challenge (auth_ttl_hours, default 12h; secret = bridge_password
or the daemon's [network] password) covering a stolen phone / compromised
Telegram account.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from emptyos.sdk import BasePlugin

from . import bridge


class TelegramPlugin(BasePlugin):
    name = "telegram"
    CLEAR_COMMAND = {"command": "clear", "description": "Start a fresh session"}

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session = None
        self._token = ""
        self._chat_id = ""
        self._poll_task: asyncio.Task | None = None
        self._bridge_ready = False
        self._state: dict = {}

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _two_way_enabled(self) -> bool:
        """Read [plugins.telegram] feature.telegram-two-way.enabled.

        BasePlugin.config() is a flat dict get, but TOML dotted keys nest —
        walk feature → telegram-two-way → enabled.
        """
        node = self.config("feature")
        for part in ("telegram-two-way", "enabled"):
            node = node.get(part) if isinstance(node, dict) else None
        return bool(node)

    def _bot_commands(self) -> list[dict]:
        """Telegram's setMyCommands replaces the whole menu, so /clear must be
        registered in the same payload as /dump and /chat."""
        commands = list(bridge.BOT_COMMANDS)
        if not any(c.get("command") == self.CLEAR_COMMAND["command"] for c in commands):
            commands.append(dict(self.CLEAR_COMMAND))
        return commands

    async def connect(self):
        self._token = self.config("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = self.config("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._session = aiohttp.ClientSession()

        if not self._token:
            print("[Telegram] No bot token configured (TELEGRAM_BOT_TOKEN)")
            return

        is_up = await self.available()
        if is_up:
            print(f"[Telegram] Connected — bot ready, chat_id={self._chat_id}")
        else:
            print("[Telegram] Bot token invalid or API unreachable")

        if self._two_way_enabled() and self._chat_id:
            self._poll_task = asyncio.create_task(self._poll_loop())
            # setMyCommands replaces the whole menu, so register all bridge
            # commands in one payload.
            print("[Telegram] two-way bridge polling (room: telegram-bridge)")
            try:
                await self.set_commands(self._bot_commands())
            except Exception:
                pass
        else:
            print("[Telegram] two-way bridge dark (feature.telegram-two-way.enabled=false)")

    async def disconnect(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None
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

    async def send(
        self,
        text: str,
        chat_id: str = "",
        parse_mode: str = "Markdown",
        reply_markup: dict | None = None,
    ) -> dict:
        """Send a text message. parse_mode="" sends plain text."""
        cid = chat_id or self._chat_id
        if not cid or not self._token:
            return {"error": "no chat_id or token"}
        payload: dict = {"chat_id": cid, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with self._session.post(
            self._api("sendMessage"),
            json=payload,
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
        # Brief blocking open — aiohttp streams the file from here.
        data.add_field("photo", open(photo_path, "rb"), filename="photo.jpg")  # noqa: ASYNC230
        if caption:
            data.add_field("caption", caption)
        async with self._session.post(
            self._api("sendPhoto"),
            data=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            return await resp.json()

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "",
        reply_markup: dict | None = None,
    ) -> dict:
        """Edit a previously sent message (used to resolve card messages)."""
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        async with self._session.post(
            self._api("editMessageText"),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return await resp.json()

    async def delete_message(self, chat_id: str, message_id: int) -> dict:
        """Delete a message (used to scrub the password from chat history)."""
        async with self._session.post(
            self._api("deleteMessage"),
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return await resp.json()

    async def answer_callback_query(self, cq_id: str, text: str = "") -> dict:
        """Ack an inline-button tap so the client stops its spinner."""
        payload: dict = {"callback_query_id": cq_id}
        if text:
            payload["text"] = text
        async with self._session.post(
            self._api("answerCallbackQuery"),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return await resp.json()

    # --- Receive messages ---

    async def get_updates(
        self,
        offset: int = 0,
        limit: int = 10,
        timeout: int = 1,
        allowed_updates: list[str] | None = None,
    ) -> list[dict]:
        """Get recent messages sent to the bot. timeout>0 = long poll."""
        params: dict = {"offset": offset, "limit": limit, "timeout": timeout}
        if allowed_updates:
            params["allowed_updates"] = json.dumps(allowed_updates)
        async with self._session.get(
            self._api("getUpdates"),
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout + 10),
        ) as resp:
            data = await resp.json()
            if not data.get("ok", False):
                # Surface 409 (another getUpdates consumer) etc. to the loop.
                raise RuntimeError(
                    f"getUpdates not ok: {data.get('error_code')} {data.get('description')}"
                )
            return data.get("result", [])

    async def get_file(self, file_id: str) -> dict:
        """Resolve a file_id to a downloadable file_path (Bot API getFile)."""
        async with self._session.get(
            self._api("getFile"),
            params={"file_id": file_id},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            return data.get("result") or {}

    async def download_file(self, file_path: str) -> bytes:
        """Download a Bot API file (voice notes are OGG/Opus, ≤20MB)."""
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        async with self._session.get(
            url, timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            return await resp.read()

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

    # --- Two-way bridge (dark: feature.telegram-two-way.enabled) ---

    # Fallback floor only — the real allowlist is derived from the verb
    # registry at boot (_build_bridge_actions); this applies iff the registry
    # read fails entirely. Method names, never registry verb names.
    BRIDGE_SERVER_ACTIONS: dict = {
        "task": ["add"],
        "journal": ["voice_add_entry"],
    }

    def _build_bridge_actions(self) -> dict:
        """Derive the bridge agent's server_actions from the verb registry.

        The bridge is a text-phone surface, so it consumes the same merged
        voice-verb view the voice assistant builds its intents from
        (``merged_voice_entries``: registry voice surface + legacy intents,
        dispatched via the phone-shaped ``voice_*`` wrappers that return
        {say, link}). Refreshed on every poll-loop start, so new apps'
        verbs appear on the phone without touching this plugin.
        voice-assistant's own aura.* verbs are excluded — companion
        mechanics, not phone verbs. gate_mode="gate" keeps every [DO:]
        behind the review gate except registry-stable VERB names the
        autopilot floor recognises.
        """
        actions: dict[str, list[str]] = {}
        try:
            from emptyos.sdk.verb_registry import merged_voice_entries

            for rec in merged_voice_entries(self.kernel.apps):
                app_id = rec.get("_app_id") or ""
                method = rec.get("method") or ""
                if not app_id or not method or app_id == "voice-assistant":
                    continue
                methods = actions.setdefault(app_id, [])
                if method not in methods:
                    methods.append(method)
        except Exception as e:
            print(f"[Telegram] verb-registry read failed ({e}); using fallback allowlist")
        return actions or dict(self.BRIDGE_SERVER_ACTIONS)

    def _redact(self, text: str) -> str:
        """Strip the bot token from text destined for the chat — aiohttp
        errors can embed token-bearing URLs, and chat history persists on
        Telegram's servers."""
        return text.replace(self._token, "[redacted]") if self._token else text

    def _state_file(self):
        return self.kernel.config.data_dir / "telegram" / "state.json"

    def _save_state(self):
        try:
            bridge.save_state(self._state_file(), self._state)
        except Exception:
            pass

    def _bridge_secret(self) -> str:
        """The periodic-auth secret: [plugins.telegram] bridge_password, else
        the daemon's human credential [network] password (AUTH.md) — never
        the machine auth_token. Empty = periodic auth disabled."""
        pw = str(self.config("bridge_password", "") or "")
        if pw:
            return pw
        try:
            return str(self.kernel.config.get("network.password", "") or "")
        except Exception:
            return ""

    def _auth_ttl_s(self) -> float:
        """[plugins.telegram] auth_ttl_hours (default 12). <= 0 disables."""
        try:
            hours = float(self.config("auth_ttl_hours", 12) or 0)
        except (TypeError, ValueError):
            hours = 12.0
        return max(hours, 0.0) * 3600.0

    def _auth_enforced(self) -> bool:
        return bool(self._bridge_secret()) and self._auth_ttl_s() > 0

    async def _auth_gate(self, payload: dict) -> bool:
        """Periodic session auth. True = proceed with the message; False =
        this turn was consumed by the auth flow (prompt / attempt / lockout).

        An expired session's FIRST message is never treated as an attempt —
        it gets the prompt (so a normal request can't burn a failure); only
        the message after the prompt is checked as a password.
        """
        if not self._auth_enforced():
            return True
        now = time.time()
        st = self._state
        if bridge.is_authed(st, now):
            return True
        chat_id = payload.get("chat_id", "")
        if bridge.is_locked_out(st, now):
            mins = int((float(st.get("lock_until", 0)) - now) / 60) + 1
            await self.send(
                f"\U0001F512 Too many wrong attempts — locked for ~{mins} min.",
                chat_id=chat_id, parse_mode="",
            )
            return False
        if st.get("awaiting_password"):
            if bridge.verify_password(payload.get("text", ""), self._bridge_secret()):
                ttl = self._auth_ttl_s()
                bridge.note_auth_success(st, now, ttl)
                self._save_state()
                if payload.get("message_id"):
                    # Scrub the secret from the Telegram chat history.
                    await self.delete_message(chat_id, payload["message_id"])
                await self.send(
                    f"✅ Unlocked for {ttl / 3600:g}h.", chat_id=chat_id, parse_mode="",
                )
            else:
                locked = bridge.note_auth_failure(st, now)
                self._save_state()
                msg = (
                    "\U0001F512 Too many wrong attempts — locked for ~15 min."
                    if locked else "❌ Wrong password. " + bridge.AUTH_PROMPT
                )
                await self.send(msg, chat_id=chat_id, parse_mode="")
            return False
        st["awaiting_password"] = True
        self._save_state()
        await self.send(bridge.AUTH_PROMPT, chat_id=chat_id, parse_mode="")
        return False

    async def _rooms(self):
        return self.kernel.apps.instances.get("rooms") or await self.kernel.apps.load("rooms")

    async def _braindump(self):
        return (self.kernel.apps.instances.get("braindump")
                or await self.kernel.apps.load("braindump"))

    async def _ensure_bridge_agent(self) -> bool:
        """Provision/refresh the telegram-bridge room agent (idempotent)."""
        try:
            rooms = await self._rooms()
            res = await rooms.register_persona(
                id="telegram-bridge",
                name="Telegram Bridge",
                # Seed only — after first create the persona is user-owned:
                # edits made in /rooms/ survive restarts (keep_existing_prompt).
                system_prompt=bridge.TELEGRAM_BRIDGE_PERSONA,
                source="plugin:telegram",
                emoji="\U0001F4F1",
                gate_mode="gate",
                server_actions=self._build_bridge_actions(),
                keep_existing_prompt=True,
            )
            if isinstance(res, dict) and res.get("error"):
                print(f"[Telegram] bridge agent registration failed: {res['error']}")
                return False
            self._bridge_ready = True
            return True
        except Exception as e:
            print(f"[Telegram] bridge agent registration error: {e}")
            return False

    async def _poll_loop(self):
        self._state = bridge.load_state(self._state_file())
        offset = int(self._state.get("offset", 0) or 0)
        backoff = 1
        while True:
            try:
                if not self._bridge_ready:
                    await self._ensure_bridge_agent()
                updates = await self.get_updates(
                    offset=offset,
                    limit=10,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
                backoff = 1
                for update in updates:
                    offset = bridge.next_offset([update], offset)
                    kind, payload = bridge.parse_update(update, self._chat_id)
                    if kind == "message":
                        # Serial by design — prevents rooms-history races.
                        await self._handle_message(payload)
                    elif kind == "callback":
                        # Fast lane: an Apply/Reject tap must not queue behind
                        # a long chat turn. apply/reject are independent of the
                        # chat pipeline, and a double-tap self-heals ("already
                        # applied" edits the card).
                        self.spawn_background(self._handle_callback(payload))
                    elif kind == "voice":
                        # Serial like messages — the transcript becomes a turn.
                        await self._handle_voice(payload)
                    elif kind == "unsupported":
                        await self.send(
                            "Text or voice notes only — I can't read that yet.",
                            chat_id=payload.get("chat_id", ""),
                            parse_mode="",
                        )
                    # kind None = dropped (wrong chat / unknown shape)
                if updates:
                    self._state["offset"] = offset
                    self._save_state()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = str(e)
                if "409" in msg:
                    print(f"[Telegram] getUpdates conflict (another poller on this token?): {msg}")
                    backoff = 60
                else:
                    print(f"[Telegram] poll error: {msg}; retry in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _handle_message(self, payload: dict):
        """One inbound text → one rooms chat turn → reply + pending cards.

        In Brain Dump mode (/dump), the turn is routed to the braindump
        pipeline instead of the chat agent. Command handling sits after the
        auth gate so /dump respects the session lock like any message, and
        (via _handle_voice → _handle_message) a spoken ramble hits it too.
        """
        chat_id = payload.get("chat_id", "")
        if not await self._auth_gate(payload):
            return
        text = payload.get("text", "")
        if bridge.is_clear_command(text):
            try:
                rooms = await self._rooms()
                rooms.clear_room_history("telegram-bridge")
                await self.send(
                    "\U0001F195 Started a fresh session — earlier messages are cleared.",
                    chat_id=chat_id, parse_mode="",
                )
            except Exception as e:
                await self.send(
                    f"Couldn't clear the session: {self._redact(str(e))[:150]}",
                    chat_id=chat_id, parse_mode="",
                )
            return
        cmd = bridge.parse_command(text)
        if cmd == "dump":
            bd = await self._braindump()
            if not (bd and bd._enabled()):
                await self.send(bridge.BRAINDUMP_DISABLED_MSG, chat_id=chat_id, parse_mode="")
                return
            self._state["braindump_mode"] = True
            self._save_state()
            await self.send(bridge.BRAINDUMP_ON_MSG, chat_id=chat_id, parse_mode="")
            return
        if cmd == "chat":
            self._state["braindump_mode"] = False
            self._save_state()
            await self.send(bridge.BRAINDUMP_OFF_MSG, chat_id=chat_id, parse_mode="")
            return
        if self._state.get("braindump_mode"):
            await self._handle_braindump(chat_id, text)
            return
        try:
            rooms = await self._rooms()
            result = await rooms.chat(agent_id="telegram-bridge", text=payload["text"])
        except Exception as e:
            await self.send(
                f"Bridge error: {self._redact(str(e))[:200]}", chat_id=chat_id, parse_mode="",
            )
            return
        reply = bridge.strip_button_tokens(str(result.get("response") or ""))
        for chunk in bridge.chunk_text(reply):
            await self.send(chunk, chat_id=chat_id, parse_mode="")
        # gate_mode="gate" agents return the SAVED PENDING entries as
        # server_results (auto-exec results carry "ok" instead of "status").
        for action in result.get("server_results") or []:
            if isinstance(action, dict) and action.get("status") == "pending":
                await self._send_card(chat_id, action)
        self.spawn_background(
            self.kernel.events.emit(
                "telegram:inbound", {"room_id": "telegram-bridge", "chars": len(payload["text"])}
            )
        )

    async def _send_card(self, chat_id: str, action: dict):
        """Render a pending action as an HTML Apply/Reject card and send it."""
        text, markup = bridge.render_card(action)
        await self.send(text, chat_id=chat_id, parse_mode="HTML", reply_markup=markup)

    async def _handle_braindump(self, chat_id: str, text: str):
        """Brain Dump mode turn: run the text through the braindump pipeline,
        send back the clean summary, then one Apply/Reject card to keep the
        summary as a note plus one card per extracted task/journal/kb item.

        braindump files every item through the rooms review gate, so the cards
        are ordinary pending records — the existing _handle_callback applies or
        rejects them with no extra code. get_pending() is synchronous.
        """
        try:
            bd = await self._braindump()
            if not (bd and bd._enabled()):
                await self.send(bridge.BRAINDUMP_DISABLED_MSG, chat_id=chat_id, parse_mode="")
                return

            run = await bd._run_pipeline({"mode": "text", "text": text})
            results = run.get("results") or {}
            summary = ((results.get("summarize") or {}).get("summary") or "").strip()
            if run.get("status") == "error" or not summary:
                detail = self._redact(str(run.get("error") or "no summary produced"))
                await self.send(f"Couldn't process that: {detail[:150]}",
                                chat_id=chat_id, parse_mode="")
                return

            for chunk in bridge.chunk_text(summary):
                await self.send(chunk, chat_id=chat_id, parse_mode="")

            rooms = await self._rooms()

            # Offer to keep the cleaned summary as a note (full pending record).
            try:
                saved = await bd.propose_action(
                    app="braindump", method="save_summary_note",
                    args={"run_id": run.get("run_id", ""), "summary": summary},
                )
                if isinstance(saved, dict) and saved.get("id"):
                    await self._send_card(chat_id, saved)
            except Exception as e:
                print(f"[Telegram] braindump keep-summary propose failed: {e}")

            # Extract typed action items → re-hydrate each id → one card.
            run2 = await bd._resume_pipeline(run.get("run_id", ""))
            proposed = ((run2.get("results") or {}).get("extract_actions") or {}).get("proposed") or []
            for p in proposed:
                aid = p.get("action_id") if isinstance(p, dict) else None
                if not aid:
                    continue
                rec = rooms.get_pending(action_id=aid)
                if not isinstance(rec, dict):
                    continue
                await self._send_card(chat_id, rec)
        except Exception as e:
            print(f"[Telegram] braindump flow failed: {e}")
            await self.send(f"Brain Dump hit an error: {self._redact(str(e))[:150]}",
                            chat_id=chat_id, parse_mode="")
            return

        self.spawn_background(
            self.kernel.events.emit("telegram:inbound", {"room_id": "braindump", "chars": len(text)})
        )

    VOICE_MAX_SECONDS = 300

    async def _handle_voice(self, payload: dict):
        """Voice note → listen-capability transcript → normal message flow.

        The password challenge stays TYPED-only: a locked session refuses
        voice notes without transcribing (replay-resistant, and a
        mis-transcribed secret must never count as a failed attempt).
        The transcript is echoed back ('🎤 …') before processing so the
        user can verify what was heard.
        """
        chat_id = payload.get("chat_id", "")
        if self._auth_enforced() and not bridge.is_authed(self._state, time.time()):
            if not bridge.is_locked_out(self._state, time.time()):
                self._state["awaiting_password"] = True
                self._save_state()
            await self.send(
                "\U0001F512 Session locked — type the bridge password first "
                "(voice can't unlock).",
                chat_id=chat_id, parse_mode="",
            )
            return
        if payload.get("duration", 0) > self.VOICE_MAX_SECONDS:
            await self.send(
                f"Voice note too long — max {self.VOICE_MAX_SECONDS // 60} min.",
                chat_id=chat_id, parse_mode="",
            )
            return
        try:
            info = await self.get_file(payload["file_id"])
            fpath = info.get("file_path") or ""
            if not fpath:
                raise RuntimeError("getFile returned no file_path")
            audio = await self.download_file(fpath)
            tmp = self.kernel.config.data_dir / "telegram" / "voice" / f"{uuid.uuid4().hex}.ogg"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(audio)
            try:
                result = await self.kernel.capability("listen").execute(audio=str(tmp))
                transcript = (result.value or "").strip()
            finally:
                try:
                    tmp.unlink()
                except Exception:
                    pass
        except Exception as e:
            # Full detail server-side only; the chat message is redacted —
            # aiohttp errors can embed the file URL, which carries the bot
            # token, and Telegram chat history persists on their servers.
            print(f"[Telegram] voice transcription failed: {e}")
            await self.send(
                f"Couldn't transcribe that: {self._redact(str(e))[:150]}",
                chat_id=chat_id, parse_mode="",
            )
            return
        if not transcript:
            await self.send(
                "I couldn't make out any speech in that voice note.",
                chat_id=chat_id, parse_mode="",
            )
            return
        await self.send(f"\U0001F3A4 “{transcript}”", chat_id=chat_id, parse_mode="")
        await self._handle_message({
            "text": transcript,
            "chat_id": chat_id,
            "message_id": payload.get("message_id"),
        })

    async def _handle_callback(self, payload: dict):
        """Apply/Reject button tap → rooms pending lifecycle → edit the card."""
        if self._auth_enforced() and not bridge.is_authed(self._state, time.time()):
            # Session expired: refuse the tap (card stays pending) and route
            # the user into the password flow in chat.
            await self.answer_callback_query(
                payload.get("cq_id", ""),
                text="\U0001F512 Locked — send the bridge password in chat first",
            )
            now = time.time()
            if not self._state.get("awaiting_password") and not bridge.is_locked_out(self._state, now):
                self._state["awaiting_password"] = True
                self._save_state()
                await self.send(
                    bridge.AUTH_PROMPT, chat_id=payload.get("chat_id", ""), parse_mode="",
                )
            return
        await self.answer_callback_query(payload.get("cq_id", ""))
        parsed = bridge.parse_callback_data(payload.get("data", ""))
        if not parsed:
            return
        verb, action_id = parsed
        try:
            rooms = await self._rooms()
            # Expiry: a tap on a card scrolled back to days later shouldn't
            # silently fire. Chat + sender are already bound in
            # bridge.parse_update; this is the time dimension of the same idea.
            ttl_s = float(self.config("card_ttl_s", 86400) or 0)
            existing = rooms.get_pending(action_id)
            if existing and bridge.card_expired(
                existing,
                now_ts=datetime.now(timezone.utc).isoformat(),
                ttl_s=ttl_s,
            ):
                result = {"error": (
                    "card expired — re-propose or apply it on /rooms/"
                )}
            else:
                method = rooms.apply_pending if verb == "ap" else rooms.reject_pending
                result = await method(action_id)
        except Exception as e:
            result = {"error": str(e)[:200]}
        text = bridge.render_resolution(
            verb, result if isinstance(result, dict) else {}, action_id=action_id,
        )
        if payload.get("message_id"):
            await self.edit_message_text(
                payload.get("chat_id", ""), payload["message_id"], text, parse_mode="HTML"
            )
        else:
            await self.send(text, chat_id=payload.get("chat_id", ""), parse_mode="HTML")
        self.spawn_background(
            self.kernel.events.emit(
                "telegram:action_resolved", {"action_id": action_id, "verb": verb}
            )
        )
