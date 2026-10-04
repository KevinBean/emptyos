"""OpenAI TTS provider — speak capability via OpenAI /audio/speech.

Cloud provider. Subject to the capability layer's consent gate (see
`Capability._consent_allows`). The API key must be set in the environment.
Audio is written to the shared TTS dir so served audio URLs work the same way.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import aiohttp

from emptyos.capabilities import Provider, spend_cap
from emptyos.capabilities.audio import AUDIO_DIR

# Published per-unit prices, transcribed from OpenAI's pricing page
# (developers.openai.com/api/docs/pricing, read 2026-10-03). A model not listed
# here cannot price its calls: the spend cap still stops it at the limit but
# cannot count it, and says so once (spend_cap.charge).
TTS_USD_PER_MILLION_CHARS = {"tts-1": 15.00, "tts-1-hd": 30.00}
STT_USD_PER_MINUTE = {"whisper-1": 0.006}

# Simple aliases so callers don't need to know OpenAI voice names.
VOICE_ALIASES = {
    "sarah": "alloy",
    "michael": "onyx",
    "emma": "nova",
    "george": "echo",
}


class OpenAITTSProvider(Provider):
    """Cloud TTS via OpenAI /v1/audio/speech."""

    name = "openai-tts"

    def __init__(
        self,
        host: str = "https://api.openai.com",
        model: str = "tts-1",
        voice: str = "alloy",
        api_key_env: str = "OPENAI_API_KEY",
        timeout: int = 30,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.default_voice = voice
        self.api_key_env = api_key_env
        self.timeout = timeout

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    async def available(self) -> bool:
        return bool(self._api_key())

    async def execute(self, *, text: str, voice: str = "", speed: float = 1.0, **_) -> str:
        voice = VOICE_ALIASES.get(voice, voice) or self.default_voice
        payload: dict = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
        }
        if speed != 1.0:
            payload["speed"] = max(0.25, min(4.0, speed))

        spend_cap.check_paid_call(self, "speak")
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        out_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/v1/audio/speech",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"OpenAI TTS failed: {resp.status} {body[:200]}")
                out_path.write_bytes(await resp.read())
        spend_cap.charge(self, "speak", self.cost_usd(text))
        return str(out_path)

    def cost_usd(self, text: str) -> float | None:
        """What sending *text* costs: billed per input character."""
        rate = TTS_USD_PER_MILLION_CHARS.get(self.model)
        return None if rate is None else len(text) * rate / 1_000_000


class OpenAIWhisperSTTProvider(Provider):
    """Cloud STT via OpenAI /v1/audio/transcriptions (whisper-1)."""

    name = "openai-whisper"

    def __init__(
        self,
        host: str = "https://api.openai.com",
        model: str = "whisper-1",
        api_key_env: str = "OPENAI_API_KEY",
        timeout: int = 60,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    async def available(self) -> bool:
        return bool(self._api_key())

    def consent_summary(self, **kwargs) -> str:
        """Name the audio being sent. The base summary reads only text kwargs,
        so a consent prompt for a file would otherwise show nothing."""
        audio = kwargs.get("audio")
        if isinstance(audio, (str, Path)):
            p = Path(str(audio))
            size = p.stat().st_size if p.is_file() else None
            return f"audio file {p.name}" + (f" ({size:,} bytes)" if size is not None else "")
        if isinstance(audio, (bytes, bytearray)):
            return f"audio data ({len(audio):,} bytes)"
        return ""

    async def execute(self, *, audio, language: str = "", timestamps: bool = False, **_) -> str | dict:
        """Transcribe ``audio``. Returns the text, or with ``timestamps=True``
        a dict ``{text, language, duration, segments, words}`` where each
        segment is ``{start, end, text}`` and each word ``{start, end, word}``.
        """
        spend_cap.check_paid_call(self, "listen")
        data = aiohttp.FormData()
        data.add_field("model", self.model)
        if language:
            data.add_field("language", language)
        # verbose_json is the only form that reports the audio's duration,
        # which a paid call is billed on, so a metered host gets it even for
        # plain text (the text is the same either way). A self-hosted
        # compatible server is not charged and keeps the plain request, which
        # some such servers do not support otherwise.
        if timestamps or spend_cap.is_metered(self):
            data.add_field("response_format", "verbose_json")
        if timestamps:
            data.add_field("timestamp_granularities[]", "word")
            data.add_field("timestamp_granularities[]", "segment")
        if isinstance(audio, (str, Path)):
            # Brief blocking open — aiohttp streams the file from here. # noqa: ASYNC230
            data.add_field("file", open(str(audio), "rb"), filename=Path(str(audio)).name)  # noqa: ASYNC230
        else:
            data.add_field("file", audio, filename="audio.wav")

        headers = {"Authorization": f"Bearer {self._api_key()}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.host}/v1/audio/transcriptions",
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"OpenAI STT failed: {resp.status} {body[:200]}")
                result = await resp.json()
        spend_cap.charge(self, "listen", self.cost_usd(result.get("duration")))
        if not timestamps:
            return result.get("text", "")
        return {
            "text": result.get("text", ""),
            "language": result.get("language", ""),
            "duration": (round(float(result["duration"]), 3)
                         if result.get("duration") is not None else None),
            "segments": [
                {"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
                 "text": (s.get("text") or "").strip()}
                for s in result.get("segments") or []
            ],
            "words": [
                {"start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3),
                 "word": w.get("word", "")}
                for w in result.get("words") or []
            ],
        }

    def cost_usd(self, duration_s) -> float | None:
        """What transcribing *duration_s* seconds costs: billed per minute of
        audio. None when the model or the duration is unknown."""
        rate = STT_USD_PER_MINUTE.get(self.model)
        try:
            seconds = float(duration_s)
        except (TypeError, ValueError):
            return None
        if rate is None or seconds < 0:
            return None
        return seconds / 60 * rate
