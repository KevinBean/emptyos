"""Voice API Server — TTS (Kokoro default, edge-tts + XTTS fallback) + STT (faster-whisper).

Runs on port 8602 (override with VOICE_API_PORT). Port 8601 is reserved for
the legacy home-portal local-voice-api used by TalkBuddy. EmptyOS voice-api
plugin connects here.

TTS engine selection:
  - Default (no voice specified): Kokoro (local ONNX, high quality)
  - voice="kokoro:<id>" or bare Kokoro id (e.g. "af_heart", "zf_xiaoxiao"): Kokoro
  - voice in custom voice registry: XTTS v2 (GPU voice cloning)
  - voice is edge alias or edge voice name: Edge-TTS
  - Kokoro failure auto-falls-back to Edge-TTS

Endpoints:
  GET  /health           — status check
  POST /tts              — text to speech (JSON or form) → audio file path
  POST /stt              — speech to text (multipart file) → transcript
  GET  /voices           — list all voices (kokoro + edge + custom)
  POST /voices/register  — register a custom voice (upload reference audio)
  DELETE /voices/{id}    — remove a custom voice

Start:  python services/voice-api/server.py
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from starlette.requests import Request

logger = logging.getLogger("voice-api")

app = FastAPI(title="EmptyOS Voice API")

# --- Auth middleware ---------------------------------------------------------
# Default-bind is loopback (127.0.0.1). If you opt into a network bind via
# VOICE_API_HOST, you should also set VOICE_API_TOKEN — otherwise every device
# on the network can call TTS/STT/upload at no cost to themselves.
#
# Token is re-read from os.environ on every request so a rotation takes effect
# without restarting the service.
_AUTH_EXEMPT_PATHS = {"/health", "/"}


def _voice_api_token() -> str:
    return os.environ.get("VOICE_API_TOKEN", "").strip()


@app.middleware("http")
async def _auth_mw(request: Request, call_next):
    token = _voice_api_token()
    if not token:
        return await call_next(request)
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        import hmac as _hmac

        if _hmac.compare_digest(auth[7:].strip(), token):
            return await call_next(request)
    return JSONResponse({"error": "unauthorized"}, status_code=401)


AUDIO_DIR = Path(tempfile.gettempdir()) / "emptyos-voice"
AUDIO_DIR.mkdir(exist_ok=True)

VOICES_DIR = (
    Path(os.environ["VOICES_DIR"])
    if os.environ.get("VOICES_DIR")
    else Path(__file__).parent / "voices"
)
VOICES_DIR.mkdir(exist_ok=True)

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")

# Kokoro model files (default engine)
KOKORO_MODEL_DIR = (
    Path(os.environ["KOKORO_MODEL_DIR"])
    if os.environ.get("KOKORO_MODEL_DIR")
    else Path(__file__).parent / "models" / "kokoro"
)
KOKORO_ONNX = KOKORO_MODEL_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES = KOKORO_MODEL_DIR / "voices-v1.0.bin"

# Built-in edge-tts voices (fallback engine)
EDGE_VOICES = {
    "emma": {"name": "Emma (British)", "edge": "en-GB-SoniaNeural", "type": "edge"},
    "michael": {"name": "Michael (American)", "edge": "en-US-GuyNeural", "type": "edge"},
    "sarah": {"name": "Sarah (American)", "edge": "en-US-JennyNeural", "type": "edge"},
    "george": {"name": "George (British)", "edge": "en-GB-RyanNeural", "type": "edge"},
}

# Curated Kokoro voices surfaced via /voices (the full 54-voice set is still callable by id)
KOKORO_VOICE_CATALOG = {
    "af_heart": {"name": "Heart (US Female, warm)", "language": "en-us"},
    "af_bella": {"name": "Bella (US Female)", "language": "en-us"},
    "af_nicole": {"name": "Nicole (US Female)", "language": "en-us"},
    "af_sarah": {"name": "Sarah (US Female)", "language": "en-us"},
    "am_michael": {"name": "Michael (US Male)", "language": "en-us"},
    "am_adam": {"name": "Adam (US Male)", "language": "en-us"},
    "am_eric": {"name": "Eric (US Male)", "language": "en-us"},
    "bf_emma": {"name": "Emma (UK Female)", "language": "en-gb"},
    "bf_isabella": {"name": "Isabella (UK Female)", "language": "en-gb"},
    "bm_george": {"name": "George (UK Male)", "language": "en-gb"},
    "bm_lewis": {"name": "Lewis (UK Male)", "language": "en-gb"},
    "zf_xiaoxiao": {"name": "Xiaoxiao (Mandarin Female)", "language": "cmn"},
    "zf_xiaoyi": {"name": "Xiaoyi (Mandarin Female)", "language": "cmn"},
    "zm_yunxi": {"name": "Yunxi (Mandarin Male)", "language": "cmn"},
    "zm_yunjian": {"name": "Yunjian (Mandarin Male)", "language": "cmn"},
    "jf_alpha": {"name": "Alpha (Japanese Female)", "language": "ja"},
    "jm_kumo": {"name": "Kumo (Japanese Male)", "language": "ja"},
}

KOKORO_DEFAULT_VOICES = {
    "en": "af_heart",
    "zh": "zf_xiaoxiao",
    "ja": "jf_alpha",
}

# Lazy-loaded models
_whisper_model = None
_xtts_model = None
_kokoro_model = None
_kokoro_lock = asyncio.Lock()


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        print(f"[Voice API] Loading faster-whisper: {WHISPER_MODEL}")
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(WHISPER_MODEL, compute_type="int8")
        print("[Voice API] Whisper ready")
    return _whisper_model


def get_xtts():
    global _xtts_model
    if _xtts_model is None:
        print("[Voice API] Loading XTTS v2 (first use, may take a moment)...")
        from TTS.api import TTS

        _xtts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cuda")
        print("[Voice API] XTTS ready")
    return _xtts_model


KOKORO_LANG_BY_PREFIX = {
    "af": "en-us",
    "am": "en-us",
    "bf": "en-gb",
    "bm": "en-gb",
    "zf": "cmn",
    "zm": "cmn",
    "jf": "ja",
    "jm": "ja",
    "ef": "es",
    "em": "es",
    "ff": "fr-fr",
    "hf": "hi",
    "hm": "hi",
    "if": "it",
    "im": "it",
    "pf": "pt-br",
    "pm": "pt-br",
}


def _kokoro_files_present() -> bool:
    return KOKORO_ONNX.exists() and KOKORO_VOICES.exists()


async def get_kokoro():
    """Lazy-load Kokoro singleton. Returns None if model files are missing."""
    global _kokoro_model
    if _kokoro_model is not None:
        return _kokoro_model
    if not _kokoro_files_present():
        return None
    async with _kokoro_lock:
        if _kokoro_model is None:
            print(f"[Voice API] Loading Kokoro ONNX from {KOKORO_MODEL_DIR}...")
            from kokoro_onnx import Kokoro

            _kokoro_model = await asyncio.to_thread(Kokoro, str(KOKORO_ONNX), str(KOKORO_VOICES))
            print(f"[Voice API] Kokoro ready ({len(list(_kokoro_model.get_voices()))} voices)")
    return _kokoro_model


def _resolve_kokoro_voice(voice: str) -> str | None:
    """Return a Kokoro voice id if `voice` refers to one, else None."""
    if not voice:
        return None
    if voice.startswith("kokoro:"):
        return voice.split(":", 1)[1]
    # Bare Kokoro-style id: two letters + underscore + name
    if len(voice) > 3 and voice[2] == "_" and voice[:2].lower() in KOKORO_LANG_BY_PREFIX:
        return voice
    return None


def _detect_lang(text: str) -> str:
    """Detect primary language from text (for picking a default voice)."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    if cjk > len(text) * 0.1:
        return "zh"
    hira_kata = sum(1 for c in text if "\u3040" <= c <= "\u30ff")
    if hira_kata > len(text) * 0.05:
        return "ja"
    return "en"


