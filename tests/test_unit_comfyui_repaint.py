"""Unit tests for ACE-Step section repaint (audio-to-audio).

Guards the traps that are invisible in review and expensive at runtime: a
repaint run loads 7.6 GB of weights before it can fail, so every one of these
would otherwise be discovered minutes in, on the GPU.

The template invariants are pinned here rather than left to the JSON comment
because each one has a silent failure mode:
  * param_seed 0 survives into a pipeline that has no `seed` argument -> the
    run dies with a TypeError AFTER loading the models;
  * a missing repaint_variance placeholder inherits the pack's 0.01 default,
    which denoises the last 1% of steps and returns the take unchanged -- a
    repaint that appears to succeed and did nothing.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "comfyui" / "plugin.py"
WORKFLOW = ROOT / "plugins" / "comfyui" / "workflows" / "acestep_repaint.json"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_repaint_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- template


def test_workflow_wires_the_repaint_node(workflow):
    nodes = {k: v for k, v in workflow.items() if not k.startswith("_")}
    by_class = {n["class_type"]: n for n in nodes.values()}
    for cls in (
        "ACEModelLoader", "LoadAudio", "MultiLinePromptACES",
        "MultiLineLyrics", "GenerationParameters", "ACEStepRepainting",
    ):
        assert cls in by_class, f"{cls} missing from the repaint graph"

    # The whole point of the workflow: it takes audio in.
    assert by_class["LoadAudio"]["inputs"]["audio"] == "{src_audio}"

    rp = by_class["ACEStepRepainting"]["inputs"]
    for field in ("models", "src_audio", "prompt", "lyrics", "parameters"):
        assert isinstance(rp[field], list), f"{field} must be a node reference"
    for field in ("repaint_start", "repaint_end", "repaint_variance", "seed"):
        assert rp[field] == "{" + field + "}", f"{field} must stay templated"


def test_generation_parameters_seed_is_templated_and_separate(workflow):
    """param_seed, not seed. GenerationParameters only converts a NONZERO seed
    into manual_seeds; a literal 0 reaches pipeline.__call__, which has no seed
    parameter, and the run dies after the models are already resident."""
    gp = workflow["5"]["inputs"]
    assert gp["seed"] == "{param_seed}"
    assert gp["infer_step"] == "{steps}"


def test_audio_duration_is_not_templated(workflow):
    """ACEStepRepainting overwrites parameters['audio_duration'] from the source
    file, so templating it would expose a knob that can never take effect."""
    assert isinstance(workflow["5"]["inputs"]["audio_duration"], (int, float))


# ---------------------------------------------------------------- defaults


def test_default_variance_is_not_the_packs_no_op(mod):
    """0.01 denoises the last 1% of steps; measured separation from the kept
    region was -0.013, i.e. indistinguishable from not repainting at all."""
    assert mod.ACESTEP_REPAINT_DEFAULT_VARIANCE > 0.1
    assert 0.01 <= mod.ACESTEP_REPAINT_DEFAULT_VARIANCE <= 1.0


def test_default_variance_holds_the_vocal_line(mod):
    """The ceiling is set by vocals, not by the music. Measured word-similarity
    of the repainted window against the source: 0.778 at variance 0.30, 0.235
    at 0.50 (the verse restarts). Every spectral metric rates 0.50 healthy, so
    this bound exists only because it was listened to and transcribed."""
    assert mod.ACESTEP_REPAINT_DEFAULT_VARIANCE <= mod.ACESTEP_REPAINT_LYRIC_SAFE_VARIANCE


def test_repaint_steps_default_is_not_the_turbo_value(mod):
    """Repaint runs ACE-Step 1.0, which is not distilled; 40 is tuned for the
    1.5 turbo checkpoint that compose uses."""
    assert mod.ACESTEP_REPAINT_DEFAULT_STEPS == 60


# ---------------------------------------------------------------- validation


class _Syslog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _Kernel:
    def __init__(self):
        # Per-instance, not a class attribute: the warning tests replace
        # .warning, and a shared singleton would carry that into later tests.
        self.syslog = _Syslog()


def _stub(mod, tmp_path):
    """A plugin instance with the network edges replaced, so the argument
    contract can be exercised without ComfyUI or a kernel."""
    self = object.__new__(mod.ComfyUIPlugin)
    self.kernel = _Kernel()
    self.calls = []
    self.config = lambda key, default=None: default

    async def _upload(src, name=""):
        return "uploaded.flac"

    async def _gen(**kwargs):
        self.calls.append(kwargs)
        return "eos-repaint/2026-08/repaint_00001.flac"

    self.upload_image = _upload
    self.generate_from_workflow = _gen
    return self


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "take.flac"
    p.write_bytes(b"not really audio")
    return p


def test_missing_source_raises(mod, tmp_path):
    self = _stub(mod, tmp_path)
    with pytest.raises(FileNotFoundError):
        asyncio.run(mod.ComfyUIPlugin.repaint_music(
            self, str(tmp_path / "nope.flac"), repaint_start=0, repaint_end=10,
            prompt="p", lyrics="l"))


@pytest.mark.parametrize("start,end", [(10, 10), (20, 5), (-1, 10)])
def test_bad_window_raises(mod, tmp_path, src, start, end):
    self = _stub(mod, tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(mod.ComfyUIPlugin.repaint_music(
            self, str(src), repaint_start=start, repaint_end=end,
            prompt="p", lyrics="l"))


@pytest.mark.parametrize("variance", [0.0, 1.5, -0.2])
def test_bad_variance_raises(mod, tmp_path, src, variance):
    self = _stub(mod, tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(mod.ComfyUIPlugin.repaint_music(
            self, str(src), repaint_start=0, repaint_end=10, variance=variance,
            prompt="p", lyrics="l"))


def test_zero_seed_resolves_nonzero_in_both_slots(mod, tmp_path, src):
    """seed=0 means 'pick one', and it must reach GenerationParameters as a
    nonzero param_seed — the TypeError trap."""
    self = _stub(mod, tmp_path)
    asyncio.run(mod.ComfyUIPlugin.repaint_music(
        self, str(src), repaint_start=5, repaint_end=15, seed=0,
        prompt="p", lyrics="l"))
    subs = self.calls[0]["substitutions"]
    assert subs["param_seed"] != 0
    assert subs["param_seed"] == self.calls[0]["seed"]


def test_window_is_passed_as_whole_seconds(mod, tmp_path, src):
    """repaint_start/end are INT inputs on the node; the pipeline converts them
    at 44100/512/8 latent frames per second."""
    self = _stub(mod, tmp_path)
    asyncio.run(mod.ComfyUIPlugin.repaint_music(
        self, str(src), repaint_start=20.7, repaint_end=40.2, seed=7,
        prompt="p", lyrics="l"))
    subs = self.calls[0]["substitutions"]
    assert isinstance(subs["repaint_start"], int) and subs["repaint_start"] == 20
    assert isinstance(subs["repaint_end"], int) and subs["repaint_end"] == 40
    assert subs["src_audio"] == "uploaded.flac"
    assert self.calls[0]["output_keys"] == ("audio", "audios")
    assert self.calls[0]["preflight_kind"] == "audio"


def test_failed_upload_returns_empty_without_queueing(mod, tmp_path, src):
    """A render must not be queued against a source that never arrived."""
    self = _stub(mod, tmp_path)

    async def _no_upload(s, name=""):
        return ""

    self.upload_image = _no_upload
    out = asyncio.run(mod.ComfyUIPlugin.repaint_music(
        self, str(src), repaint_start=0, repaint_end=10,
        prompt="p", lyrics="l"))
    assert out == ""
    assert self.calls == []


def test_lyrics_and_prompt_have_no_default(mod):
    """An empty `lyrics` is not neutral -- the model invents vocals over the
    window (0.149 word-similarity, measured). Both are required so a caller
    cannot reach that state by omission; passing "" stays possible, but only
    as a deliberate act for an instrumental section."""
    import inspect
    sig = inspect.signature(mod.ComfyUIPlugin.repaint_music)
    for name in ("prompt", "lyrics"):
        assert sig.parameters[name].default is inspect.Parameter.empty, (
            f"{name} must not have a default"
        )


def test_high_variance_with_lyrics_warns(mod, tmp_path, src):
    """The alignment break is invisible to every spectral check, so it has to
    be said out loud at call time."""
    self = _stub(mod, tmp_path)
    warned = []
    self.kernel.syslog.warning = lambda *a, **k: warned.append(a)
    asyncio.run(mod.ComfyUIPlugin.repaint_music(
        self, str(src), repaint_start=0, repaint_end=10,
        prompt="p", lyrics="real words here", variance=0.9, seed=3))
    assert warned, "no warning for a lyric-bearing repaint above the safe band"


def test_high_variance_without_lyrics_is_silent(mod, tmp_path, src):
    """An instrumental window legitimately goes high; warning there is noise."""
    self = _stub(mod, tmp_path)
    warned = []
    self.kernel.syslog.warning = lambda *a, **k: warned.append(a)
    asyncio.run(mod.ComfyUIPlugin.repaint_music(
        self, str(src), repaint_start=0, repaint_end=10,
        prompt="p", lyrics="", variance=0.9, seed=3))
    assert not warned
