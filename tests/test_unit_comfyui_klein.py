"""Unit tests for the comfyui plugin's FLUX.2 Klein branch + text overlay.

Kernel-free: loads plugins/comfyui/plugin.py via importlib and exercises the
pure workflow builders on a stub instance (no daemon, no ComfyUI needed).
Pins the wiring added for the Klein commercial-cover path.
"""
import importlib.util
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "comfyui" / "plugin.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_plugin_test", PLUGIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def plugin(mod):
    # skip __init__ (which wants a kernel); the builders only use self to reach
    # sibling builder methods, no instance state.
    return mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)


def _types(wf):
    return {n["class_type"] for n in wf.values()}


def test_klein_presets_exist(mod):
    for key, unet, clip in [
        ("klein-4b", "flux-2-klein-4b-fp8.safetensors", "qwen_3_4b.safetensors"),
        ("klein-9b", "flux-2-klein-9b-fp8.safetensors", "qwen_3_8b_fp8mixed.safetensors"),
    ]:
        p = mod.STYLE_PRESETS[key]
        assert p["is_flux2"] is True
        assert p["unet"] == unet
        assert p["clip"] == clip  # encoders are NOT interchangeable — pin the pairing


def test_flux2_graph_shape(mod, plugin):
    wf = plugin._build_workflow("art", 1024, 1024, 42, mod.STYLE_PRESETS["klein-4b"])
    t = _types(wf)
    assert "UNETLoader" in t and "CheckpointLoaderSimple" not in t
    assert {"CLIPLoader", "VAELoader", "EmptyFlux2LatentImage",
            "Flux2Scheduler", "SamplerCustomAdvanced", "BasicGuider"} <= t
    # guidance-distilled → exactly one CLIPTextEncode (no negative channel)
    assert sum(1 for n in wf.values() if n["class_type"] == "CLIPTextEncode") == 1
    # correct encoder + vae wired
    clip = next(n for n in wf.values() if n["class_type"] == "CLIPLoader")["inputs"]
    assert clip["clip_name"] == "qwen_3_4b.safetensors" and clip["type"] == "flux2"


def test_flux2_no_overlay_saves_decoded(mod, plugin):
    wf = plugin._build_workflow("art", 1024, 1024, 42, mod.STYLE_PRESETS["klein-4b"])
    assert "DrawText+" not in _types(wf)
    save = next(n for n in wf.values() if n["class_type"] == "SaveImage")
    assert save["inputs"]["images"] == ["dec", 0]  # straight from the shared sample tail's VAEDecode


def test_flux2_overlay_appends_drawtext(mod, plugin):
    wf = plugin._build_workflow(
        "art", 1024, 1024, 42, mod.STYLE_PRESETS["klein-4b"],
        overlay_title="EMPTY OS", overlay_subtitle="a mind companion",
    )
    assert "DrawText+" in _types(wf)
    save = next(n for n in wf.values() if n["class_type"] == "SaveImage")
    assert save["inputs"]["images"] == ["t_sub", 0]  # last overlay in the chain
    # overlay nudges the prompt text-free so the model stops baking in letters
    enc = next(n for n in wf.values() if n["class_type"] == "CLIPTextEncode")
    assert "no text" in enc["inputs"]["text"]


def test_flux2_title_only(mod, plugin):
    wf = plugin._build_workflow(
        "art", 1024, 1024, 42, mod.STYLE_PRESETS["klein-4b"], overlay_title="HELLO",
    )
    save = next(n for n in wf.values() if n["class_type"] == "SaveImage")
    assert save["inputs"]["images"] == ["t_title", 0]
    assert "t_sub" not in wf


def test_flux1_path_unaffected(mod, plugin):
    wf = plugin._build_workflow("art", 1024, 1024, 42, {})  # no preset → FLUX.1 default
    t = _types(wf)
    assert "CheckpointLoaderSimple" in t and "UNETLoader" not in t
    assert wf["9"]["inputs"]["images"] == ["8", 0]  # unchanged save path
    assert "DrawText+" not in t


def test_flux1_overlay_also_works(mod, plugin):
    wf = plugin._build_workflow("art", 1024, 1024, 42, {}, overlay_title="HI")
    assert "DrawText+" in _types(wf)
    assert wf["9"]["inputs"]["images"] == ["t_title", 0]


# --- reference-conditioned edit / i2i / multi-reference -----------------------

def test_edit_single_ref_preserves_canvas(mod, plugin):
    wf = plugin._build_flux2_edit_workflow(
        "make it night", 1024, 1024, 1, mod.STYLE_PRESETS["klein-9b"], ["src.png"],
    )
    t = _types(wf)
    assert sum(1 for n in wf.values() if n["class_type"] == "LoadImage") == 1
    assert sum(1 for n in wf.values() if n["class_type"] == "ReferenceLatent") == 1
    assert "EmptyFlux2LatentImage" not in t  # single ref edits in place
    # sampler canvas is the encoded reference latent (structure preserved)
    assert wf["samp"]["inputs"]["latent_image"] == ["en0", 0]
    # correct encoder pairing carried from the 9B preset
    assert wf["c"]["inputs"]["clip_name"] == "qwen_3_8b_fp8mixed.safetensors"


def test_edit_multi_ref_composes_on_fresh_canvas(mod, plugin):
    wf = plugin._build_flux2_edit_workflow(
        "combine them", 1024, 1024, 1, mod.STYLE_PRESETS["klein-9b"], ["a.png", "b.png"],
    )
    assert sum(1 for n in wf.values() if n["class_type"] == "LoadImage") == 2
    # two references chained: rl1's conditioning comes from rl0
    assert wf["rl1"]["inputs"]["conditioning"] == ["rl0", 0]
    assert "EmptyFlux2LatentImage" in _types(wf)
    assert wf["samp"]["inputs"]["latent_image"] == ["empty", 0]
    # guider conditioned on the last reference in the chain
    assert wf["guider"]["inputs"]["conditioning"] == ["rl1", 0]


def test_edit_overlay_appends(mod, plugin):
    wf = plugin._build_flux2_edit_workflow(
        "x", 1024, 1024, 1, mod.STYLE_PRESETS["klein-4b"], ["src.png"],
        overlay_title="HELLO",
    )
    assert "DrawText+" in _types(wf)
    assert wf["save"]["inputs"]["images"] == ["t_title", 0]


def test_edit_image_rejects_non_flux2(mod, plugin):
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(plugin.edit_image("src.png", "edit", model="photo"))
