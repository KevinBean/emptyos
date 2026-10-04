"""tg-life-surface T9 + T11 — one manager on two surfaces.

The phone bot and the computer manager session share two vault notes,
`manager/profile.md` and `manager/log.md`. These pins cover both halves:

* reading — every phone turn hands rooms the profile plus the newest log lines
  as live context, bounded, with frontmatter and editor notes left out;
* writing — a decision the model marks `[LOG: …]` and every verb that ran
  from the phone (auto-run, Apply tap, Undo) becomes one dated `phone` line,
  appended under the note lock; the token never reaches the phone;
* T11 — persona v3 no longer says nothing runs before Apply, and v2 is in the
  superseded list so the live bot reseeds.

Kernel-free: bridge.py is pure; plugin.py runs against a fake kernel whose
vault_map points at a tmp dir.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "plugins" / "telegram"
OWNER = "424242"
WHEN = datetime(2026, 10, 3, 14, 5)

PROFILE = """---
created: 2026-09-30
tags:
  - emptyos-manager
---

# Manager profile

> This note is sent to a cloud model with every phone turn.

## Persona and voice

- A calm, practical chief-of-staff.
"""

LOG_HEAD = """---
created: 2026-09-30
---

# Manager log

Shared memory between the phone bot and the computer manager session.

