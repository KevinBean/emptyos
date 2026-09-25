"""Telegram file-send wiring — the guard is actually on every send path.

`tests/test_unit_telegram_outgoing.py` proves the guard refuses what it should.
That proves nothing about whether the plugin CALLS it: a send method that forgot
the guard would leave every one of those tests green while shipping any file on
disk. So these drive the real `send_photo` / `send_video` / `send_document` with
a fake HTTP session and assert two things per refusal — an error came back, and
**no request was made at all**.

`send_photo` predates the guard and was unguarded; it is covered here because
adding a guarded sibling while leaving an unguarded door open would be security
theatre.

Three properties a hostile review found unpinned in the first version:

- **the roots are output directories, not the whole vault** — a note under the
  vault root must be refused, or the guard is a filter over everything the user
  owns;
- **`file_roots` config is actually read** — the first version stubbed `config`
  to always return the default, so deleting the operator-extension lines kept
  every test green;
- **the token check runs before the file is opened** — asserting only "no request
  was made" passes for an implementation that opens (and leaks) the file first.

No daemon, no network — the session is a stub. Run:
    python -m pytest tests/test_unit_telegram_file_send.py -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def plugin_mod():
    """Import plugins/telegram/plugin.py with its package-relative imports intact."""
    pkg = types.ModuleType("tg_pkg_under_test")
    pkg.__path__ = [str(REPO / "plugins" / "telegram")]
    sys.modules["tg_pkg_under_test"] = pkg
    for sub in ("bridge", "outgoing"):
        spec = importlib.util.spec_from_file_location(
            f"tg_pkg_under_test.{sub}", REPO / "plugins" / "telegram" / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "tg_pkg_under_test.plugin", REPO / "plugins" / "telegram" / "plugin.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class FakeSession:
    """Records posts instead of making them. `calls` empty == nothing left the box."""

    def __init__(self):
        self.calls: list[dict] = []

    def post(self, url, data=None, json=None, timeout=None):
        fields = []
        for f in getattr(data, "_fields", []):
            try:
                fields.append(f[0].get("name"))
            except Exception:
                pass
        self.calls.append({"url": url, "fields": fields, "data": data})
        return FakeResponse({"ok": True, "result": {"message_id": 1}})


@pytest.fixture
def tg(plugin_mod, tmp_path):
    """A plugin whose vault has both an output subtree and ordinary notes."""
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    media = vault / "70_Media" / "clips"
    media.mkdir(parents=True)
    (vault / "50_Journal").mkdir(parents=True)
    data.mkdir()
    (media / "take.mp4").write_bytes(b"\x00\x00fake mp4")
    (media / "shot.png").write_bytes(b"\x89PNG\x00fake")
    (media / "notes.md").write_text("nothing secret here", encoding="utf-8")
    (media / "leaky.md").write_text(
        "key sk-ant-" + "a" * 40, encoding="utf-8")  # check-secrets: ignore
    (vault / "50_Journal" / "private.md").write_text("a private note", encoding="utf-8")
    (data / "secrets").mkdir()
    (data / "secrets" / "token.json").write_text("{}", encoding="utf-8")

    kernel = types.SimpleNamespace(
        config=types.SimpleNamespace(notes_path=vault, data_dir=data))
    p = plugin_mod.TelegramPlugin.__new__(plugin_mod.TelegramPlugin)
    p.kernel = kernel
    p.manifest = {}
    p._config = {}
    p._token = "test-token"
    p._chat_id = "12345"
    p._session = FakeSession()
    # The plugin is built with __new__ (a real __init__ needs a kernel), so any
    # attribute __init__ would have set has to be set here. Missing
    # `_proactive_lock` raised AttributeError inside _proactive_record's
    # `except Exception`, which silently skipped every delivery record.
    p._proactive_lock = None
    p.config = lambda key, default=None: p._config.get(key, default)
    return p, vault, data


def run(coro):
    return asyncio.run(coro)


# --- the good paths still work --------------------------------------------

def test_send_video_posts_to_sendVideo(tg):
    p, vault, _ = tg
    out = run(p.send_video(str(vault / "70_Media" / "clips" / "take.mp4"), caption="a clip"))
    assert out.get("ok") is True
    assert len(p._session.calls) == 1
    call = p._session.calls[0]
    assert call["url"].endswith("/sendVideo")
    assert "video" in call["fields"] and "supports_streaming" in call["fields"]


def test_send_document_posts_to_sendDocument(tg):
    p, vault, _ = tg
    out = run(p.send_document(str(vault / "70_Media" / "clips" / "notes.md")))
    assert out.get("ok") is True
    assert p._session.calls[0]["url"].endswith("/sendDocument")


def test_send_photo_still_works(tg):
    p, vault, _ = tg
    out = run(p.send_photo(str(vault / "70_Media" / "clips" / "shot.png")))
    assert out.get("ok") is True
    assert p._session.calls[0]["url"].endswith("/sendPhoto")


def test_data_dir_is_a_root(tg):
    p, _, data = tg
    (data / "report.pdf").write_bytes(b"%PDF-1.4 \x00x")
    out = run(p.send_document(str(data / "report.pdf")))
    assert out.get("ok") is True


# --- the roots are output directories, not the whole vault -----------------

def test_an_ordinary_vault_note_is_not_sendable(tg):
    """The narrowing the user asked for: generated output, not every note."""
    p, vault, _ = tg
    out = run(p.send_document(str(vault / "50_Journal" / "private.md")))
    assert "outside the allowed roots" in out.get("error", "")
    assert p._session.calls == []


def test_file_roots_config_extends_the_roots(tg, tmp_path):
    """Pins that `[plugins.telegram] file_roots` is actually read — deleting
    that loop left every other test green."""
    p, _, _ = tg
    extra = tmp_path / "extra-out"
    extra.mkdir()
    (extra / "clip.mp4").write_bytes(b"\x00\x00x")
    assert "outside the allowed roots" in run(p.send_video(str(extra / "clip.mp4"))).get("error", "")
    p._config["file_roots"] = [str(extra)]
    assert run(p.send_video(str(extra / "clip.mp4"))).get("ok") is True


def test_max_file_mb_config_is_read(tg):
    p, vault, _ = tg
    p._config["max_file_mb"] = 0.000001  # 1 byte, once multiplied out
    out = run(p.send_video(str(vault / "70_Media" / "clips" / "take.mp4")))
    assert "over the" in out.get("error", "")
    assert p._session.calls == []


def test_a_vaultless_daemon_still_has_data_as_a_root(tg, plugin_mod, tmp_path):
    """`Config.notes_path` can be None; the roots must degrade, not crash."""
    p, _, data = tg
    p.kernel.config.notes_path = None
    (data / "clip.mp4").write_bytes(b"\x00\x00x")
    assert run(p.send_video(str(data / "clip.mp4"))).get("ok") is True


# --- refusals: an error AND no request -------------------------------------

@pytest.mark.parametrize("method,arg", [
    ("send_video", "outside.mp4"),
    ("send_document", "outside.md"),
    ("send_photo", "outside.png"),
])
def test_file_outside_roots_is_refused_on_every_method(tg, tmp_path, method, arg):
    p, _, _ = tg
    outside = tmp_path / arg
    outside.write_bytes(b"\x00x")
    out = run(getattr(p, method)(str(outside)))
    assert "refused" in out.get("error", "")
    assert p._session.calls == [], "a refused send must make no request"


def test_secrets_file_is_refused(tg):
    p, _, data = tg
    out = run(p.send_document(str(data / "secrets" / "token.json")))
    assert "refused" in out.get("error", "")
    assert p._session.calls == []


def test_secret_bearing_note_is_refused(tg):
    p, vault, _ = tg
    out = run(p.send_document(str(vault / "70_Media" / "clips" / "leaky.md")))
    assert "Anthropic API key" in out.get("error", "")
    assert p._session.calls == []


def test_caption_with_a_secret_is_refused(tg):
    p, vault, _ = tg
    out = run(p.send_video(
        str(vault / "70_Media" / "clips" / "take.mp4"),
        caption="token sk-ant-" + "a" * 40))  # check-secrets: ignore
    assert "caption flagged" in out.get("error", "")
    assert p._session.calls == []


def test_photo_method_refuses_a_video(tg):
    p, vault, _ = tg
    out = run(p.send_photo(str(vault / "70_Media" / "clips" / "take.mp4")))
    assert "not allowed for photo" in out.get("error", "")
    assert p._session.calls == []


def test_no_token_refuses_before_the_guard_runs(tg, tmp_path):
    """Ordering, measured: with no token, a path the guard would refuse must
    still come back as the TOKEN error — otherwise the check moved."""
    p, _, _ = tg
    p._token = ""
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"\x00x")
    out = run(p.send_video(str(outside)))
    assert out.get("error") == "no chat_id or token"
    assert p._session.calls == []


# --- the proactive gate ----------------------------------------------------

def _enable_proactive(data_root, **over):
    from emptyos.sdk import proactive

    policy = proactive.load_policy(data_root)
    policy.update({"enabled": True})
    policy.update(over)
    proactive.save_policy(data_root, policy)
    return policy


def test_quiet_hours_hold_a_file_push(tg):
    """A file is a phone notification like any other — one app looping over 40
    clips must not push 40 files at 03:00."""
    p, vault, data = tg
    _enable_proactive(data, quiet_start="00:00", quiet_end="23:59")
    out = run(p.send_video(str(vault / "70_Media" / "clips" / "take.mp4")))
    assert "proactive gate" in out.get("error", "")
    assert p._session.calls == [], "a held push must make no request"


def test_the_same_file_twice_is_deduped(tg):
    p, vault, data = tg
    _enable_proactive(data, quiet_start="", quiet_end="", min_gap_sec=0)
    clip = str(vault / "70_Media" / "clips" / "take.mp4")
    assert run(p.send_video(clip)).get("ok") is True
    second = run(p.send_video(clip))
    assert "proactive gate" in second.get("error", "")
    assert len(p._session.calls) == 1


def test_gate_disabled_sends_as_before(tg):
    """The gate ships off by default; that path must stay byte-for-byte."""
    p, vault, _ = tg
    out = run(p.send_video(str(vault / "70_Media" / "clips" / "take.mp4")))
    assert out.get("ok") is True and len(p._session.calls) == 1


def test_two_concurrent_sends_are_both_recorded(tg):
    """Each send carries its own record token. With one shared slot on the
    plugin, the second send overwrote the first and the first delivery was
    never counted — both files arrived, the counter moved once."""
    p, vault, data = tg
    _enable_proactive(data, quiet_start="", quiet_end="", min_gap_sec=0, daily_cap=100)
    clips = vault / "70_Media" / "clips"
    (clips / "a.mp4").write_bytes(b"\x00\x00a")
    (clips / "b.mp4").write_bytes(b"\x00\x00bb")

    async def both():
        return await asyncio.gather(
            p.send_video(str(clips / "a.mp4")), p.send_video(str(clips / "b.mp4")))

    results = run(both())
    assert all(r.get("ok") for r in results), results
    from emptyos.sdk import proactive

    day = proactive.load_state(data).get("day", {})
    assert day.get("kinds", {}).get("file") == 2, (
        f"both deliveries must be counted, got {day}")
