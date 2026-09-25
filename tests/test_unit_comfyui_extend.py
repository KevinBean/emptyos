"""Unit tests for ACE-Step extend (intro / outro).

Sibling of test_unit_comfyui_repaint.py. The template invariants are the same
family of silent, expensive failures — an extend run loads 7.6 GB of weights
before it can discover any of them — plus two that are specific to this verb:

  * extend has NO variance input, so a `{repaint_variance}` placeholder copied
    from the repaint sibling would bind to nothing and be silently ignored;
  * an extend of 0 + 0 seconds is not a no-op, it is a full re-render of the
    take that grows nothing, which is the worst outcome per GPU-minute.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "comfyui" / "plugin.py"
WORKFLOW = ROOT / "plugins" / "comfyui" / "workflows" / "acestep_extend.json"
REPAINT_WORKFLOW = ROOT / "plugins" / "comfyui" / "workflows" / "acestep_repaint.json"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_extend_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- template


def test_workflow_wires_the_extend_node(workflow):
    nodes = {k: v for k, v in workflow.items() if not k.startswith("_")}
    by_class = {n["class_type"]: n for n in nodes.values()}
    for cls in (
        "ACEModelLoader", "LoadAudio", "MultiLinePromptACES",
        "MultiLineLyrics", "GenerationParameters", "ACEStepExtend",
    ):
        assert cls in by_class, f"{cls} missing from the extend graph"

    assert by_class["LoadAudio"]["inputs"]["audio"] == "{src_audio}"

    ex = by_class["ACEStepExtend"]["inputs"]
    for field in ("models", "src_audio", "prompt", "lyrics", "parameters"):
        assert isinstance(ex[field], list), f"{field} must be a node reference"
    for field in ("left_extend_length", "right_extend_length", "seed"):
        assert ex[field] == "{" + field + "}", f"{field} must stay templated"


def test_extend_has_no_variance_placeholder(workflow):
    """ACEStepExtend exposes no variance input. A placeholder copied from the
    repaint sibling would substitute into nothing and read as a working dial.

    Checks the node inputs, not the raw file: the _comment names the field on
    purpose, to say it must not be added."""
    for nid, node in workflow.items():
        if nid.startswith("_"):
            continue
        for key, value in node.get("inputs", {}).items():
            assert "variance" not in key, f"node {nid} has a variance input"
            assert not (isinstance(value, str) and "variance" in value), (
                f"node {nid}.{key} substitutes a variance placeholder"
            )


def test_generation_parameters_seed_is_templated(workflow):
    """Same TypeError trap as repaint: GenerationParameters only converts a
    NONZERO seed into manual_seeds, and a literal 0 reaches a pipeline with no
    seed argument, after the models are already resident."""
    assert workflow["5"]["inputs"]["seed"] == "{param_seed}"


def test_audio_duration_is_not_templated(workflow):
    """Overwritten from the source file by the node, so templating it would
    expose a knob that can never take effect."""
    assert isinstance(workflow["5"]["inputs"]["audio_duration"], (int, float))


def test_shares_the_repaint_graph_apart_from_the_verb(workflow):
    """The two graphs are deliberately the same shape. If they drift, the
    reason should be a real one and not an editing accident."""
    repaint = json.loads(REPAINT_WORKFLOW.read_text(encoding="utf-8"))
    shared = ("1", "2", "3", "4", "5")
    for nid in shared:
        assert workflow[nid]["class_type"] == repaint[nid]["class_type"], (
            f"node {nid} diverged from the repaint sibling"
        )


# ---------------------------------------------------------------- validation


class _Syslog:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _Kernel:
    def __init__(self):
        self.syslog = _Syslog()


def _stub(mod):
    self = object.__new__(mod.ComfyUIPlugin)
    self.kernel = _Kernel()
    self.calls = []
    self.config = lambda key, default=None: default

    async def _upload(src, name=""):
        return "uploaded.flac"

    async def _gen(**kwargs):
        self.calls.append(kwargs)
        return "eos-extend/2026-08/extend_00001.flac"

    self.upload_image = _upload
    self.generate_from_workflow = _gen
    return self


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "take.flac"
    p.write_bytes(b"not really audio")
    return p


def test_missing_source_raises(mod, tmp_path):
    self = _stub(mod)
    with pytest.raises(FileNotFoundError):
        asyncio.run(mod.ComfyUIPlugin.extend_music(
            self, str(tmp_path / "nope.flac"), prompt="p", lyrics="l",
            right_seconds=10))


def test_zero_extend_raises(mod, src):
    """0 + 0 would re-render the whole take and grow nothing — a full GPU run
    for no change."""
    self = _stub(mod)
    with pytest.raises(ValueError):
        asyncio.run(mod.ComfyUIPlugin.extend_music(
            self, str(src), prompt="p", lyrics="l"))


@pytest.mark.parametrize("left,right", [(-1, 10), (10, -5), (0, 1001), (1001, 0)])
def test_bad_lengths_raise(mod, src, left, right):
    self = _stub(mod)
    with pytest.raises(ValueError):
        asyncio.run(mod.ComfyUIPlugin.extend_music(
            self, str(src), prompt="p", lyrics="l",
            left_seconds=left, right_seconds=right))


def test_lyrics_and_prompt_have_no_default(mod):
    """Same contract as repaint — an empty lyrics makes the model invent
    vocals, so it must be reached deliberately rather than by omission."""
    import inspect
    sig = inspect.signature(mod.ComfyUIPlugin.extend_music)
    for name in ("prompt", "lyrics"):
        assert sig.parameters[name].default is inspect.Parameter.empty


def test_no_variance_argument(mod):
    """The node has none. Accepting one would imply a dial that does nothing."""
    import inspect
    sig = inspect.signature(mod.ComfyUIPlugin.extend_music)
    assert "variance" not in sig.parameters


def test_lengths_passed_as_whole_seconds(mod, src):
    self = _stub(mod)
    asyncio.run(mod.ComfyUIPlugin.extend_music(
        self, str(src), prompt="p", lyrics="l",
        left_seconds=4.8, right_seconds=12.2, seed=9))
    subs = self.calls[0]["substitutions"]
    assert isinstance(subs["left_extend_length"], int) and subs["left_extend_length"] == 4
    assert isinstance(subs["right_extend_length"], int) and subs["right_extend_length"] == 12
    assert subs["param_seed"] == self.calls[0]["seed"] != 0
    assert self.calls[0]["output_keys"] == ("audio", "audios")
    assert self.calls[0]["preflight_kind"] == "audio"


def test_failed_upload_returns_empty_without_queueing(mod, src):
    self = _stub(mod)

    async def _no_upload(s, name=""):
        return ""

    self.upload_image = _no_upload
    out = asyncio.run(mod.ComfyUIPlugin.extend_music(
        self, str(src), prompt="p", lyrics="l", right_seconds=10))
    assert out == ""
    assert self.calls == []