## Log
"""


def _log_lines(n: int) -> str:
    return "".join(f"- 2026-10-0{1 + i // 60} {i // 60 % 24:02d}:{i % 60:02d} · computer · entry {i}\n"
                   for i in range(n))


@pytest.fixture(scope="module")
def tg():
    pkg_name = "tg_plugin_pkg_t9"
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
    return types.SimpleNamespace(bridge=sys.modules[f"{pkg_name}.bridge"],
                                 plugin=sys.modules[f"{pkg_name}.plugin"])


@pytest.fixture
def bridge(tg):
    return tg.bridge


# ── pure helpers ─────────────────────────────────────────────────────────


class TestReadSide:
    def test_profile_drops_frontmatter_and_editor_notes(self, bridge):
        text = bridge.manager_profile_text(PROFILE)
        assert text.startswith("# Manager profile")
        assert "created:" not in text and "emptyos-manager" not in text
        assert "sent to a cloud model" not in text      # the `>` note is for editors
        assert "chief-of-staff" in text

    def test_profile_is_capped_at_a_line_boundary(self, bridge):
        long = "# P\n" + "".join(f"- rule {i} " + "x" * 40 + "\n" for i in range(200))
        text = bridge.manager_profile_text(long, max_chars=500)
        assert len(text) <= 500
        assert text.endswith("\n…")
        assert all(ln.startswith(("# P", "- rule", "…")) for ln in text.splitlines())

    def test_digest_keeps_only_the_newest_dated_entries(self, bridge):
        digest = bridge.manager_log_digest(LOG_HEAD + _log_lines(25))
        lines = digest.splitlines()
        assert len(lines) == bridge.MANAGER_LOG_MAX_LINES
        assert lines[-1].endswith("entry 24") and lines[0].endswith("entry 15")
        assert "Shared memory" not in digest and "## Log" not in digest

    def test_digest_drops_oldest_lines_to_fit_the_char_budget(self, bridge):
        raw = "".join(f"- 2026-10-01 10:{i:02d} · phone · " + "y" * 200 + f" {i}\n"
                      for i in range(10))
        digest = bridge.manager_log_digest(raw, max_lines=10, max_chars=700)
        assert len(digest) <= 700
        assert digest.splitlines()[-1].endswith(" 9")   # newest survives

    def test_context_names_both_parts_and_is_empty_with_neither(self, bridge):
        ctx = bridge.manager_context(PROFILE, LOG_HEAD + _log_lines(2))
        assert "Manager profile" in ctx and "Manager log, newest last" in ctx
        assert "entry 1" in ctx
        assert bridge.manager_context("", "") == ""
        assert bridge.manager_context("", LOG_HEAD) == ""   # header only, no entries


class TestWriteSide:
    def test_log_tokens_are_stripped_and_capped(self, bridge):
        reply = ("OK, skipping it.\n[LOG: user skips the gym this week]\n"
                 "[LOG: two] [LOG: three]")
        clean, lines = bridge.extract_log_tokens(reply)
        assert "[LOG" not in clean and clean == "OK, skipping it."
        assert lines == ["user skips the gym this week", "two"]

    def test_reply_without_a_token_is_untouched(self, bridge):
        assert bridge.extract_log_tokens("Done.") == ("Done.", [])

    def test_log_line_shape_and_bounds(self, bridge):
        line = bridge.manager_log_line(WHEN, "  decided:\n no pushes  on Sunday ")
        assert line == "- 2026-10-03 14:05 · phone · decided: no pushes on Sunday"
        long = bridge.manager_log_line(WHEN, "z" * 500)
        assert len(long.split(" · phone · ")[1]) == bridge.MANAGER_LOG_LINE_MAX
        assert bridge.manager_log_line(WHEN, "   ") is None

    def test_log_line_refuses_a_secret(self, bridge):
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        assert bridge.manager_log_line(WHEN, f"my token is {token}") is None

    def test_action_text_is_the_verb_and_id_never_the_reply(self, bridge):
        # The reply echoes the argument (a fee, a name, a diagnosis) and
        # third-party text, and the log goes back to the model every turn.
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        task = {"id": "act-5", "app": "task", "method": "voice_add_task",
                "decided_as": "task.add",
                "result": "{'say': 'Added: pay Dr Lee $2,300 %s'}" % token}
        assert bridge.action_log_text(task) == "ran task.add (act-5)"
        assert bridge.action_log_text(task, undone=True) == "undid task.add (act-5)"
        bare = {"app": "expense", "method": "voice_add_expense", "result": "Logged $45"}
        assert bridge.action_log_text(bare) == "ran expense.voice_add_expense"

    def test_log_token_keeps_a_wikilink_and_ignores_case(self, bridge):
        clean, lines = bridge.extract_log_tokens("ok [LOG: moved [[note]] to archive]")
        assert (clean, lines) == ("ok", ["moved [[note]] to archive"])
        assert bridge.extract_log_tokens("Fine.\n[log: lower]") == ("Fine.", ["lower"])

    def test_one_long_entry_cannot_crowd_out_the_rest(self, bridge):
        raw = "".join(f"- 2026-10-01 10:{i:02d} · computer · " + "w" * 600 + "\n"
                      for i in range(10))
        lines = bridge.manager_log_digest(raw).splitlines()
        assert len(lines) >= 4
        assert all(len(ln) <= bridge.MANAGER_LOG_DIGEST_LINE_MAX for ln in lines)


class TestPersonaV3:
    def test_no_longer_promises_a_card_before_anything_runs(self, bridge):
        p = bridge.TELEGRAM_BRIDGE_PERSONA
        assert "BEFORE anything runs" not in p and "Nothing executes until" not in p
        assert "Undo" in p and "Apply/Reject card" in p
        assert "never claim it already happened" in p and "never promise a card" in p
        # Only some verbs auto-run, and only on deployments that enable it:
        # the persona must not name a class of verb as one that always does.
        assert "You cannot tell which" in p
        assert "a reminder, an expense" not in p

    def test_follows_the_shared_profile_and_teaches_the_log_token(self, bridge):
        p = bridge.TELEGRAM_BRIDGE_PERSONA
        assert "manager profile" in p and "[LOG:" in p
        assert "do not add a [LOG:] for them" in p
        # The computer side reads these lines; a model-written "approved" would
        # read as the user's OK there.
        assert "never write that they approved or authorised something" in p

    def test_keeps_v2_routing_rules(self, bridge):
        p = bridge.TELEGRAM_BRIDGE_PERSONA
        for rule in ("[DO:app.method({})]", "dictionary lookup", "半小时后",
                     "task to add, even when it names a report", "Never emit [BUTTON:"):
            assert rule in p, rule

    def test_every_retired_seed_reseeds(self, bridge):
        assert bridge.SUPERSEDED_PERSONAS == (bridge.TELEGRAM_BRIDGE_PERSONA_V1,
                                              bridge.TELEGRAM_BRIDGE_PERSONA_V2)


# ── plugin wiring ────────────────────────────────────────────────────────


class _Rooms:
    def __init__(self, response="Got it.", results=(), record=None):
        self.chats: list[dict] = []
        self._response = response
        self._results = list(results)
        self._record = record

    async def chat(self, agent_id, text, context=""):
        self.chats.append({"agent_id": agent_id, "text": text, "context": context})
        return {"response": self._response, "server_results": self._results}

    async def apply_pending(self, action_id, **kw):
        return dict(self._record, status="applied")

    def get_pending(self, action_id):
        return self._record

    async def undo_action(self, action_id, *, max_age_s=0):
        return {"ok": True, "action": self._record}


class _VaultMap:
    def __init__(self, root: Path):
        self.root = root

    def get_absolute(self, app_id, key, default=""):
        assert (app_id, key) == ("telegram", "manager_dir")
        return self.root

    def get(self, app_id, key, default=""):
        assert (app_id, key) == ("telegram", "manager_dir")
        return "30_Resources/EmptyOS/manager"


class _NoteLock:
    """Records the log's size when the lock is taken and when it is released,
    so a test can tell a write made inside the lock from one made outside."""

    def __init__(self):
        self.keys: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.log: Path | None = None

    def note_lock(self, rel):
        self.keys.append(str(rel))
        outer = self

        class _L:
            async def __aenter__(self):
                self.before = outer.log.stat().st_size

            async def __aexit__(self, *a):
                outer.sizes.append((self.before, outer.log.stat().st_size))
        return _L()


class _Settings:
    def __init__(self, tz):
        self.tz = tz

    def get(self, key, default=None):
        return self.tz if key == "location.timezone" else default


def _plugin(tg, rooms, vault: Path, *, config=None, tz="UTC", real_clock=False):
    p = tg.plugin.TelegramPlugin.__new__(tg.plugin.TelegramPlugin)
    p._chat_id = OWNER
    p._token = "tok"
    p._bridge_ready = True
    p._config = {"manager_memory": True, **(config or {})}
    p._state = {}
    p._bg_tasks = set()
    p.sent = []
    p.edited = []

    async def _send(text, chat_id="", parse_mode="Markdown", reply_markup=None):
        p.sent.append(text)
        return {"ok": True}

    async def _edit(chat_id, message_id, text, parse_mode="", reply_markup=None):
        p.edited.append(text)
        return {"ok": True}

    async def _ack(cq_id, text=""):
        return {"ok": True}

    p.send, p.edit_message_text, p.answer_callback_query = _send, _edit, _ack
    lock = _NoteLock()
    lock.log = vault / "30_Resources" / "EmptyOS" / "manager" / "log.md"
    settings = _Settings(tz)

    class _Events:
        async def emit(self, *a, **k):
            return None

    class _Apps:
        instances = {"rooms": rooms}

        async def load(self, app_id):
            return self.instances.get(app_id)

    class _Services:
        def get_optional(self, name):
            return {"vault_index": lock, "settings": settings}.get(name)

    class _Config:
        notes_path = vault

        def get(self, key, default=None):
            return default

    class _Kernel:
        events = _Events()
        apps = _Apps()
        services = _Services()
        config = _Config()
        vault_map = _VaultMap(vault / "30_Resources" / "EmptyOS" / "manager")

    p.kernel = _Kernel()
    p.note_locks = lock
    if not real_clock:
        p._local_now = lambda: WHEN
    return p


@pytest.fixture
def vault(tmp_path):
    d = tmp_path / "30_Resources" / "EmptyOS" / "manager"
    d.mkdir(parents=True)
    (d / "profile.md").write_text(PROFILE, encoding="utf-8")
    (d / "log.md").write_text(LOG_HEAD + _log_lines(3), encoding="utf-8")
    return tmp_path


def _log(vault: Path) -> str:
    return (vault / "30_Resources" / "EmptyOS" / "manager" / "log.md").read_text(encoding="utf-8")


TASK = {"id": "act-5", "app": "task", "method": "voice_add_task", "decided_as": "task.add",
        "status": "applied", "result": "{'say': 'Added: call landlord'}", "undo_id": "u1"}


class TestPluginTurn:
    def test_turn_carries_profile_and_log_as_live_context(self, tg, vault):
        rooms = _Rooms()
        p = _plugin(tg, rooms, vault)
        asyncio.run(p._handle_message({"text": "hi", "chat_id": OWNER, "message_id": 1}))
        ctx = rooms.chats[0]["context"]
        assert "chief-of-staff" in ctx and "entry 2" in ctx
        assert "created:" not in ctx

    def test_decision_token_is_logged_and_never_sent(self, tg, vault):
        rooms = _Rooms(response="Noted.\n[LOG: no pushes on Sunday]")
        p = _plugin(tg, rooms, vault)
        asyncio.run(p._handle_message({"text": "no pushes on sunday", "chat_id": OWNER}))
        assert p.sent == ["Noted."]
        assert _log(vault).endswith("entry 2\n- 2026-10-03 14:05 · phone · no pushes on Sunday\n")
        assert p.note_locks.keys == ["30_Resources/EmptyOS/manager/log.md"]
        (before, after), = p.note_locks.sizes
        assert after > before   # the append happened while the lock was held

    def test_auto_run_verb_is_logged_failed_one_is_not(self, tg, vault):
        failed = {"id": "act-6", "app": "task", "method": "voice_add_task",
                  "status": "failed", "error": "disk full"}
        rooms = _Rooms(response="Adding it.", results=[TASK, failed])
        p = _plugin(tg, rooms, vault)
        asyncio.run(p._handle_message({"text": "call landlord", "chat_id": OWNER}))
        new = _log(vault).split("entry 2\n", 1)[1]
        assert new == "- 2026-10-03 14:05 · phone · ran task.add (act-5)\n"

    def test_apply_tap_and_undo_tap_are_logged(self, tg, vault):
        rooms = _Rooms(record=dict(TASK, status="pending"))
        p = _plugin(tg, rooms, vault)
        asyncio.run(p._handle_callback({"cq_id": "c", "chat_id": OWNER, "message_id": 3,
                                        "data": "ap:act-5"}))
        asyncio.run(p._handle_callback({"cq_id": "c", "chat_id": OWNER, "message_id": 4,
                                        "data": "un:act-5"}))
        tail = _log(vault).splitlines()[-2:]
        assert tail[0].endswith("· phone · ran task.add (act-5)")
        assert tail[1].endswith("· phone · undid task.add (act-5)")

    def test_failed_apply_and_refused_undo_are_not_logged(self, tg, vault):
        before = _log(vault)

        class _Refusing(_Rooms):
            async def apply_pending(self, action_id, **kw):
                return {"error": "unknown method", "action": self._record}

            async def undo_action(self, action_id, *, max_age_s=0):
                return {"ok": False, "message": "Too old to undo.", "action": self._record}

        p = _plugin(tg, _Refusing(record=dict(TASK, status="pending")), vault)
        for data in ("ap:act-5", "un:act-5"):
            asyncio.run(p._handle_callback({"cq_id": "c", "chat_id": OWNER,
                                            "message_id": 3, "data": data}))
        assert _log(vault) == before

    def test_line_time_follows_the_settings_timezone(self, tg, vault):
        from datetime import timedelta

        # A zone that is neither this machine's nor a CI runner's (+5:30, no
        # DST), so falling back to the machine zone cannot pass by accident.
        chosen = _plugin(tg, _Rooms(), vault, tz="Asia/Kolkata", real_clock=True)
        assert chosen._local_now().utcoffset() == timedelta(hours=5, minutes=30)
        # "UTC" is the setting's unset default, so it means "not chosen".
        machine = _plugin(tg, _Rooms(), vault, tz="UTC", real_clock=True)
        assert machine._local_now().utcoffset() == datetime.now().astimezone().utcoffset()

    def test_off_by_default_because_the_notes_go_to_the_think_provider(self, tg, vault):
        before = _log(vault)
        rooms = _Rooms(response="Ok [LOG: something]", results=[TASK])
        p = _plugin(tg, rooms, vault)
        p._config = {}
        asyncio.run(p._handle_message({"text": "x", "chat_id": OWNER}))
        assert rooms.chats[0]["context"] == ""
        assert _log(vault) == before

    def test_an_absent_log_is_never_created(self, tg, vault):
        log = vault / "30_Resources" / "EmptyOS" / "manager" / "log.md"
        log.unlink()
        rooms = _Rooms(response="Ok [LOG: something]", results=[TASK])
        p = _plugin(tg, rooms, vault)
        asyncio.run(p._handle_message({"text": "x", "chat_id": OWNER}))
        assert not log.exists()
        assert p.sent[0] == "Ok"

    def test_a_log_without_trailing_newline_gets_one_first(self, tg, vault):
        log = vault / "30_Resources" / "EmptyOS" / "manager" / "log.md"
        log.write_text(LOG_HEAD + "- 2026-10-01 09:00 · computer · last", encoding="utf-8")
        p = _plugin(tg, _Rooms(response="k [LOG: next]"), vault)
        asyncio.run(p._handle_message({"text": "x", "chat_id": OWNER}))
        assert log.read_text(encoding="utf-8").endswith(
            "· computer · last\n- 2026-10-03 14:05 · phone · next\n")

    def test_switched_off_sends_no_context_and_writes_nothing(self, tg, vault):
        before = _log(vault)
        rooms = _Rooms(response="Ok [LOG: something]", results=[TASK])
        p = _plugin(tg, rooms, vault, config={"manager_memory": False})
        asyncio.run(p._handle_message({"text": "x", "chat_id": OWNER}))
        assert rooms.chats[0]["context"] == ""
        assert _log(vault) == before
        assert p.sent[0] == "Ok"   # the token is still stripped
