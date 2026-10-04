"""Unit tests for ComfyUI's /view download — the last step of a paid render.

No daemon, no GPU, no ComfyUI. The session is a fake.

Why this file exists. On 2026-07-28 two separate 121-frame Wan generations
completed successfully on the GPU and wrote valid ~1.08MB mp4s
(``eos-mv-wan_00229.mp4`` at 09:31:36 and ``eos-mv-wan_00237.mp4`` at
13:33:25). One second after each, the daemon logged "animate returned no clip
— check the ComfyUI console (likely OOM or a workflow node error)" and dropped
the scene. ``/view`` served both files in 4ms when asked again, so nothing was
wrong with the render, the file, or the endpoint: ``download_image`` had a bare
``except Exception: return False`` that erased a transient error, and the
caller's message then blamed OOM and misdirected every investigation.

So the two properties worth pinning are not the happy path:

1. **A transient failure must not discard the render.** One retry recovers it.
2. **A real failure must always say why.** Returning False silently is what
   made this cost two renders and a morning; the reason belongs in syslog.
"""

from __future__ import annotations

import asyncio
import importlib.util
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_comfyui_plugin():
    spec = importlib.util.spec_from_file_location(
        "comfyui_plugin_download_under_test",
        REPO / "plugins" / "comfyui" / "plugin.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSyslog:
    def __init__(self):
        self.lines = []

    def info(self, tag, msg):
        self.lines.append(("info", tag, msg))

    def warning(self, tag, msg):
        self.lines.append(("warning", tag, msg))

    def error(self, tag, msg):
        self.lines.append(("error", tag, msg))

    def text(self):
        return "\n".join(m for _lvl, _tag, m in self.lines)

    def levels(self):
        return [lvl for lvl, _tag, _m in self.lines]


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def read(self):
        return self._body


class ScriptedSession:
    """Replays a list of outcomes: an Exception instance raises, else responds."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_plugin(mod, session):
    plugin = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    plugin._config = {"host": "http://127.0.0.1:8188"}
    plugin._session = session
    plugin.kernel = types.SimpleNamespace(syslog=FakeSyslog())
    return plugin


@pytest.fixture(scope="module")
def mod():
    return _load_comfyui_plugin()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


# --- property 1: a transient failure must not discard the render ---------

def test_transient_error_is_recovered_by_retry(mod, tmp_path):
    """The 2026-07-28 case: first GET blows up, the file is fine, retry wins."""
    payload = b"\x00\x01" * 512
    session = ScriptedSession([
        ConnectionResetError("connection reset"),
        FakeResponse(200, payload),
    ])
    p = _make_plugin(mod, session)
    dest = tmp_path / "clip-08.mp4"

    ok = asyncio.run(p.download_image("eos-mv-wan_00237.mp4", dest))

    assert ok is True, "a completed render must survive one transient error"
    assert dest.read_bytes() == payload
    assert session.calls == 2
    assert "attempt 2" in p.kernel.syslog.text()


def test_transient_non_200_is_recovered_by_retry(mod, tmp_path):
    payload = b"video-bytes"
    session = ScriptedSession([FakeResponse(503), FakeResponse(200, payload)])
    p = _make_plugin(mod, session)
    dest = tmp_path / "clip.mp4"

    assert asyncio.run(p.download_image("x.mp4", dest)) is True
    assert dest.read_bytes() == payload


def test_happy_path_does_not_retry_or_log(mod, tmp_path):
    session = ScriptedSession([FakeResponse(200, b"ok")])
    p = _make_plugin(mod, session)
    dest = tmp_path / "clip.mp4"

    assert asyncio.run(p.download_image("x.mp4", dest)) is True
    assert session.calls == 1, "a working fetch must stay a single request"
    assert p.kernel.syslog.lines == [], "the quiet path must stay quiet"


# --- property 2: a real failure must always say why ---------------------

def test_persistent_exception_is_explained_not_swallowed(mod, tmp_path):
    session = ScriptedSession([ConnectionResetError("connection reset")])
    p = _make_plugin(mod, session)

    ok = asyncio.run(p.download_image("x.mp4", tmp_path / "c.mp4"))

    assert ok is False
    assert "error" in p.kernel.syslog.levels(), "silence is the bug being fixed"
    text = p.kernel.syslog.text()
    assert "ConnectionResetError" in text, "the reason must survive"
    assert "x.mp4" in text, "the filename must be identifiable"
    assert "OOM" in text, "must steer the reader off the wrong explanation"


def test_persistent_non_200_reports_the_status(mod, tmp_path):
    session = ScriptedSession([FakeResponse(404)])
    p = _make_plugin(mod, session)

    assert asyncio.run(p.download_image("gone.mp4", tmp_path / "c.mp4")) is False
    assert "HTTP 404" in p.kernel.syslog.text()


def test_attempts_are_bounded(mod, tmp_path):
    session = ScriptedSession([ConnectionResetError("nope")])
    p = _make_plugin(mod, session)

    asyncio.run(p.download_image("x.mp4", tmp_path / "c.mp4", attempts=2))
    assert session.calls == 2, "retry must not become an unbounded loop"


def test_empty_filename_is_still_a_cheap_no(mod, tmp_path):
    session = ScriptedSession([FakeResponse(200, b"x")])
    p = _make_plugin(mod, session)

    assert asyncio.run(p.download_image("", tmp_path / "c.mp4")) is False
    assert session.calls == 0
    assert p.kernel.syslog.lines == []


# --- the completion race that discarded three finished renders ----------

class HistorySession:
    """Replays a sequence of /history payloads, one per poll."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def post(self, *_a, **_k):
        return FakeJsonResponse({"prompt_id": "p1"})

    def get(self, *_a, **_k):
        i = min(self.calls, len(self.payloads) - 1)
        self.calls += 1
        return FakeJsonResponse(self.payloads[i])


class FakeJsonResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def json(self):
        return self.payload


DONE = {"completed": True, "status_str": "success"}
RUNNING = {"completed": False, "status_str": "running"}
OUT = {"11": {"gifs": [{"filename": "eos-mv-wan_00249.mp4", "subfolder": ""}]}}


def _poll(mod, payloads, monkeypatch):
    p = _make_plugin(mod, HistorySession(payloads))
    return asyncio.run(p._submit_and_poll(
        {}, output_keys=("videos", "gifs", "images"),
        max_polls=30, poll_interval=0.0,
    ))


def test_entry_present_but_outputs_not_attached_yet_keeps_polling(mod, monkeypatch):
    """The 2026-07-28 race: three ~5-GPU-minute Wan renders were discarded
    one second after ComfyUI reported execution_success, because the history
    entry appears a moment before its outputs do."""
    got = _poll(mod, [
        {},                                            # not in history yet
        {"p1": {"status": RUNNING, "outputs": {}}},    # present, still running
        {"p1": {"status": DONE, "outputs": {}}},       # done, outputs not attached
        {"p1": {"status": DONE, "outputs": OUT}},      # outputs land
    ], monkeypatch)
    assert got is not None
    assert got["filename"] == "eos-mv-wan_00249.mp4"


def test_finished_with_a_real_output_returns_immediately(mod, monkeypatch):
    got = _poll(mod, [{"p1": {"status": DONE, "outputs": OUT}}], monkeypatch)
    assert got["filename"] == "eos-mv-wan_00249.mp4"


def test_finished_with_a_non_matching_output_still_gives_up(mod, monkeypatch):
    """A genuinely wrong workflow must not spin the whole poll budget."""
    got = _poll(mod, [{"p1": {"status": DONE, "outputs": {"9": {"audio": [{}]}}}}],
                monkeypatch)
    assert got is None


def test_forever_empty_eventually_gives_up(mod, monkeypatch):
    """Bounded: it must not poll the full budget on a truly empty result."""
    session = HistorySession([{"p1": {"status": DONE, "outputs": {}}}])
    p = _make_plugin(mod, session)
    got = asyncio.run(p._submit_and_poll(
        {}, output_keys=("gifs",), max_polls=200, poll_interval=0.0,
    ))
    assert got is None
    assert session.calls <= 25, "give-up must be bounded, not the full budget"
    assert "error" in p.kernel.syslog.levels()