def _load_custom_voices() -> dict:
    """Load custom voice registry from voices/registry.json."""
    reg_path = VOICES_DIR / "registry.json"
    if reg_path.exists():
        return json.loads(reg_path.read_text(encoding="utf-8"))
    return {}


def _save_custom_voices(data: dict):
    (VOICES_DIR / "registry.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# --- Endpoints ---


@app.get("/health")
async def health():
    custom_count = len(_load_custom_voices())
    return {
        "status": "ok",
        "whisper": WHISPER_MODEL,
        "default_engine": "kokoro" if _kokoro_files_present() else "edge",
        "kokoro_available": _kokoro_files_present(),
        "kokoro_loaded": _kokoro_model is not None,
        "kokoro_voices": len(KOKORO_VOICE_CATALOG),
        "edge_voices": len(EDGE_VOICES),
        "custom_voices": custom_count,
        "xtts_loaded": _xtts_model is not None,
    }


@app.post("/stt")
async def stt(file: UploadFile = File(...), language: str = Form("en")):
    ext = Path(file.filename or "audio.wav").suffix or ".wav"
    tmp = AUDIO_DIR / f"stt_{uuid.uuid4().hex[:8]}{ext}"
    tmp.write_bytes(await file.read())
    try:
        model = get_whisper()
        segments, info = model.transcribe(str(tmp), language=language, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments)
        return {"text": text, "language": info.language, "duration": round(info.duration, 2)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/tts")
async def tts(request: Request):
    ct = request.headers.get("content-type", "")
    if "json" in ct:
        data = await request.json()
        text = data.get("text", "")
        voice = data.get("voice", "")
        speed = float(data.get("speed", 1.0))
        engine = str(data.get("engine", "") or "").lower()
        language = str(data.get("language", "") or "")
    else:
        form = await request.form()
        text = str(form.get("text", ""))
        voice = str(form.get("voice", ""))
        speed = float(form.get("speed", "1.0"))
        engine = str(form.get("engine", "") or "").lower()
        language = str(form.get("language", "") or "")

    if not text.strip():
        return JSONResponse({"error": "text required"}, status_code=400)

    custom_voices = _load_custom_voices()

    if voice and voice in custom_voices:
        return await _tts_xtts(text, custom_voices[voice], speed)

    if engine == "edge":
        return await _tts_edge(text, voice, speed)
    if engine == "kokoro":
        return await _tts_kokoro_or_fallback(text, voice or None, speed, language)

    k_voice = _resolve_kokoro_voice(voice)
    if k_voice:
        return await _tts_kokoro_or_fallback(text, k_voice, speed, language)

    # Any other non-empty voice string is treated as an Edge voice name/alias;
    # empty voice falls through to Kokoro (default engine).
    if voice:
        return await _tts_edge(text, voice, speed)
    return await _tts_kokoro_or_fallback(text, None, speed, language)


async def _tts_kokoro_or_fallback(text: str, voice: str | None, speed: float, language: str = ""):
    """Try Kokoro; on any failure fall back to Edge-TTS."""
    try:
        return await _tts_kokoro(text, voice, speed, language)
    except Exception as e:
        logger.warning("Kokoro synthesis failed, falling back to edge-tts: %s", e)
        return await _tts_edge(text, "", speed)


async def _tts_kokoro(text: str, voice: str | None, speed: float, language: str = ""):
    """TTS via Kokoro-82M ONNX (local, free, high quality)."""
    kokoro = await get_kokoro()
    if kokoro is None:
        raise RuntimeError(
            f"Kokoro model files not found at {KOKORO_MODEL_DIR}. "
            "Download kokoro-v1.0.onnx and voices-v1.0.bin from "
            "https://github.com/thewh1teagle/kokoro-onnx/releases"
        )

    # Resolve voice id
    if not voice:
        lang_key = language[:2].lower() if language else _detect_lang(text)
        voice = KOKORO_DEFAULT_VOICES.get(lang_key, KOKORO_DEFAULT_VOICES["en"])

    # Determine lang code for phonemizer
    lang_code = KOKORO_LANG_BY_PREFIX.get(voice[:2].lower(), "en-us")

    import soundfile as sf
    from pydub import AudioSegment

    wav_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"
    out_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"

    samples, sr = await asyncio.to_thread(
        kokoro.create, text, voice=voice, speed=speed, lang=lang_code
    )
    await asyncio.to_thread(sf.write, str(wav_path), samples, sr)
    # Re-encode to mp3 for consistent consumer expectations
    seg = AudioSegment.from_wav(str(wav_path))
    seg.export(str(out_path), format="mp3")
    try:
        wav_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {"path": str(out_path), "audio_path": str(out_path), "engine": "kokoro", "voice": voice}


async def _tts_edge(text: str, voice: str, speed: float):
    """TTS via edge-tts (fast, built-in voices)."""
    import edge_tts

    voice_info = EDGE_VOICES.get(voice, {})
    voice_name = voice_info.get("edge", "") if voice_info else ""
    if not voice_name:
        # Try direct edge voice name
        voice_name = voice if voice and any(c.isalpha() for c in voice) else "en-US-AriaNeural"

    out_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
    try:
        rate = f"{int((speed - 1) * 100):+d}%" if speed != 1.0 else "+0%"
        communicate = edge_tts.Communicate(text, voice_name, rate=rate)
        await communicate.save(str(out_path))
        return {
            "path": str(out_path),
            "audio_path": str(out_path),
            "engine": "edge",
            "voice": voice_name,
        }
    except Exception as e:
        return JSONResponse({"error": f"edge-tts failed: {e}"}, status_code=500)


async def _tts_xtts(text: str, voice_info: dict, speed: float):
    """TTS via XTTS v2 (GPU voice cloning)."""
    import asyncio

    ref_path = voice_info.get("reference")
    if not ref_path or not Path(ref_path).exists():
        return JSONResponse(
            {"error": f"Voice reference file not found: {ref_path}"}, status_code=500
        )

    language = voice_info.get("language", "en")
    out_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.wav"

    def _generate():
        model = get_xtts()
        model.tts_to_file(
            text=text,
            file_path=str(out_path),
            speaker_wav=ref_path,
            language=language,
            speed=speed,
        )

    try:
        await asyncio.to_thread(_generate)
        return {"path": str(out_path), "audio_path": str(out_path)}
    except Exception as e:
        return JSONResponse({"error": f"XTTS failed: {e}"}, status_code=500)


@app.get("/voices")
async def voices():
    """List all voices (kokoro + edge + custom)."""
    result = []
    kokoro_ok = _kokoro_files_present()
    if kokoro_ok:
        for vid, info in KOKORO_VOICE_CATALOG.items():
            result.append(
                {
                    "id": vid,
                    "name": info["name"],
                    "type": "kokoro",
                    "language": info["language"],
                    "default": vid in KOKORO_DEFAULT_VOICES.values(),
                }
            )

    for vid, info in EDGE_VOICES.items():
        result.append({"id": vid, "name": info["name"], "type": "edge"})

    for vid, info in _load_custom_voices().items():
        result.append(
            {
                "id": vid,
                "name": info.get("name", vid),
                "type": "xtts",
                "language": info.get("language", "en"),
            }
        )

    return {"voices": result}


@app.post("/voices/register")
async def register_voice(
    file: UploadFile = File(...),
    name: str = Form(""),
    voice_id: str = Form(""),
    language: str = Form("en"),
):
    """Register a custom voice by uploading a reference audio sample (5-30s WAV/MP3).

    The voice will be available for TTS via XTTS voice cloning.
    """
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    vid = voice_id or name.lower().replace(" ", "-")
    ext = Path(file.filename or "ref.wav").suffix or ".wav"
    ref_path = VOICES_DIR / f"{vid}{ext}"
    ref_path.write_bytes(await file.read())

    # Verify it's valid audio
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(str(ref_path))
        duration = len(audio) / 1000
        if duration < 3:
            ref_path.unlink()
            return JSONResponse(
                {"error": f"Audio too short ({duration:.1f}s). Need at least 5 seconds."},
                status_code=400,
            )
        if duration > 60:
            # Trim to first 30s
            audio = audio[:30000]
            audio.export(str(ref_path), format=ext.lstrip("."))
            duration = 30
    except Exception as e:
        ref_path.unlink(missing_ok=True)
        return JSONResponse({"error": f"Invalid audio: {e}"}, status_code=400)

    # Register
    custom = _load_custom_voices()
    custom[vid] = {
        "name": name,
        "reference": str(ref_path),
        "language": language,
        "duration": round(duration, 1),
    }
    _save_custom_voices(custom)

    return {"id": vid, "name": name, "language": language, "duration": round(duration, 1)}


@app.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """Remove a custom voice."""
    custom = _load_custom_voices()
    if voice_id not in custom:
        return JSONResponse({"error": "voice not found"}, status_code=404)

    info = custom.pop(voice_id)
    ref = Path(info.get("reference", ""))
    if ref.exists():
        ref.unlink()
    _save_custom_voices(custom)
    return {"deleted": voice_id}


if __name__ == "__main__":
    port = int(os.environ.get("VOICE_API_PORT", "8602"))
    # Default-bind loopback. Override with VOICE_API_HOST=0.0.0.0 if you
    # deliberately want LAN access — pair that with VOICE_API_TOKEN.
    host = os.environ.get("VOICE_API_HOST", "127.0.0.1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    _initial_token = _voice_api_token()
    auth_state = "auth=on" if _initial_token else "auth=off"
    print(f"[Voice API] {host}:{port} ({auth_state}) | Whisper={WHISPER_MODEL}")
    if host != "127.0.0.1" and not _initial_token:
        print(
            "[Voice API] WARNING: bound non-loopback without VOICE_API_TOKEN — anyone on the network can call this service."
        )
    if _kokoro_files_present():
        print(
            f"[Voice API] Kokoro: {len(KOKORO_VOICE_CATALOG)} curated voices (default engine) | {KOKORO_MODEL_DIR}"
        )
    else:
        print("[Voice API] Kokoro: model files missing — falling back to edge-tts as default")
        print(f"[Voice API]         expected at: {KOKORO_MODEL_DIR}")
    print(f"[Voice API] Edge: {len(EDGE_VOICES)} voices (fallback) | Custom dir: {VOICES_DIR}")
    print(f"[Voice API] Audio: {AUDIO_DIR}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
