"""Unit tests for the comfyui plugin's Qwen-Image branch.

Kernel-free: loads plugins/comfyui/plugin.py via importlib and exercises the
pure workflow builder on a stub instance (no daemon, no ComfyUI needed), plus
one routed ``generate()`` call with the network edges stubbed, so the preset is
proven to reach the builder rather than only the builder proven to work.
"""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "comfyui" / "plugin.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("eos_comfy_plugin_qwen_test", PLUGIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def plugin(mod):
    return mod.ComfyUIPlugin.__new__(mod.ComfyUIPlugin)


def _node(wf, class_type):
    return next(n for n in wf.values() if n["class_type"] == class_type)


def _build(mod, plugin, **kw):
    return plugin._build_workflow("a poster", 1024, 1024, 42, mod.STYLE_PRESETS["qwen-2512"], **kw)


def test_preset_pins_the_benchmarked_files(mod):
    p = mod.STYLE_PRESETS["qwen-2512"]
    assert p["is_qwen_image"] is True and not p.get("is_flux2")
    # Q3, not Q4: Q4 left ~0.8 GB VRAM and stalled into shared memory on the 16 GB card
    assert p["unet"] == "qwen-image-2512-Q3_K_M.gguf"
    assert p["clip"] == "qwen_2.5_vl_7b_fp8_scaled.safetensors"
    assert p["vae"] == "qwen_image_vae.safetensors"
    assert (p["steps"], p["cfg"]) == (4, 1.0)
    # studio reads a `lora` key as "this style carries a user LoRA"; the
    # Lightning distillation is not one, so it lives under its own key
    assert "lora" not in p and p["accel_lora"].startswith("Qwen-Image-2512-Lightning-4steps")


def test_graph_shape(mod, plugin):
    wf = _build(mod, plugin)
    assert _node(wf, "UnetLoaderGGUF")["inputs"]["unet_name"] == "qwen-image-2512-Q3_K_M.gguf"
    clip = _node(wf, "CLIPLoader")["inputs"]
    assert clip["type"] == "qwen_image"
    assert _node(wf, "VAELoader")["inputs"]["vae_name"] == "qwen_image_vae.safetensors"
    ks = _node(wf, "KSampler")["inputs"]
    assert (ks["steps"], ks["cfg"], ks["seed"], ks["sampler_name"], ks["scheduler"]) == (4, 1.0, 42, "euler", "simple")
    assert ks["denoise"] == 1.0
    # the prompt reaches the sampler, and the decoder uses the Qwen VAE
    assert ks["positive"] == ["4", 0] and ks["latent_image"] == ["6", 0]
    assert wf["10"]["inputs"] == {"samples": ["9", 0], "vae": ["3", 0]}
    # none of the Klein / FLUX.1 loaders leak into this graph
    types = {n["class_type"] for n in wf.values()}
    assert not types & {"UNETLoader", "CheckpointLoaderSimple", "EmptyFlux2LatentImage"}


def test_non_square_canvas_keeps_both_dimensions(mod, plugin):
    # music-studio renders MV stills at 1280x720; a width/height mix-up stays green on a square
    wf = plugin._build_workflow("a still", 1280, 720, 1, mod.STYLE_PRESETS["qwen-2512"])
    latent = _node(wf, "EmptySD3LatentImage")["inputs"]
    assert (latent["width"], latent["height"]) == (1280, 720)


def test_builder_reads_the_preset_not_literals(plugin):
    style = {"is_qwen_image": True, "unet": "u.gguf", "clip": "c.safetensors", "vae": "v.safetensors",
             "accel_lora": "l.safetensors", "steps": 9, "cfg": 2.5, "shift": 1.7}
    wf = plugin._build_workflow("p", 512, 512, 3, style)
    assert _node(wf, "UnetLoaderGGUF")["inputs"]["unet_name"] == "u.gguf"
    assert _node(wf, "CLIPLoader")["inputs"]["clip_name"] == "c.safetensors"
    assert _node(wf, "VAELoader")["inputs"]["vae_name"] == "v.safetensors"
    assert _node(wf, "LoraLoaderModelOnly")["inputs"]["lora_name"] == "l.safetensors"
    assert _node(wf, "ModelSamplingAuraFlow")["inputs"]["shift"] == 1.7
    ks = _node(wf, "KSampler")["inputs"]
    assert (ks["steps"], ks["cfg"]) == (9, 2.5)


