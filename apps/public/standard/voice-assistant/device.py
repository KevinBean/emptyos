"""Voice-assistant — headless-device voice turns (ESP32 satellite path).

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the shared device preamble (multipart parse → bus announce →
audio normalize → STT), the collapsed /api/device_turn JSON reply, the
streaming /api/device_turn_stream NDJSON fast path, per-sentence WAV
transcode, debug audio persistence, and the ffprobe/astats review surface.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``turn_events`` (chat_pipeline) for the turn.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from emptyos.sdk import ndjson_response, web_route

from .chat_pipeline import turn_events
from .shared import is_stt_artifact

if TYPE_CHECKING:
    from .app import VoiceAssistantApp  # noqa: F401 — type hints only


# ─── Bind to VoiceAssistantApp class as ─────────────────────────────────
#   _device_prep              = _device._device_prep
#   api_device_turn           = _device.api_device_turn
#   _device_turn_error        = _device._device_turn_error
#   api_device_turn_stream    = _device.api_device_turn_stream
#   _device_wav_urls          = _device._device_wav_urls
#   _save_device_audio        = _device._save_device_audio
#   _device_turn_audio_review = _device._device_turn_audio_review
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


async def _device_prep(self, request):
    """Shared preamble for /api/device_turn and its streaming sibling: parse the
    multipart form, announce the device on the bus, normalize the mic audio, run
    STT. Returns ``(ctx, err)`` with exactly one truthy:
      ctx — {user_text, messages, companion_id, fast_mode, stt_ms, raw_audio, norm_metrics}
      err — {code, raw_audio, norm_metrics}; the caller shapes it (dict vs NDJSON).
    """
    import time as _time
    form = await request.form()
    audio_file = form.get("audio")
    messages_str = form.get("messages", "[]")
    companion_id = form.get("companion") or None
    # The device defaults to fast mode (latency matters most on a voice puck);
    # an explicit fast=0/1 form field overrides (the sim toggle sends it).
    _fast_raw = form.get("fast", None)
    if _fast_raw in (None, ""):
        fast_mode = bool(self.app_config("device_fast_mode", True))
    else:
        fast_mode = str(_fast_raw).lower() in ("1", "true", "on")

    # Announce the device on the bus so the `devices` registry can register +
    # heartbeat it through its normal traffic (decoupled; no-op if no listener).
    device_id = str(form.get("device_id") or "").strip()
    if device_id:
        import asyncio
        asyncio.create_task(self.emit("device:seen", {
            "id": device_id, "category": "controller",
            "board": str(form.get("device_board") or ""),
        }))
    try:
        messages = json.loads(messages_str)
    except Exception:
        messages = []
    if not isinstance(messages, list):
        messages = []

    if not audio_file:
        return None, {"code": "no audio", "raw_audio": b"", "norm_metrics": None}
    raw_audio = await audio_file.read()

    # Normalize quiet/variable device audio up into whisper's confidence range
    # before STT — a hardware mic's level can't be fixed by a single analog gain,
    # and whisper drops low-confidence quiet segments. A real-silence clip is
    # rejected so we never amplify room noise into a hallucinated turn.
    audio_bytes = raw_audio
    norm_metrics = None
    if self.app_config("device_audio_normalize", True):
        try:
            from emptyos.sdk.media.normalize import normalize_wav_pcm16
            normd, norm_metrics = normalize_wav_pcm16(raw_audio)
        except Exception:
            normd, norm_metrics = raw_audio, {"skipped": "error"}
        if normd is None:
            self._save_device_audio(raw_audio, None)
            return None, {"code": "too quiet", "raw_audio": raw_audio, "norm_metrics": norm_metrics}
        audio_bytes = normd

    # Debug: persist the last device recording (raw + normalized) so the actual
    # captured audio can be fetched + inspected without reflashing the device.
    self._save_device_audio(raw_audio, audio_bytes)

    _t_stt = _time.perf_counter()
    try:
        user_text = await self.listen(audio=audio_bytes)
    except Exception as e:
        return None, {"code": f"Failed to listen: {e}", "raw_audio": raw_audio, "norm_metrics": norm_metrics}
    stt_ms = int((_time.perf_counter() - _t_stt) * 1000)
    if not user_text or not user_text.strip() or is_stt_artifact(user_text):
        # Caption-artifact filter — Whisper hallucinates YouTube boilerplate on
        # silence/noise that survived the RMS gate. Treat as no speech.
        return None, {"code": "Could not understand audio", "raw_audio": raw_audio, "norm_metrics": norm_metrics}

    return {
        "user_text": user_text, "messages": messages, "companion_id": companion_id,
        "fast_mode": fast_mode, "stt_ms": stt_ms, "raw_audio": raw_audio,
        "norm_metrics": norm_metrics,
    }, None


@web_route("POST", "/api/device_turn")
async def api_device_turn(self, request):
    """Headless-device voice turn — collapses the streaming pipeline into a
    single JSON reply a thin client (e.g. an ESP32 satellite) can consume.

    Request (multipart/form-data):
      audio    — WAV bytes from the device mic (16 kHz mono 16-bit ideal)
      messages — JSON string of prior turns (optional; from a prior session)
      companion — companion id echoed from a prior turn (optional)

    Response (JSON):
      {transcript, reply_text, audio_urls[], actions[], session{messages, companion}}

    Reuses the exact listen→think→intent→speak pipeline as /api/chat_stream
    via turn_events (chat_pipeline.py) — the browser stream's per-sentence
    events are collapsed here so the device never parses NDJSON. Play each
    audio_urls[i] in order; persist `session` and resend it next turn.
    """
    ctx, err = await self._device_prep(request)
    debug = request.query_params.get("debug") == "1"
    if err:
        code = err["code"]
        if code == "no audio":
            return {"error": "no audio"}
        if code.startswith("Failed to listen"):
            return {"error": code}
        return await self._device_turn_error(code, err["raw_audio"], err["norm_metrics"], debug)

    import time as _time
    user_text = ctx["user_text"]
    messages = ctx["messages"]
    companion_id = ctx["companion_id"]
    fast_mode = ctx["fast_mode"]
    stt_ms = ctx["stt_ms"]
    raw_audio = ctx["raw_audio"]
    norm_metrics = ctx["norm_metrics"]

    reply_text = ""
    audio_urls: list[str] = []
    actions: list[dict] = []
    session = {"messages": messages, "companion": companion_id}

    # Prefer a WAV provider (kokoro) so the device can play the reply audio
    # without an MP3 decoder. Browser /api/chat_stream is unaffected.
    speak_prefer = self.app_config("device_speak_providers", ["kokoro"]) or None
    _t_gen = _time.perf_counter()
    async for ev in turn_events(self, user_text, messages, companion_id, speak_prefer=speak_prefer, fast=fast_mode, surface="device"):
        t = ev.get("type")
        if t == "text":
            reply_text += ev.get("delta", "")
        elif t == "audio":
            url = ev.get("url")
            if url:
                audio_urls.append(url)
        elif t == "card":
            actions.append({"verb": ev.get("intent", ""), "status": "applied",
                            "description": ev.get("title") or ""})
        elif t == "link":
            actions.append({"verb": ev.get("intent", ""), "status": "applied",
                            "description": ev.get("text") or ""})
        elif t == "pending_action":
            actions.append({"verb": ev.get("verb", ""), "status": "pending",
                            "description": ev.get("description") or "",
                            "action_id": ev.get("action_id")})
        elif t == "confirm_required":
            actions.append({"verb": ev.get("verb", ""), "status": "confirm_required",
                            "description": ev.get("description") or ""})
        elif t == "companion_switch":
            session["companion"] = ev.get("companion")
        elif t == "error":
            return {"error": ev.get("error", "turn failed"), "transcript": user_text}
        elif t == "done":
            session["messages"] = ev.get("messages", messages)
            session["companion"] = ev.get("companion")
            reply_text = ev.get("full_text") or reply_text

    gen_ms = int((_time.perf_counter() - _t_gen) * 1000)

    # The reply TTS is usually MP3; the device plays raw PCM, so transcode to
    # 16 kHz mono WAV it can play directly.
    _t_xc = _time.perf_counter()
    if self.app_config("device_transcode_wav", True):
        audio_urls = await self._device_wav_urls(audio_urls)
    xcode_ms = int((_time.perf_counter() - _t_xc) * 1000)

    out = {
        "transcript": user_text,
        "reply_text": reply_text.strip(),
        "audio_urls": audio_urls,
        "actions": actions,
        "session": session,
        "timing": {"stt_ms": stt_ms, "gen_ms": gen_ms, "xcode_ms": xcode_ms, "fast": fast_mode},
    }
    if debug:
        out["norm_metrics"] = norm_metrics
        out["audio_metrics"] = await self._device_turn_audio_review(raw_audio)
    return out


async def _device_turn_error(self, msg: str, raw_audio: bytes, norm_metrics, debug: bool) -> dict:
    """Device-shaped error reply, with audio diagnostics attached under ?debug=1."""
    out = {"error": msg, "transcript": ""}
    if debug:
        out["norm_metrics"] = norm_metrics
        out["audio_metrics"] = await self._device_turn_audio_review(raw_audio)
    return out


@web_route("POST", "/api/device_turn_stream")
async def api_device_turn_stream(self, request):
    """Streaming device turn — the fast path. Same pipeline as /api/device_turn but
    NOT collapsed: emits NDJSON events as they happen, so the puck plays sentence 1
    while the rest still generates. Reply audio is transcoded per-sentence to 16 kHz
    mono WAV (overlaps with playback). Event shapes the device reads:
      {"type":"transcript","text":..}   once, after STT
      {"type":"text","delta":..}          reply text as it streams (display only)
      {"type":"audio","url":..}           play this now (one per sentence)
      {"type":"done","reply_text":..,"session":{messages,companion},"timing":{..}}
      {"type":"error","error":..}
    """
    import time as _time

    async def _err(msg):
        yield {"type": "error", "error": msg}

    ctx, err = await self._device_prep(request)
    if err:
        return ndjson_response(_err(err["code"]))
    user_text = ctx["user_text"]
    messages = ctx["messages"]
    companion_id = ctx["companion_id"]
    fast_mode = ctx["fast_mode"]
    stt_ms = ctx["stt_ms"]

    speak_prefer = self.app_config("device_speak_providers", ["kokoro"]) or None
    transcode = bool(self.app_config("device_transcode_wav", True))

    async def dev_stream():
        yield {"type": "transcript", "text": user_text}
        _t_gen = _time.perf_counter()
        session = {"messages": messages, "companion": companion_id}
        async for ev in turn_events(
            self, user_text, messages, companion_id, echo_user_text=False,
            speak_prefer=speak_prefer, fast=fast_mode, surface="device",
        ):
            t = ev.get("type")
            if t == "audio":
                url = ev.get("url")
                if not url:
                    continue
                if transcode:
                    try:
                        url = (await self._device_wav_urls([url]))[0]
                    except Exception:
                        pass
                yield {"type": "audio", "url": url}
            elif t == "text":
                d = ev.get("delta", "")
                if d:
                    yield {"type": "text", "delta": d}
            elif t in ("pending_action", "confirm_required"):
                yield {"type": "action", "verb": ev.get("verb", ""),
                       "description": ev.get("description", "")}
            elif t == "error":
                yield {"type": "error", "error": ev.get("error", "turn failed")}
            elif t == "done":
                session = {"messages": ev.get("messages", messages),
                           "companion": ev.get("companion")}
                yield {"type": "done",
                       "reply_text": ev.get("full_text", "") or "",
                       "session": session,
                       "timing": {"stt_ms": stt_ms,
                                  "gen_ms": int((_time.perf_counter() - _t_gen) * 1000),
                                  "fast": fast_mode}}

    return ndjson_response(dev_stream())


async def _device_wav_urls(self, urls: list[str]) -> list[str]:
    """Transcode reply audio (often MP3) to 16 kHz mono PCM WAV the device can play.
    Falls back to the original URL when ffmpeg is missing or a transcode fails."""
    import asyncio
    import shutil

    ff = shutil.which("ffmpeg")
    if not ff or not urls:
        return urls
    adir = self.data_dir / "audio"
    out: list[str] = []
    for u in urls:
        try:
            name = u.rsplit("/", 1)[-1]
            src = adir / name
            if not src.exists():
                out.append(u)
                continue
            dst = src.with_name(src.stem + "_w.wav")
            proc = await asyncio.create_subprocess_exec(
                ff, "-y", "-loglevel", "error", "-i", str(src),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            out.append(u.rsplit("/", 1)[0] + "/" + dst.name if dst.exists() else u)
        except Exception:
            out.append(u)
    return out


def _save_device_audio(self, raw_audio: bytes, normalized: bytes | None) -> None:
    """Persist the last device recording (raw + normalized) so the actual captured
    audio is fetchable at /voice-assistant/audio/_dev_last_{raw,norm}.wav for inspection."""
    if not self.app_config("device_audio_debug_save", True):
        return
    try:
        d = self.data_dir / "audio"
        d.mkdir(parents=True, exist_ok=True)
        (d / "_dev_last_raw.wav").write_bytes(raw_audio)
        if normalized is not None and normalized is not raw_audio:
            (d / "_dev_last_norm.wav").write_bytes(normalized)
    except Exception:
        pass


async def _device_turn_audio_review(self, audio_bytes: bytes) -> dict:
    """ffprobe/astats metrics (rms_db/peak_db/duration/silence) on the received
    audio — debug only, so a satellite's level can be diagnosed without reflashing."""
    import os
    import tempfile
    from pathlib import Path

    import math

    from emptyos.sdk.media.review import review_audio

    def _fin(x):  # -inf/nan (silence) aren't valid JSON under Starlette's allow_nan=False
        return None if isinstance(x, float) and not math.isfinite(x) else x

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    tmp = Path(path)
    try:
        tmp.write_bytes(audio_bytes)
        v = await review_audio(str(tmp))
        return {"ok": v.ok, "hard": list(v.hard), "soft": list(v.soft),
                **{k: _fin(val) for k, val in v.metrics.items()}}
    except Exception as e:
        return {"error": str(e)}
    finally:
        tmp.unlink(missing_ok=True)
