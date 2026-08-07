"""Telegram two-way bridge — pure-helper + handler tests (no daemon, no token).

bridge.py is deliberately kernel-free, so it loads via bare importlib.
plugin.py does `from . import bridge`, so it loads under a registered
synthetic package (same pattern as tests/test_unit_rooms_logic.py).
Handler tests construct the plugin via __new__ with fakes — the real
Telegram API and the real rooms app are never touched.

The single most important test here is test_wrong_chat_id_dropped: the
chat_id allowlist in parse_update is the bridge's ENTIRE auth model.

Run: python -m pytest tests/test_unit_telegram_bridge.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "plugins" / "telegram"

OWNER = "111222333"  # synthetic owner chat_id — never a real one (Rule 13)


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location(
        "telegram_bridge_under_test", PLUGIN_DIR / "bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def plugin_module():
    """Load plugin.py under a synthetic package so `from . import bridge` resolves."""
    pkg_name = "tg_plugin_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = pkg
    for sub in ("bridge", "plugin"):
        full = f"{pkg_name}.{sub}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, PLUGIN_DIR / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.plugin"]


def _msg_update(uid=1, chat=OWNER, text="hello", **extra):
    m = {"message_id": 42, "chat": {"id": int(chat) if str(chat).isdigit() else chat}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"update_id": uid, "message": m}


def _cb_update(uid=2, chat=OWNER, frm=OWNER, data="ap:act-abc123"):
    return {
        "update_id": uid,
        "callback_query": {
            "id": "cq-1",
            "from": {"id": int(frm) if str(frm).isdigit() else frm},
            "data": data,
            "message": {"message_id": 99, "chat": {"id": int(chat) if str(chat).isdigit() else chat}},
        },
    }


# ── parse_update — the auth line ───────────────────────────────────────


class TestParseUpdate:
    def test_owner_text_accepted(self, bridge):
        kind, p = bridge.parse_update(_msg_update(), OWNER)
        assert kind == "message"
        assert p["text"] == "hello"
        assert p["chat_id"] == OWNER
        assert p["message_id"] == 42

    def test_wrong_chat_id_dropped(self, bridge):
        # THE auth test: a stranger who found the bot is silently dropped.
        kind, p = bridge.parse_update(_msg_update(chat="666"), OWNER)
        assert kind is None and p == {}

    def test_callback_classified(self, bridge):
        kind, p = bridge.parse_update(_cb_update(), OWNER)
        assert kind == "callback"
        assert p["cq_id"] == "cq-1"
        assert p["data"] == "ap:act-abc123"
        assert p["message_id"] == 99

    def test_callback_wrong_tapper_dropped(self, bridge):
        # Card lives in the owner chat but a different account tapped.
        kind, _ = bridge.parse_update(_cb_update(frm="666"), OWNER)
        assert kind is None

    def test_callback_wrong_chat_dropped(self, bridge):
        kind, _ = bridge.parse_update(_cb_update(chat="666"), OWNER)
        assert kind is None

    def test_owner_nontext_unsupported(self, bridge):
        kind, p = bridge.parse_update(_msg_update(text=None, photo=[{"file_id": "x"}]), OWNER)
        assert kind == "unsupported"
        assert p["chat_id"] == OWNER

    def test_edited_message_ignored(self, bridge):
        u = {"update_id": 3, "edited_message": {"chat": {"id": int(OWNER)}, "text": "edit"}}
        kind, _ = bridge.parse_update(u, OWNER)
        assert kind is None

    def test_malformed_and_no_allowlist(self, bridge):
        assert bridge.parse_update({}, OWNER) == (None, {})
        assert bridge.parse_update("junk", OWNER) == (None, {})
        # No configured chat_id → NOTHING is ever accepted (fail closed).
        assert bridge.parse_update(_msg_update(), "") == (None, {})

    def test_next_offset(self, bridge):
        ups = [_msg_update(uid=7), _msg_update(uid=12), _msg_update(uid=9)]
        assert bridge.next_offset(ups, 5) == 13
        assert bridge.next_offset([], 5) == 5


# ── callback data ──────────────────────────────────────────────────────


class TestCallbackData:
    def test_roundtrip_and_budget(self, bridge):
        data = bridge.callback_data("ap", "act-0123456789")
        assert len(data.encode("utf-8")) <= 64  # Telegram hard cap
        assert bridge.parse_callback_data(data) == ("ap", "act-0123456789")
        assert bridge.parse_callback_data("rj:act-x") == ("rj", "act-x")

    def test_malformed(self, bridge):
        for bad in ("", "ap", "zz:act-1", "ap:", None, 42):
            assert bridge.parse_callback_data(bad) is None


# ── rendering ──────────────────────────────────────────────────────────


class TestRenderCard:
    def test_hostile_args_escaped(self, bridge):
        action = {
            "id": "act-1",
            "app": "task",
            "method": "add",
            "args": {"text": '</pre><b onclick="x">&hack</b>'},
            "status": "pending",
        }
        text, markup = bridge.render_card(action)
        assert "</pre><b onclick" not in text  # escaped, can't break out
        assert "&lt;/pre&gt;" in text
        assert "task.add" in text
        btns = markup["inline_keyboard"][0]
        assert btns[0]["callback_data"] == "ap:act-1"
        assert btns[1]["callback_data"] == "rj:act-1"
        for b in btns:
            assert len(b["callback_data"].encode("utf-8")) <= 64

    def test_long_args_truncated(self, bridge):
        action = {"id": "act-2", "app": "a", "method": "m",
                  "args": {"blob": "x" * 5000}, "status": "pending"}
        text, _ = bridge.render_card(action)
        assert len(text) < 2000

    def test_diff_and_command_shapes(self, bridge):
        action = {
            "id": "act-3", "app": "rooms", "method": "write_note", "args": {},
            "proposed_changes": [{"path": "00_Inbox/x.md", "diff_lines": [1, 2, 3]}],
            "proposed_command": {"cmd": "echo <hi>"},
            "status": "pending",
        }
        text, _ = bridge.render_card(action)
        assert "00_Inbox/x.md" in text
        assert "3 diff lines" in text
        # The command is deliberately NOT disclosed any more — it used to be
        # printed verbatim ("&lt;hi&gt;"), which put a full shell command into
        # Telegram's chat history. Only its size travels; the command itself is
        # read locally. See tests/test_unit_telegram_disclosure.py.
        assert "&lt;hi&gt;" not in text
        assert "command hidden" in text


class TestRenderResolution:
    def test_applied(self, bridge):
        action = {"app": "task", "method": "add", "status": "applied",
                  "result": "{'ok': True, 'id': 't-1'}"}
        out = bridge.render_resolution("ap", action)
        assert out.startswith("✅") and "task.add" in out and "t-1" in out

    def test_rejected(self, bridge):
        action = {"app": "task", "method": "add", "status": "rejected"}
        assert bridge.render_resolution("rj", action).startswith("❌")

    def test_error_and_already_resolved(self, bridge):
        out = bridge.render_resolution("ap", {"error": "already applied"})
        assert out.startswith("⚠") and "already applied" in out
        # error + action detail shape from apply_pending failure path
        out2 = bridge.render_resolution(
            "ap", {"error": "boom <tag>", "action": {"app": "x", "method": "y"}}
        )
        assert "x.y" in out2 and "&lt;tag&gt;" in out2


class TestTextHelpers:
    def test_strip_button_tokens(self, bridge):
        raw = 'Do it [BUTTON:Add|DO:task.add({"text":"a"})] now'
        out = bridge.strip_button_tokens(raw)
        assert "[BUTTON" not in out
        assert "Do it" in out and "now" in out

    def test_chunk_passthrough_and_empty(self, bridge):
        assert bridge.chunk_text("short") == ["short"]
        assert bridge.chunk_text("") == []

    def test_chunk_newline_boundary(self, bridge):
        text = "\n".join(["line " + str(i) for i in range(1000)])
        chunks = bridge.chunk_text(text, limit=200)
        assert all(len(c) <= 200 for c in chunks)
        # No content lost
        assert sum(c.count("line") for c in chunks) == 1000

    def test_chunk_no_newline_hard_split(self, bridge):
        chunks = bridge.chunk_text("x" * 950, limit=300)
        assert all(len(c) <= 300 for c in chunks)
        assert sum(len(c) for c in chunks) == 950


class TestClearCommand:
    def test_recognises_variants(self, bridge):
        for t in ("/clear", "/new", "/reset", "  /CLEAR ", "/clear@mybot"):
            assert bridge.is_clear_command(t), t

    def test_ignores_non_commands(self, bridge):
        for t in ("clear", "/clearing the desk", "please /clear", "", "/help"):
            assert not bridge.is_clear_command(t), t


class TestState:
    def test_roundtrip(self, bridge, tmp_path):
        p = tmp_path / "tg" / "state.json"
        bridge.save_state(p, {"offset": 123})
        assert bridge.load_state(p) == {"offset": 123}

    def test_missing_and_corrupt(self, bridge, tmp_path):
        assert bridge.load_state(tmp_path / "nope.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert bridge.load_state(bad) == {}


class TestParseCommand:
    def test_dump_and_chat(self, bridge):
        assert bridge.parse_command("/dump") == "dump"
        assert bridge.parse_command("/DUMP") == "dump"
        assert bridge.parse_command("/dump chase the report") == "dump"
        assert bridge.parse_command("/dump@MyBot") == "dump"
        assert bridge.parse_command("  /chat  ") == "chat"

    def test_non_commands(self, bridge):
        for t in ("dump", "/dumpx", "hello /dump", "", "   ", None, "/other", 42):
            assert bridge.parse_command(t) is None


# ── plugin handlers (fakes; no network, no rooms app) ──────────────────


class _FakeRooms:
    def __init__(self):
        self.calls: list = []

    async def chat(self, agent_id: str, text: str):
        self.calls.append(("chat", agent_id, text))
        return {
            "response": "Sure. [BUTTON:x|DO:task.add({\"text\":\"a\"})]",
            "server_results": [
                {"id": "act-9", "app": "task", "method": "add",
                 "args": {"text": "buy milk"}, "status": "pending"},
                {"ok": True},  # auto-exec-shaped result must NOT render a card
            ],
        }

    async def apply_pending(self, action_id: str):
        self.calls.append(("apply", action_id))
        return {"app": "task", "method": "add", "status": "applied", "result": "ok"}

    async def reject_pending(self, action_id: str):
        self.calls.append(("reject", action_id))
        return {"app": "task", "method": "add", "status": "rejected"}

    def get_pending(self, action_id: str):
        # SYNC (mirrors rooms.pending.get_pending) — the braindump card path
        # re-hydrates each proposed id into a full pending record.
        self.calls.append(("get_pending", action_id))
        return {"id": action_id, "app": "task", "method": "add",
                "args": {"text": "chase report"}, "status": "pending"}


class _FakeBraindump:
    """Stand-in for the braindump app: the enabled flag, the two pipeline
    calls, and propose_action — each returning the shapes _handle_braindump
    reads (see apps/public/standard/braindump/pipeline.py)."""

    def __init__(self, enabled=True, summary="Clean summary.", proposed=None,
                 status="paused"):
        self._is_enabled = enabled
        self._summary = summary
        self._proposed = ([{"action_id": "act-t1", "type": "task",
                            "verb": "task.add", "summary": "chase report"}]
                          if proposed is None else proposed)
        self._status = status
        self.calls: list = []

    def _enabled(self):
        return self._is_enabled

    async def _run_pipeline(self, inputs, *, run_id=None):
        self.calls.append(("run", inputs))
        return {"run_id": "run-1", "status": self._status, "error": None,
                "results": {"transcribe": {"transcript": inputs.get("text", "")},
                            "summarize": {"summary": self._summary}}}

    async def propose_action(self, *, app, method, args=None, **kw):
        self.calls.append(("propose", app, method))
        return {"id": "act-sum", "app": app, "method": method,
                "args": args or {}, "status": "pending"}

    async def _resume_pipeline(self, run_id, *, summary_override=""):
        self.calls.append(("resume", run_id))
        return {"run_id": run_id, "status": "complete",
                "results": {"extract_actions": {"proposed": self._proposed,
                                                "count": len(self._proposed)}}}


def _make_plugin(plugin_module, rooms, braindump=None):
    p = plugin_module.TelegramPlugin.__new__(plugin_module.TelegramPlugin)
    p._chat_id = OWNER
    p._token = "tok"
    p._bridge_ready = True
    p._config = {}   # no bridge_password → periodic auth disabled by default
    p._state = {}
    p.sent: list = []
    p.edited: list = []
    p.acked: list = []
    p.deleted: list = []

    async def _delete(chat_id, message_id):
        p.deleted.append(message_id)
        return {"ok": True}

    p.delete_message = _delete

    async def _send(text, chat_id="", parse_mode="Markdown", reply_markup=None):
        p.sent.append({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
        return {"ok": True}

    async def _edit(chat_id, message_id, text, parse_mode="", reply_markup=None):
        p.edited.append({"message_id": message_id, "text": text})
        return {"ok": True}

    async def _ack(cq_id, text=""):
        p.acked.append(cq_id)
        return {"ok": True}

    p.send = _send
    p.edit_message_text = _edit
    p.answer_callback_query = _ack

    class _Events:
        async def emit(self, *a, **k):
            return None

    _inst = {"rooms": rooms}
    if braindump is not None:
        _inst["braindump"] = braindump

    class _Apps:
        instances = _inst

        async def load(self, app_id):
            return _inst.get(app_id)

    class _Kernel:
        events = _Events()
        apps = _Apps()

    p.kernel = _Kernel()
    return p


class TestHandlers:
    def test_message_sends_reply_and_card(self, plugin_module):
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms)

        async def run():
            await p._handle_message({"text": "add milk", "chat_id": OWNER, "message_id": 1})
            await asyncio.sleep(0)  # let the fire-and-forget emit task run

        asyncio.run(run())
        assert ("chat", "telegram-bridge", "add milk") in rooms.calls
        # First send = plain-text reply with [BUTTON:] stripped
        assert p.sent[0]["parse_mode"] == ""
        assert "[BUTTON" not in p.sent[0]["text"]
        # Exactly ONE card (the pending entry; the ok-shaped result is skipped)
        cards = [s for s in p.sent if s["reply_markup"]]
        assert len(cards) == 1
        assert cards[0]["parse_mode"] == "HTML"
        assert "task.add" in cards[0]["text"]
        assert cards[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "ap:act-9"

    def test_callback_apply_edits_card(self, plugin_module):
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms)

        async def run():
            await p._handle_callback({
                "cq_id": "cq-1", "chat_id": OWNER, "message_id": 99,
                "data": "ap:act-9",
            })
            await asyncio.sleep(0)

        asyncio.run(run())
        assert p.acked == ["cq-1"]
        assert ("apply", "act-9") in rooms.calls
        assert p.edited and p.edited[0]["message_id"] == 99
        assert p.edited[0]["text"].startswith("✅")

    def test_callback_reject(self, plugin_module):
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms)

        async def run():
            await p._handle_callback({
                "cq_id": "cq-2", "chat_id": OWNER, "message_id": 100,
                "data": "rj:act-9",
            })
            await asyncio.sleep(0)

        asyncio.run(run())
        assert ("reject", "act-9") in rooms.calls
        assert p.edited[0]["text"].startswith("❌")

    def test_callback_malformed_data_noop(self, plugin_module):
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms)

        async def run():
            await p._handle_callback({
                "cq_id": "cq-3", "chat_id": OWNER, "message_id": 1, "data": "zz:whatever",
            })

        asyncio.run(run())
        assert p.acked == ["cq-3"]  # still acked so the client spinner stops
        assert rooms.calls == [] and p.edited == []


class TestBraindumpMode:
    """/dump ↔ /chat toggle + the braindump-routed message flow."""

    def test_dump_toggles_mode_on(self, plugin_module):
        rooms = _FakeRooms()
        bd = _FakeBraindump()
        p = _make_plugin(plugin_module, rooms, braindump=bd)

        asyncio.run(p._handle_message({"text": "/dump", "chat_id": OWNER}))
        assert p._state.get("braindump_mode") is True
        assert "Brain Dump mode on" in p.sent[-1]["text"]
        assert rooms.calls == []  # NOT routed to the chat agent

    def test_dump_disabled_stays_off(self, plugin_module):
        rooms = _FakeRooms()
        bd = _FakeBraindump(enabled=False)
        p = _make_plugin(plugin_module, rooms, braindump=bd)

        asyncio.run(p._handle_message({"text": "/dump", "chat_id": OWNER}))
        assert not p._state.get("braindump_mode")
        assert "isn't enabled" in p.sent[-1]["text"]

    def test_chat_toggles_mode_off(self, plugin_module):
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms, braindump=_FakeBraindump())
        p._state = {"braindump_mode": True}

        asyncio.run(p._handle_message({"text": "/chat", "chat_id": OWNER}))
        assert p._state.get("braindump_mode") is False
        assert "normal chat" in p.sent[-1]["text"].lower()

    def test_message_in_mode_routes_to_braindump(self, plugin_module):
        rooms = _FakeRooms()
        bd = _FakeBraindump(summary="Chase the cable report; call mum.")
        p = _make_plugin(plugin_module, rooms, braindump=bd)
        p._state = {"braindump_mode": True}

        async def run():
            await p._handle_message({"text": "chase report, call mum", "chat_id": OWNER})
            await asyncio.sleep(0)  # let the fire-and-forget emit task run

        asyncio.run(run())
        # NOT routed to the chat agent
        assert all(c[0] != "chat" for c in rooms.calls)
        # Summary went out as a plain-text message
        assert any(s["parse_mode"] == "" and "Chase the cable report" in s["text"]
                   for s in p.sent)
        # Two cards: keep-summary + one extracted item, both HTML w/ buttons
        cards = [s for s in p.sent if s["reply_markup"]]
        assert len(cards) == 2
        assert all(c["parse_mode"] == "HTML" for c in cards)
        assert "braindump.save_summary_note" in cards[0]["text"]  # keep-summary first
        assert ("get_pending", "act-t1") in rooms.calls  # item re-hydrated
        assert "task.add" in cards[1]["text"]

    def test_error_status_reports_not_crashes(self, plugin_module):
        rooms = _FakeRooms()
        bd = _FakeBraindump(status="error", summary="")
        p = _make_plugin(plugin_module, rooms, braindump=bd)
        p._state = {"braindump_mode": True}

        asyncio.run(p._handle_message({"text": "mumble", "chat_id": OWNER}))
        assert any("Couldn't process" in s["text"] for s in p.sent)
        cards = [s for s in p.sent if s["reply_markup"]]
        assert cards == []  # nothing proposed on a failed run


class TestPeriodicAuth:
    """Password/token session challenge — the second factor over the chat_id line."""

    def test_verify_password(self, bridge):
        assert bridge.verify_password("s3cret", "s3cret")
        assert bridge.verify_password("  s3cret \n", "s3cret")  # stripped
        assert not bridge.verify_password("wrong", "s3cret")
        assert not bridge.verify_password("", "s3cret")
        assert not bridge.verify_password("anything", "")  # empty secret fails closed

    def test_auth_state_helpers(self, bridge):
        import time as _t
        now = _t.time()
        st: dict = {}
        assert not bridge.is_authed(st, now)
        bridge.note_auth_success(st, now, 3600)
        assert bridge.is_authed(st, now + 3599)
        assert not bridge.is_authed(st, now + 3601)  # periodic expiry
        # 5 failures trip the lockout
        st2: dict = {}
        for _ in range(4):
            assert bridge.note_auth_failure(st2, now) is False
        assert bridge.note_auth_failure(st2, now) is True
        assert bridge.is_locked_out(st2, now + 1)
        assert not bridge.is_locked_out(st2, now + 901)

    def _authed_plugin(self, plugin_module, rooms):
        p = _make_plugin(plugin_module, rooms)
        p._config = {"bridge_password": "s3cret", "auth_ttl_hours": 12}
        return p

    def test_expired_first_message_prompts_not_processed(self, plugin_module):
        rooms = _FakeRooms()
        p = self._authed_plugin(plugin_module, rooms)

        asyncio.run(p._handle_message({"text": "add milk", "chat_id": OWNER, "message_id": 1}))
        # Not processed as chat, no failure burned, prompt sent, now awaiting.
        assert rooms.calls == []
        assert p._state.get("awaiting_password") is True
        assert p._state.get("fails", 0) == 0
        assert "password" in p.sent[-1]["text"].lower()

    def test_correct_password_unlocks_and_scrubs(self, plugin_module):
        rooms = _FakeRooms()
        p = self._authed_plugin(plugin_module, rooms)
        p._state = {"awaiting_password": True}

        asyncio.run(p._handle_message({"text": "s3cret", "chat_id": OWNER, "message_id": 7}))
        assert rooms.calls == []  # the password is never sent to the LLM
        assert p.deleted == [7]   # secret scrubbed from chat history
        assert "Unlocked" in p.sent[-1]["text"]
        import time as _t
        assert p._state["authed_until"] > _t.time()

        # Next message flows through normally.
        asyncio.run(p._handle_message({"text": "add milk", "chat_id": OWNER, "message_id": 8}))
        assert ("chat", "telegram-bridge", "add milk") in rooms.calls

    def test_wrong_password_five_times_locks_out(self, plugin_module):
        rooms = _FakeRooms()
        p = self._authed_plugin(plugin_module, rooms)
        p._state = {"awaiting_password": True}

        async def attempt(text):
            # Re-arm awaiting between attempts (a lockout clears it).
            if not p._state.get("lock_until"):
                p._state["awaiting_password"] = True
            await p._handle_message({"text": text, "chat_id": OWNER, "message_id": 1})

        async def run():
            for _ in range(5):
                await attempt("nope")
            # Locked now: even the right password is refused during lockout.
            await attempt("s3cret")

        asyncio.run(run())
        assert rooms.calls == []
        assert p._state.get("lock_until")
        assert "locked" in p.sent[-1]["text"].lower()
        assert p.deleted == []  # nothing scrubbed, nothing unlocked

    def test_callback_refused_while_locked(self, plugin_module):
        rooms = _FakeRooms()
        p = self._authed_plugin(plugin_module, rooms)  # no authed_until → expired

        asyncio.run(p._handle_callback({
            "cq_id": "cq-9", "chat_id": OWNER, "message_id": 99, "data": "ap:act-9",
        }))
        assert rooms.calls == []      # apply NOT executed — card stays pending
        assert p.edited == []
        assert p._state.get("awaiting_password") is True
        assert "password" in p.sent[-1]["text"].lower()

    def test_disabled_when_no_secret(self, plugin_module):
        # No bridge_password + no reachable network.password → gate is a no-op
        # (covered implicitly by every TestHandlers test; assert explicitly).
        rooms = _FakeRooms()
        p = _make_plugin(plugin_module, rooms)
        assert p._auth_enforced() is False
        asyncio.run(p._handle_message({"text": "hi", "chat_id": OWNER, "message_id": 1}))
        assert ("chat", "telegram-bridge", "hi") in rooms.calls


class TestTwoWayFlag:
    def test_flag_walk(self, plugin_module):
        p = plugin_module.TelegramPlugin.__new__(plugin_module.TelegramPlugin)
        p._config = {"feature": {"telegram-two-way": {"enabled": True}}}
        assert p._two_way_enabled() is True
        p._config = {"feature": {"telegram-two-way": {"enabled": False}}}
        assert p._two_way_enabled() is False
        p._config = {}
        assert p._two_way_enabled() is False
        p._config = {"feature": "junk"}
        assert p._two_way_enabled() is False

    def test_bot_commands_include_clear_without_dropping_mode_toggles(self, plugin_module):
        p = plugin_module.TelegramPlugin.__new__(plugin_module.TelegramPlugin)
        commands = p._bot_commands()
        assert [c["command"] for c in commands] == ["dump", "chat", "clear"]


class TestBuildBridgeActions:
    """Registry-derived allowlist — voice surface + legacy intents, VA excluded."""

    def _registry(self):
        from emptyos.sdk.verb_registry import VerbEntry, VerbRegistry
        return VerbRegistry([
            VerbEntry(verb="expense.add", app_id="expense", method="add",
                      args={"amount": "number", "description": "string", "category": "string?"},
                      eligibility="stable", surfaces=("voice", "agent", "mcp"),
                      voice={"method": "voice_add_expense"}),
            VerbEntry(verb="task.add", app_id="task", method="add",
                      surfaces=("voice", "agent"), voice={"method": "voice_add_task"}),
            VerbEntry(verb="task.list_today", app_id="task", method="voice_list_today",
                      surfaces=("voice",)),
            VerbEntry(verb="aura.remember", app_id="voice-assistant",
                      method="voice_remember", surfaces=("voice",)),
            VerbEntry(verb="commons.share", app_id="commons", method="share",
                      surfaces=("agent",)),
        ])

    def _plugin(self, plugin_module, apps):
        p = plugin_module.TelegramPlugin.__new__(plugin_module.TelegramPlugin)

        class _K:
            pass

        _K.apps = apps
        p.kernel = _K()
        return p

    def test_registry_derivation(self, plugin_module):
        reg = self._registry()

        class _Apps:
            def get_verbs(self):
                return reg

            def get_contributions(self, target, slot):
                assert (target, slot) == ("voice-assistant", "intent")
                return [
                    {"_app_id": "note", "verb": "note.create", "method": "voice_create_note"},
                    # task is registry-migrated → its legacy row must be skipped
                    {"_app_id": "task", "verb": "task.old", "method": "legacy_dupe"},
                ]

        actions = self._plugin(plugin_module, _Apps())._build_bridge_actions()
        assert actions["expense"] == ["voice_add_expense"]   # voice wrapper, not canonical
        assert actions["task"] == ["voice_add_task", "voice_list_today"]  # no legacy dupe
        assert actions["note"] == ["voice_create_note"]       # legacy-only app included
        assert "voice-assistant" not in actions               # aura.* excluded
        assert "commons" not in actions                       # agent-only surface excluded

    def test_fallback_on_registry_failure(self, plugin_module):
        class _Apps:
            def get_verbs(self):
                raise RuntimeError("boom")

        actions = self._plugin(plugin_module, _Apps())._build_bridge_actions()
        assert actions == plugin_module.TelegramPlugin.BRIDGE_SERVER_ACTIONS


class TestSpeakableResult:
    """Voice-shaped {say, card, link} results print as sentences, not dict repr."""

    def test_say_unwrapped(self, bridge):
        raw = "{'say': 'Logged $23 for lunch (Food)', 'link': {'text': 'Open expenses', 'href': '/expense/'}}"
        out = bridge._speakable_result(raw)
        assert out.startswith("Logged $23 for lunch")
        assert "Open expenses: /expense/" in out
        assert "{'say'" not in out

    def test_card_list_rendered_as_lines(self, bridge):
        raw = ("{'say': 'Here is today.', 'card': {'renderer': 'task-list', "
               "'data': [{'text': 'buy milk'}, {'text': 'call agent'}]}}")
        out = bridge._speakable_result(raw)
        assert "Here is today." in out
        assert "• buy milk" in out and "• call agent" in out

    def test_plain_string_passthrough(self, bridge):
        assert bridge._speakable_result("wrote 1 file(s): x.md") == "wrote 1 file(s): x.md"
        assert bridge._speakable_result("") == ""

    def test_malformed_dict_passthrough(self, bridge):
        assert bridge._speakable_result("{not python") == "{not python"

    def test_resolution_uses_say(self, bridge):
        action = {"app": "expense", "method": "voice_add_expense", "status": "applied",
                  "result": "{'say': 'Logged $23 for lunch'}"}
        out = bridge.render_resolution("ap", action)
        assert "Logged $23 for lunch" in out
        assert "{'say'" not in out


def _voice_update(uid=5, chat=OWNER, duration=4, file_id="vf-1"):
    return {
        "update_id": uid,
        "message": {
            "message_id": 55,
            "chat": {"id": int(chat) if str(chat).isdigit() else chat},
            "voice": {"file_id": file_id, "duration": duration, "mime_type": "audio/ogg"},
        },
    }


class TestVoiceNotes:
    def test_parse_owner_voice(self, bridge):
        kind, p = bridge.parse_update(_voice_update(), OWNER)
        assert kind == "voice"
        assert p["file_id"] == "vf-1" and p["duration"] == 4
        assert p["chat_id"] == OWNER

    def test_parse_wrong_chat_voice_dropped(self, bridge):
        kind, _ = bridge.parse_update(_voice_update(chat="666"), OWNER)
        assert kind is None

    def _voice_plugin(self, plugin_module, rooms, tmp_path, transcript="add a task to buy milk"):
        p = _make_plugin(plugin_module, rooms)
        p.got_file: list = []

        class _Cfg:
            data_dir = tmp_path

        p.kernel.config = _Cfg()

        class _Result:
            value = transcript

        class _Listen:
            async def execute(self, **kw):
                p.listened = kw
                return _Result()

        p.kernel.capability = lambda name: _Listen()

        async def _get_file(file_id):
            p.got_file.append(file_id)
            return {"file_path": "voice/x.oga"}

        async def _download(fp):
            return b"OGGDATA"

        p.get_file = _get_file
        p.download_file = _download
        return p

    def test_voice_transcribed_and_processed(self, plugin_module, tmp_path):
        rooms = _FakeRooms()
        p = self._voice_plugin(plugin_module, rooms, tmp_path)

        async def run():
            await p._handle_voice({"file_id": "vf-1", "duration": 4,
                                   "chat_id": OWNER, "message_id": 55})
            await asyncio.sleep(0)

        asyncio.run(run())
        # Echo first, then the transcript ran as a normal message.
        assert any("\U0001F3A4" in s["text"] and "buy milk" in s["text"] for s in p.sent)
        assert ("chat", "telegram-bridge", "add a task to buy milk") in rooms.calls
        # Temp audio cleaned up.
        assert not list((tmp_path / "telegram" / "voice").glob("*.ogg"))

    def test_voice_refused_while_locked(self, plugin_module, tmp_path):
        rooms = _FakeRooms()
        p = self._voice_plugin(plugin_module, rooms, tmp_path)
        p._config = {"bridge_password": "s3cret", "auth_ttl_hours": 12}  # locked

        asyncio.run(p._handle_voice({"file_id": "vf-1", "duration": 4,
                                     "chat_id": OWNER, "message_id": 55}))
        assert p.got_file == []          # never even downloaded
        assert rooms.calls == []
        assert "type the bridge password" in p.sent[-1]["text"]
        assert p._state.get("awaiting_password") is True

    def test_voice_too_long_refused(self, plugin_module, tmp_path):
        rooms = _FakeRooms()
        p = self._voice_plugin(plugin_module, rooms, tmp_path)

        asyncio.run(p._handle_voice({"file_id": "vf-1", "duration": 999,
                                     "chat_id": OWNER, "message_id": 55}))
        assert p.got_file == [] and rooms.calls == []
        assert "too long" in p.sent[-1]["text"]

    def test_voice_empty_transcript(self, plugin_module, tmp_path):
        rooms = _FakeRooms()
        p = self._voice_plugin(plugin_module, rooms, tmp_path, transcript="  ")

        asyncio.run(p._handle_voice({"file_id": "vf-1", "duration": 4,
                                     "chat_id": OWNER, "message_id": 55}))
        assert rooms.calls == []
        assert "couldn't make out" in p.sent[-1]["text"]

    def test_transcription_error_redacts_token(self, plugin_module, tmp_path):
        rooms = _FakeRooms()
        p = self._voice_plugin(plugin_module, rooms, tmp_path)

        async def _boom(file_id):
            raise RuntimeError(f"GET https://api.telegram.org/file/bot{p._token}/x failed")

        p.get_file = _boom
        asyncio.run(p._handle_voice({"file_id": "vf-1", "duration": 4,
                                     "chat_id": OWNER, "message_id": 55}))
        assert p._token not in p.sent[-1]["text"]
        assert "[redacted]" in p.sent[-1]["text"]
