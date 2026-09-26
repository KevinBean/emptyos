"""Build and submit an InfiniteTalk workflow to ComfyUI (audio-driven lip-sync video).

    python scripts/mv/lipsync/infinitetalk_workflow.py '{"mode":"v2v","video":"D:/src.mp4",
        "image":"start.jpg","audio":"guide.wav","frames":331,"seed":2,"steps":6,
        "start_step":0,"prefix":"out"}' [--comfy-host http://host:8188] [--print]

Modes:
- ``i2v``: a still plus audio.
- ``v2v``: a source video plus audio. Note that it regenerates every frame, so
  a dancing take flattens into a talking head (measured: 33% of mean motion kept).
- ``plain``: i2v with no audio embeds, for a source whose lips are deliberately
  not driven.
- ``multi``: two speakers in one frame.

Paths in the JSON use forward slashes; a Windows backslash breaks it.

Settings that decide the result (measured on the One More Hour tests,
2026-09-16):
- ``start_step`` is the whole ballgame. ``steps 4 / start_step 2`` leaves only
  two effective steps and keeps the source's mouth: sync r measured −0.047.
  ``steps 6 / start_step 0`` measured +0.490, against a native control's +0.528.
- The wav2vec model is not a choice. ``MultiTalkWav2VecEmbeds`` accepts only the
  tencent Chinese encoder, whatever language is sung.
- ``LoadWanVideoT5TextEncoder`` refuses fp8 *scaled*; a GGUF base cannot merge
  LoRAs, so ``merge_loras`` is False.
- The output is longer than asked: it is padded to whole windows (65 → 81,
  85 → 153 frames). Trim to the speech, and score it with the source frame
  count (``lipsync_check.py --frames``).

Model file names come from ``[mv_tools.infinitetalk]`` (keys: base, talk,
talk_multi, vae, t5, lora, w2v), defaulting to the set that works on the
16 GB card. The defaults use ComfyUI's Windows subfolder separator
(``wanvideo\\...``); a ComfyUI on Linux needs them set with ``/``. The host
comes from ``mv_config.comfy_host``.

Without ``pos`` the prompt is the MV default, "a man singing to camera" (or
the dance variant for ``plain``) — pass ``pos`` for anyone else. ``multi``
requires it: a two-person scene is never the single-singer default.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mv_config  # noqa: E402

MODEL_DEFAULTS = {
    "base": r"wanvideo\wan2.1-i2v-14b-480p-Q4_K_S.gguf",
    "talk": r"wanvideo\InfiniteTalk\Wan2_1-InfiniteTalk_Single_Q8.gguf",
    "talk_multi": r"wanvideo\InfiniteTalk\Wan2_1-InfiniteTalk_Multi_Q4_K_M.gguf",
    "vae": r"wanvideo\Wan2_1_VAE_bf16.safetensors",
    "t5": "umt5-xxl-enc-fp8_e4m3fn.safetensors",
    "lora": "lightx2v_T2V_14B_distill_rank64.safetensors",
    "w2v": "TencentGameMate/chinese-wav2vec2-base",
}

POS = ("a man singing to camera, natural relaxed performance, closed-lip between "
       "phrases, soft indoor lamp light, cinematic")
# Motion is the point of a plain source clip: adult groove, grounded weight
# shifts, open palms, closed lips, no held grin, feet no wider than hips.
POS_DANCE = ("a man singing and moving to the beat in a warm lamplit room, "
             "grounded on-beat weight shifts from one foot to the other, "
             "shoulders rolling with the groove, one hand rising in an open "
             "palm gesture, torso turning slightly, calm closed-lip face "
             "between phrases, feet no wider than hips, soft indoor lamp "
             "light, handheld 35mm, cinematic")
NEG = ("bright colors, overexposed, static, blurred details, subtitles, style, "
       "artwork, painting, picture, still, overall gray, worst quality, low quality, "
       "JPEG artifacts, ugly, deformed, extra fingers, poorly drawn face, "
       "malformed limbs, fused fingers, cluttered background, three legs, "
       "walking backwards")


def models_from_config(cfg: dict | None = None) -> dict:
    return {k: mv_config.setting(f"infinitetalk.{k}", v, cfg=cfg) for k, v in MODEL_DEFAULTS.items()}


def build(mode, image, audio, frames, seed, steps, start_step,
          w=480, h=832, fps=25.0, audio_scale=1.5, block_swap=22,
          window=81, motion_frame=9, prefix="uf_test", video=None,
          pos=None, audio_2=None, masks=None, models=None):
    """The ComfyUI API graph for one run. Pure: submits nothing.

    ``multi``: ``audio`` drives the first mask's face and ``audio_2`` the
    second; ``masks`` = [left, right, background] image names staged in
    ComfyUI/input (white = that speaker). Give each speaker their own track on
    a shared timeline, silent while the other talks. The saved mp4 carries
    audio_1 only.
    """
    if mode not in ("plain", "i2v", "v2v", "multi"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "multi" and not audio_2:
        raise ValueError("mode 'multi' needs audio_2 (the right-hand speaker)")
    if mode == "multi" and not masks:
        # The documented default masks are computed too late to be read
        # (nodes_sampler.py reads ref_target_masks at 586 and bakes it at 1456,
        # before multitalk_loop sets it), so a two-speaker run without masks
        # dies with "'NoneType' object has no attribute 'max'".
        raise ValueError("mode 'multi' needs masks=[left.png, right.png, background.png] staged in ComfyUI/input")
    if mode == "v2v" and not video:
        raise ValueError("mode 'v2v' needs video")
    if mode == "multi" and not pos:
        raise ValueError("mode 'multi' needs pos: describe both people, the default is one singer")
    m = {**MODEL_DEFAULTS, **(models or {})}
    g = {}

    def n(i, ct, inp):
        g[str(i)] = {"class_type": ct, "inputs": inp}

    n(1, "WanVideoVAELoader", {"model_name": m["vae"], "precision": "bf16"})
    n(2, "MultiTalkModelLoader", {"model": m["talk_multi"] if mode == "multi" else m["talk"]})
    n(3, "WanVideoBlockSwap", {"blocks_to_swap": block_swap,
                               "offload_img_emb": False, "offload_txt_emb": False})
    n(4, "WanVideoLoraSelect", {"lora": m["lora"], "strength": 1.0, "low_mem_load": False,
                                "merge_loras": False})
    n(5, "WanVideoModelLoader", {
        "model": m["base"], "base_precision": "fp16_fast", "quantization": "disabled",
        "load_device": "offload_device", "attention_mode": "sdpa",
        "block_swap_args": ["3", 0], "lora": ["4", 0], "multitalk_model": ["2", 0]})
    n(6, "LoadWanVideoT5TextEncoder", {"model_name": m["t5"], "precision": "bf16",
                                       "load_device": "offload_device",
                                       "quantization": "disabled"})
    n(7, "WanVideoTextEncode", {"positive_prompt": pos or (POS_DANCE if mode == "plain" else POS),
                                "negative_prompt": NEG, "t5": ["6", 0], "force_offload": True,
                                "device": "gpu"})
    n(8, "DownloadAndLoadWav2VecModel", {"model": m["w2v"], "base_precision": "fp16",
                                         "load_device": "main_device"})
    n(9, "LoadAudio", {"audio": audio})
    n(10, "MultiTalkWav2VecEmbeds", {
        "wav2vec_model": ["8", 0], "audio_1": ["9", 0], "normalize_loudness": True,
        "num_frames": frames, "fps": fps, "audio_scale": audio_scale,
        "audio_cfg_scale": 1.0, "multi_audio_type": "para"})
    if mode == "multi":
        n(17, "LoadAudio", {"audio": audio_2})
        g["10"]["inputs"]["audio_2"] = ["17", 0]
        # Row order matters: mask 1 drives audio_1, mask 2 audio_2, 3rd is background.
        for i, mk in enumerate(masks):
            n(20 + i, "LoadImage", {"image": mk})
        n(23, "ImageBatch", {"image1": ["20", 0], "image2": ["21", 0]})
        n(24, "ImageBatch", {"image1": ["23", 0], "image2": ["22", 0]})
        n(25, "ImageToMask", {"image": ["24", 0], "channel": "red"})
        g["10"]["inputs"]["ref_target_masks"] = ["25", 0]

    if mode == "plain":
        n(11, "LoadImage", {"image": image})
        n(12, "WanVideoImageResizeToClosest", {
            "image": ["11", 0], "generation_width": w, "generation_height": h,
            "aspect_ratio_preservation": "crop_to_new"})
        n(13, "WanVideoImageToVideoEncode", {
            "vae": ["1", 0], "width": w, "height": h, "num_frames": frames,
            "noise_aug_strength": 0.0, "start_latent_strength": 1.0,
            "end_latent_strength": 1.0, "force_offload": True,
            "start_image": ["12", 0], "tiled_vae": False})
        n(14, "WanVideoSampler", {
            "model": ["5", 0], "image_embeds": ["13", 0], "steps": steps, "cfg": 1.0,
            "shift": 11.0, "seed": seed, "force_offload": True,
            "scheduler": "dpm++_sde", "riflex_freq_index": 0,
            "text_embeds": ["7", 0], "start_step": start_step,
            "denoise_strength": 1.0, "rope_function": "comfy"})
        n(15, "WanVideoDecode", {"vae": ["1", 0], "samples": ["14", 0],
                                 "enable_vae_tiling": False, "tile_x": 272,
                                 "tile_y": 272, "tile_stride_x": 144,
                                 "tile_stride_y": 128})
        n(16, "VHS_VideoCombine", {
            "images": ["15", 0], "frame_rate": fps, "loop_count": 0,
            "filename_prefix": prefix, "format": "video/h264-mp4",
            "pingpong": False, "save_output": True})
        return g
    if mode in ("i2v", "multi"):
        n(11, "LoadImage", {"image": image})
    else:
        n(11, "VHS_LoadVideoPath", {
            "video": video, "force_rate": fps, "force_size": "Disabled",
            "custom_width": w, "custom_height": h, "frame_load_cap": frames,
            "skip_first_frames": 0, "select_every_nth": 1})
    n(12, "WanVideoImageResizeToClosest", {
        "image": ["11", 0], "generation_width": w, "generation_height": h,
        "aspect_ratio_preservation": "crop_to_new"})
    n(13, "WanVideoImageToVideoMultiTalk", {
        "vae": ["1", 0], "width": w, "height": h, "frame_window_size": window,
        "motion_frame": motion_frame, "force_offload": True, "colormatch": "disabled",
        "start_image": ["12", 0], "tiled_vae": False, "mode": "infinitetalk"})
    n(14, "WanVideoSampler", {
        "model": ["5", 0], "image_embeds": ["13", 0], "steps": steps, "cfg": 1.0,
        "shift": 11.0, "seed": seed, "force_offload": True, "scheduler": "dpm++_sde",
        "riflex_freq_index": 0, "text_embeds": ["7", 0],
        "multitalk_embeds": ["10", 0], "start_step": start_step,
        "denoise_strength": 1.0, "rope_function": "comfy"})
    n(15, "WanVideoDecode", {"vae": ["1", 0], "samples": ["14", 0],
                             "enable_vae_tiling": False, "tile_x": 272, "tile_y": 272,
                             "tile_stride_x": 144, "tile_stride_y": 128})
    n(16, "VHS_VideoCombine", {
        "images": ["15", 0], "frame_rate": fps, "loop_count": 0,
        "filename_prefix": prefix, "format": "video/h264-mp4", "pingpong": False,
        "save_output": True, "audio": ["10", 1]})
    return g


def submit(graph: dict, host: str) -> dict:
    body = json.dumps({"prompt": graph, "client_id": str(uuid.uuid4())}).encode()
    req = urllib.request.Request(f"{host}/prompt", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise OSError(f"ComfyUI answered with something that is not JSON: {raw[:200]!r}") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config", help="JSON object of build() arguments")
    ap.add_argument("--comfy-host")
    ap.add_argument("--print", action="store_true", help="print the graph, submit nothing")
    a = ap.parse_args(argv)
    try:
        cfg = json.loads(a.config)
        if not isinstance(cfg, dict):
            raise ValueError("the config must be a JSON object")
        if "models" in cfg:
            raise ValueError("model names come from [mv_tools.infinitetalk] in emptyos.toml, not the JSON")
        graph = build(**cfg, models=models_from_config())
    except (ValueError, TypeError) as exc:
        print(f"infinitetalk_workflow: {exc}", file=sys.stderr)
        return 1
    if a.print:
        print(json.dumps(graph, indent=1))
        return 0
    try:
        out = submit(graph, mv_config.comfy_host(a.comfy_host))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}\n{e.read().decode(errors='replace')[:3000]}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"infinitetalk_workflow: cannot reach ComfyUI: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"prompt_id": out.get("prompt_id"), "node_errors": out.get("node_errors")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