def test_lightning_lora_sits_between_unet_and_shift(mod, plugin):
    wf = _build(mod, plugin)
    lora = wf["7"]
    assert lora["class_type"] == "LoraLoaderModelOnly"
    assert lora["inputs"] == {"model": ["1", 0], "strength_model": 1.0,
                              "lora_name": mod.STYLE_PRESETS["qwen-2512"]["accel_lora"]}
    assert wf["8"]["class_type"] == "ModelSamplingAuraFlow"
    assert wf["8"]["inputs"] == {"model": ["7", 0], "shift": 3.1}
    assert wf["9"]["inputs"]["model"] == ["8", 0]


def test_no_accel_lora_wires_unet_straight_to_shift(plugin):
    style = {"is_qwen_image": True, "unet": "u.gguf", "clip": "c", "vae": "v"}
    wf = plugin._build_workflow("p", 512, 512, 3, style)
    assert "LoraLoaderModelOnly" not in {n["class_type"] for n in wf.values()}
    assert _node(wf, "ModelSamplingAuraFlow")["inputs"]["model"] == ["1", 0]


def test_prompt_untouched_without_overlay(mod, plugin):
    wf = _build(mod, plugin)
    assert wf["4"]["inputs"]["text"] == "a poster"  # the model is asked to draw text itself
    assert wf["20"]["inputs"]["images"] == ["10", 0]


def test_overlay_appends_drawtext_and_frees_the_prompt(mod, plugin):
    # Latin title on purpose: the overlay fonts carry no CJK glyphs, so a Chinese
    # title belongs in the prompt (see the preset comment), never in overlay_title
    wf = _build(mod, plugin, overlay_title="EMPTY OS")
    assert wf["20"]["inputs"]["images"] == ["t_title", 0]
    assert wf["t_title"]["inputs"]["img_composite"] == ["10", 0]
    assert wf["4"]["inputs"]["text"].startswith("a poster") and "no text" in wf["4"]["inputs"]["text"]


def test_subtitle_only_overlay(mod, plugin):
    wf = _build(mod, plugin, overlay_subtitle="a mind companion")
    assert wf["20"]["inputs"]["images"] == ["t_sub", 0]
    assert "t_title" not in wf


def test_negative_routes_to_the_negative_encoder(mod, plugin):
    wf = _build(mod, plugin, negative_extra="watermark")
    assert wf["5"]["inputs"]["text"] == "watermark"
    assert wf["9"]["inputs"]["negative"] == ["5", 0]


def test_klein_graph_unaffected(mod, plugin):
    wf = plugin._build_workflow("art", 1024, 1024, 42, mod.STYLE_PRESETS["klein-9b"])
    types = {n["class_type"] for n in wf.values()}
    assert "UNETLoader" in types and "UnetLoaderGGUF" not in types


def test_generate_routes_the_preset_to_the_qwen_graph(mod, plugin):
    submitted = {}

    async def _noop(*a, **k):
        return None

    async def _submit(workflow, **kw):
        submitted["wf"] = workflow
        return {"filename": "x.png", "subfolder": "", "type": "output"}

    plugin._ensure_runtime_compatible = _noop
    plugin._preflight = _noop
    plugin._maybe_free_gpu = _noop
    plugin._submit_and_poll = _submit
    plugin.kernel = SimpleNamespace(syslog=SimpleNamespace(info=lambda *a, **k: None))

    asyncio.run(plugin.generate("一张海报", style="qwen-2512", seed=7))
    wf = submitted["wf"]
    assert _node(wf, "UnetLoaderGGUF")["inputs"]["unet_name"] == "qwen-image-2512-Q3_K_M.gguf"
    assert _node(wf, "KSampler")["inputs"]["seed"] == 7
