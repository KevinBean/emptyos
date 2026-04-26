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

from emptyos.capabilities import Provider
from emptyos.capabilities.audio import AUDIO_DIR

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
        return str(out_path)


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

    async def execute(self, *, audio, language: str = "", **_) -> str:
        data = aiohttp.FormData()
        data.add_field("model", self.model)
        if language:
            data.add_field("language", language)
        if isinstance(audio, (str, Path)):
            data.add_field("file", open(str(audio), "rb"), filename=Path(str(audio)).name)
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
                return result.get("text", "")
