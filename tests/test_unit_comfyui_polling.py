"""Unit tests for live-aware ComfyUI polling.

Slow video diffusion may outlast the nominal polling window. The caller must
continue only while the exact prompt remains in ComfyUI's running/pending
queue, rather than submitting a duplicate render or waiting blindly forever.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import types
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "comfyui" / "plugin.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_poll_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, histories, queue):
        self.histories = list(histories)
        self.queue = queue
        self.history_calls = 0
        self.queue_calls = 0

    def post(self, *_args, **_kwargs):
        return _Response({"prompt_id": "prompt-1"})

    def get(self, url, **_kwargs):
        if url.endswith("/queue"):
            self.queue_calls += 1
            return _Response(self.queue)
        self.history_calls += 1
        payload = self.histories.pop(0) if self.histories else {}
        return _Response(payload)


class _UploadSession:
    def __init__(self):
        self.stream = None

    def post(self, _url, *, data, **_kwargs):
        self.stream = data._fields[0][2]
        return _Response({"name": "uploaded.png"})


class _Syslog:
    """Stand-in for `emptyos.kernel.syslog.SystemLog`.

    Carries every level the real one exposes — `debug`/`info`/`warn`/`warning`/
    `error` — deliberately, rather than only the two this test happened to need.
    A partial double turns "the code logged at a new level" into an
    `AttributeError` inside the branch under test, which reads as a logic failure
    and is not one: that is exactly how the give-up path broke when it gained a
    `syslog.error` call, on a plugin whose production code was correct all along.
    """

    def __init__(self):
        self.lines = []

    def _record(self, source, message, **_kwargs):
        self.lines.append((source, message))

    debug = info = warn = warning = error = _record


def _plugin(mod, session):
    plugin = mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)
    plugin._session = session
    plugin._host = lambda: "http://comfy.test"
    plugin.kernel = types.SimpleNamespace(syslog=_Syslog())
    return plugin


def test_slow_live_prompt_continues_past_nominal_window(mod):
    item = {"filename": "done.mp4", "type": "output"}
    session = _Session(
        histories=[
            {},
            {},
            {"prompt-1": {"outputs": {"11": {"gifs": [item]}}}},
        ],
        queue={"queue_running": [[1, "prompt-1"]], "queue_pending": []},
    )
    plugin = _plugin(mod, session)

    result = asyncio.run(plugin._submit_and_poll(
        {"1": {}},
        output_keys=("gifs",),
        max_polls=1,
        live_grace_polls=3,
        poll_interval=0,
    ))

    assert result == item
    assert session.history_calls == 3
    assert session.queue_calls == 1
    assert "continuing while ComfyUI reports it live" in (
        plugin.kernel.syslog.lines[0][1]
    )


def test_dead_prompt_stops_at_nominal_window(mod):
    session = _Session(
        histories=[{}, {"prompt-1": {"outputs": {}}}],
        queue={"queue_running": [], "queue_pending": []},
    )
    plugin = _plugin(mod, session)

    result = asyncio.run(plugin._submit_and_poll(
        {"1": {}},
        output_keys=("gifs",),
        max_polls=1,
        live_grace_polls=3,
        poll_interval=0,
    ))

    assert result is None
    assert session.history_calls == 1
    assert session.queue_calls == 1


def test_upload_closes_caller_owned_stream_before_return(mod, tmp_path):
    image = tmp_path / "handoff.png"
    image.write_bytes(b"png")
    session = _UploadSession()
    plugin = _plugin(mod, session)

    result = asyncio.run(plugin.upload_image(image, "uploaded.png"))

    assert result == "uploaded.png"
    assert session.stream is not None
    assert session.stream.closed is True
    # This is the operation that raised WinError 32 in chained MV rendering.
    image.unlink()
    assert not image.exists()


def test_animate_provider_persists_exact_api_workflow_snapshot(
    mod, tmp_path, monkeypatch,
):
    template = tmp_path / "template.json"
    template.write_text(
        """{
          "_comment": "not submitted",
          "1": {
            "class_type": "Sampler",
            "inputs": {
              "text": "{prompt}",
              "seed": "{seed}",
              "length": "{frames}",
              "width": "{width}",
              "height": "{height}"
            }
          }
        }""",
        encoding="utf-8",
    )
    snapshot = tmp_path / "scene-01.workflow-api.json"
    plugin = _plugin(mod, object())
    plugin.config = lambda _key, default="": str(template)
    plugin._preflight = lambda _kind: asyncio.sleep(0)
    plugin._submit_and_poll = lambda *_args, **_kwargs: asyncio.sleep(
        0, result={"filename": "done.mp4", "type": "output"},
    )
    monkeypatch.setattr(plugin, "_item_path", lambda item: item["filename"])

    result = asyncio.run(plugin.generate_from_workflow(
        workflow_key="video",
        prompt="mist moves",
        image_filename="still.png",
        num_frames=49,
        seed=123,
        width=1024,
        height=576,
        workflow_snapshot_path=str(snapshot),
    ))

    payload = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    assert result == "done.mp4"
    assert "_comment" not in payload
    assert payload["1"]["inputs"] == {
        "text": "mist moves",
        "seed": 123,
        "length": 49,
        "width": 1024,
        "height": 576,
    }


def test_workflow_substitutions_keep_whole_value_types(
    mod, tmp_path, monkeypatch,
):
    template = tmp_path / "audio.json"
    template.write_text(
        """{
          "1": {
            "class_type": "AudioNode",
            "inputs": {
              "tags": "{style}, {prompt}",
              "duration": "{duration}",
              "bpm": "{bpm}"
            }
          }
        }""",
        encoding="utf-8",
    )
    submitted = {}
    plugin = _plugin(mod, object())
    plugin.config = lambda _key, default="": str(template)
    plugin._preflight = lambda kind: asyncio.sleep(
        0, result=submitted.setdefault("preflight", kind),
    )

    async def submit(workflow, **kwargs):
        submitted["workflow"] = workflow
        submitted["kwargs"] = kwargs
        return {"filename": "track.flac", "type": "output"}

    plugin._submit_and_poll = submit
    monkeypatch.setattr(plugin, "_item_path", lambda item: item["filename"])

    result = asyncio.run(plugin.generate_from_workflow(
        workflow_key="music",
        prompt="mist over water",
        seed=123,
        substitutions={"style": "ambient", "duration": 30.0, "bpm": 86},
        output_keys=("audio", "audios"),
        preflight_kind="audio",
    ))

    assert result == "track.flac"
    assert submitted["preflight"] == "audio"
    assert submitted["kwargs"]["output_keys"] == ("audio", "audios")
    assert submitted["workflow"]["1"]["inputs"] == {
        "tags": "ambient, mist over water",
        "duration": 30.0,
        "bpm": 86,
    }


def test_generate_music_downloads_audio_to_requested_destination(
    mod, tmp_path,
):
    plugin = _plugin(mod, object())
    calls = {}

    async def generate_from_workflow(**kwargs):
        calls["workflow"] = kwargs
        return "eos-compose/track.flac"

    async def download(filename, destination):
        calls["download"] = (filename, destination)
        Path(destination).write_bytes(b"fLaC")
        return True

    plugin.generate_from_workflow = generate_from_workflow
    plugin.download_image = download
    destination = tmp_path / "track.flac"

    result = asyncio.run(plugin.generate_music(
        "dreamy instrumental",
        style="ambient",
        duration=30,
        language="zh",
        bpm=86,
        keyscale="C major",
        timesignature="3",
        seed=123,
        dest=str(destination),
    ))

    assert result == str(destination)
    assert destination.read_bytes() == b"fLaC"
    assert calls["workflow"]["preflight_kind"] == "audio"
    assert calls["workflow"]["output_keys"] == ("audio", "audios")
    assert calls["workflow"]["substitutions"] == {
        "style": "ambient",
        "duration": 30.0,
        "lyrics": "",
        "language": "zh",
        "bpm": 86,
        "keyscale": "C major",
        "timesignature": "3",
        "steps": 40,
    }


# --- ACE-Step key / meter (the 2026-08-15 fix) -------------------------------
# Until 2026-08-15 the shipped workflow pinned `timesignature: "4"` and
# `keyscale: "E minor"`, so every track EmptyOS composed came out in E minor.


def test_steps_are_reachable_and_bounded(mod):
    """steps was pinned at 8 until 2026-08-15 on a theory the ear disproved.

    40 is audibly better and costs +2.5s on a 60s song, so the caller must be
    able to reach it. Guard the upper end so a typo cannot queue a huge render.
    """
    plugin = _plugin(mod, object())
    calls = {}

    async def generate_from_workflow(**kwargs):
        calls.update(kwargs)
        return ""

    plugin.generate_from_workflow = generate_from_workflow
    asyncio.run(plugin.generate_music("x", seed=1))
    assert calls["substitutions"]["steps"] == mod.ACESTEP_DEFAULT_STEPS == 40

    asyncio.run(plugin.generate_music("x", steps=80, seed=1))
    assert calls["substitutions"]["steps"] == 80

    with pytest.raises(ValueError, match="steps"):
        asyncio.run(plugin.generate_music("x", steps=0))
    with pytest.raises(ValueError, match="steps"):
        asyncio.run(plugin.generate_music("x", steps=mod.ACESTEP_MAX_STEPS + 1))


def test_shipped_music_workflow_has_no_hardcoded_key_or_meter():
    """The regression lock.

    Every other test here builds a synthetic template, so all of them would
    stay green if someone pasted the literals back into the real file. This
    one reads the shipped workflow.
    """
    wf = json.loads(
        (PLUGIN.parent / "workflows" / "acestep15_audio.json")
        .read_text(encoding="utf-8")
    )
    node5 = wf["5"]["inputs"]
    assert node5["keyscale"] == "{keyscale}"
    assert node5["timesignature"] == "{timesignature}"
    assert wf["8"]["inputs"]["steps"] == "{steps}"


def test_auto_keyscale_is_seed_deterministic_and_spreads(mod):
    """An empty keyscale draws from the seed — reproducible, but not all one key."""
    assert mod._resolve_keyscale("", 4242) == mod._resolve_keyscale("", 4242)
    keys = {mod._resolve_keyscale("", s) for s in range(64)}
    assert len(keys) >= 8
    assert keys != {"E minor"}          # the exact pre-fix behaviour
    assert keys <= set(mod.ACESTEP_KEYSCALES)


def test_keyscale_is_case_normalised(mod):
    assert mod._resolve_keyscale("c MAJOR", 1) == "C major"
    assert mod._resolve_keyscale("  f# minor  ", 1) == "F# minor"


def test_invalid_key_or_meter_raises_before_queueing(mod):
    """A typo must cost a readable error, not a silent GPU burn.

    ComfyUI rejects an out-of-list COMBO value for the WHOLE prompt and that
    reaches us only as an opaque "no prompt_id" line, so the domain has to be
    enforced before anything is submitted.
    """
    plugin = _plugin(mod, object())

    async def tripwire(**kwargs):
        raise AssertionError("generate_from_workflow must not be reached")

    plugin.generate_from_workflow = tripwire

    with pytest.raises(ValueError, match="keyscale"):
        asyncio.run(plugin.generate_music("x", keyscale="H minor"))
    with pytest.raises(ValueError, match="timesignature"):
        asyncio.run(plugin.generate_music("x", timesignature="5"))


def test_timesignature_substitutes_as_string_not_int(mod):
    """ComfyUI COMBO members are strings: 4 != "4" under `val not in options`."""
    plugin = _plugin(mod, object())
    calls = {}

    async def generate_from_workflow(**kwargs):
        calls.update(kwargs)
        return ""

    plugin.generate_from_workflow = generate_from_workflow
    asyncio.run(plugin.generate_music("x", timesignature=3, seed=7))
    assert calls["substitutions"]["timesignature"] == "3"
    assert isinstance(calls["substitutions"]["timesignature"], str)
