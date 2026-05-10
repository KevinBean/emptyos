"""Voice API plugin — registers separate providers per engine.

The local voice-api service (`services/voice-api/server.py`, default :8602)
hosts three engines. This plugin exposes each as its own capability provider
so the speak/listen capabilities fall back independently:

    speak  → kokoro (local Kokoro ONNX) + xtts (GPU voice cloning)
    listen → whisper (faster-whisper)

Each provider reads the service's /health to decide if it's available.
If Kokoro files are missing the kokoro provider reports unavailable but
xtts/whisper can still work — and vice versa.
"""

from __future__ import annotations

import aiohttp

from emptyos.capabilities import Provider
from emptyos.sdk import BasePlugin


class VoiceAPIPlugin(BasePlugin):
    name = "voice_api"

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._session: aiohttp.ClientSession | None = None
        self._embedded_proc = None
        self._health_cache: dict = {}
        self._health_ts: float = 0.0

    def _host(self) -> str:
        # 8602 is the EmptyOS voice service default. 8601 is legacy home-portal TalkBuddy.
        return self.config("host", "http://127.0.0.1:8602")

    def _token(self) -> str:
        """Optional bearer token for the voice-api server.

        Read from plugin config first, then VOICE_API_TOKEN env var. The
        service-side middleware accepts requests without a token only when
        no token is configured server-side, so this stays opt-in."""
        import os as _os

        return (
            self.config("auth_token", "") or _os.environ.get("VOICE_API_TOKEN", "") or ""
        ).strip()

    def _auth_headers(self) -> dict:
        return self.bearer_headers(self._token())

    async def connect(self):
        self._session = aiohttp.ClientSession()

        if not await self._service_up():
            try:
                await self._start_embedded()
            except Exception as e:
                print(f"[voice-api] Could not start embedded server: {e}")

        if await self._service_up():
            print(f"[voice-api] Connected to {self._host()}")

        # Register one provider per engine. Each reports its own availability
        # based on the /health response, so a disabled engine doesn't starve
        # the others.
        speak = self.kernel.capabilities.get("speak")
        listen = self.kernel.capabilities.get("listen")
        speak.add_provider(KokoroTTSProvider(self))
        speak.add_provider(XTTSProvider(self))
        listen.add_provider(WhisperSTTProvider(self))

    async def _start_embedded(self):
        """Start the voice API server as a subprocess."""
        import asyncio
        import os
        import sys
        from pathlib import Path
        from urllib.parse import urlparse

        server_path = Path(__file__).parent.parent.parent / "services" / "voice-api" / "server.py"
        if not server_path.exists():
            raise FileNotFoundError(f"Voice server not found: {server_path}")

        env = os.environ.copy()
        parsed = urlparse(self._host())
        if parsed.port:
            env["VOICE_API_PORT"] = str(parsed.port)

        self._embedded_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(server_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        for _ in range(10):
            await asyncio.sleep(0.5)
            if await self._service_up():
                return
        raise RuntimeError("Embedded voice server didn't start in time")

    async def disconnect(self):
        if self._embedded_proc and self._embedded_proc.returncode is None:
            self._embedded_proc.terminate()
            self._embedded_proc = None
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def _service_up(self) -> bool:
        """Bare ping — is the voice-api HTTP server responding at all?"""
        try:
            async with self._session.get(
                f"{self._host()}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def health(self) -> dict:
        """Cached /health response. TTL 5s — enough to de-bounce a burst of
        `available()` checks from the capability layer without stale state."""
        import time

        now = time.monotonic()
        if self._health_cache and (now - self._health_ts) < 5.0:
            return self._health_cache
        try:
            async with self._session.get(
                f"{self._host()}/health",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json()
                self._health_cache = data
                self._health_ts = now
                return data
        except Exception:
            return {}

    @staticmethod
    def _detect_language(text: str) -> str:
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return "zh" if cjk > len(text) * 0.1 else "en"

    async def _tts_post(self, payload: dict) -> str:
        async with self._session.post(
            f"{self._host()}/tts",
            json=payload,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"TTS failed: {resp.status}")
            data = await resp.json()
            return data.get("path", data.get("audio_path", ""))

    async def voices(self) -> list[dict]:
        """List all voices exposed by the underlying voice-api service
        (kokoro, edge, and any registered xtts clones). Returns [] if the
        service is unreachable."""
        try:
            async with self._session.get(
                f"{self._host()}/voices",
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("voices", []) if isinstance(data, dict) else (data or [])
        except Exception:
            return []

    async def stt(self, audio, language: str = "en") -> str:
        """Speech to text. Accepts file path or bytes."""
        data = aiohttp.FormData()
        if isinstance(audio, str):
            # Brief blocking open — aiohttp streams the file from here.
            data.add_field("file", open(audio, "rb"), filename="audio.wav")  # noqa: ASYNC230
        else:
            data.add_field("file", audio, filename="audio.wav")
        if language:
            data.add_field("language", language)
        async with self._session.post(
            f"{self._host()}/stt",
            data=data,
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            result = await resp.json()
            return result.get("text", "")


class KokoroTTSProvider(Provider):
    """Local Kokoro ONNX TTS. High quality, CPU-friendly, no API key."""

    name = "kokoro"

    def __init__(self, plugin: VoiceAPIPlugin):
        self.plugin = plugin
        self.host = plugin._host()

    async def available(self) -> bool:
        health = await self.plugin.health()
        return bool(health) and health.get("kokoro_available", False)

    async def health(self) -> dict:
        h = await self.plugin.health()
        if not h:
            return {
                "available": False,
                "reason": f"voice-api service unreachable at {self.host}",
                "recovery": {
                    "kind": "plugin",
                    "id": "voice-api",
                    "launcher": "Start the voice-api plugin or service at " + self.host,
                },
            }
        if not h.get("kokoro_available"):
            return {
                "available": False,
                "reason": "voice-api is up but Kokoro TTS engine is not loaded",
                "recovery": {
                    "kind": "service",
                    "id": "voice-api",
                    "url": self.host,
                    "hint": "Install Kokoro ONNX files in the voice-api service directory",
                },
            }
        return {"available": True, "reason": None, "recovery": None}

    async def execute(
        self, *, text: str, voice: str = "", speed: float = 1.0, language: str = "", **_
    ) -> str:
        if not language:
            language = VoiceAPIPlugin._detect_language(text)
        # If caller passed a non-Kokoro voice (e.g. edge alias), fall back to
        # the default Kokoro voice for the detected language — never route
        # through a different engine from inside this provider.
        payload: dict = {"text": text, "language": language}
        if voice and voice.startswith("kokoro:"):
            payload["voice"] = voice
        elif voice and "_" in voice and not voice.startswith("en-"):
            # Raw Kokoro id pattern (e.g. "af_heart", "zf_xiaoxiao") — the
            # language-code prefix disambiguates from edge voices like "en-US-...".
            payload["voice"] = voice
        if speed != 1.0:
            payload["speed"] = speed
        return await self.plugin._tts_post(payload)


class XTTSProvider(Provider):
    """GPU voice cloning via XTTS v2. Requires a registered custom voice."""

    name = "xtts"

    def __init__(self, plugin: VoiceAPIPlugin):
        self.plugin = plugin
        self.host = plugin._host()

    async def available(self) -> bool:
        health = await self.plugin.health()
        if not health:
            return False
        # XTTS is only meaningful with a reference voice. Also accept a
        # pre-loaded model (enables calling with fresh voices registered later).
        return bool(health.get("xtts_loaded")) or (health.get("custom_voices", 0) > 0)

    async def execute(self, *, text: str, voice: str = "", **_) -> str:
        if not voice:
            raise RuntimeError("xtts requires a custom voice id")
        return await self.plugin._tts_post({"text": text, "voice": voice})


class WhisperSTTProvider(Provider):
    """Local faster-whisper STT."""

    name = "whisper"

    def __init__(self, plugin: VoiceAPIPlugin):
        self.plugin = plugin
        self.host = plugin._host()

    async def available(self) -> bool:
        health = await self.plugin.health()
        return bool(health) and bool(health.get("whisper"))

    async def health(self) -> dict:
        h = await self.plugin.health()
        if not h:
            return {
                "available": False,
                "reason": f"voice-api service unreachable at {self.host}",
                "recovery": {
                    "kind": "plugin",
                    "id": "voice-api",
                    "launcher": "Start the voice-api plugin or service at " + self.host,
                },
            }
        if not h.get("whisper"):
            return {
                "available": False,
                "reason": "voice-api is up but Whisper STT engine is not loaded",
                "recovery": {
                    "kind": "service",
                    "id": "voice-api",
                    "url": self.host,
                    "hint": "Install faster-whisper in the voice-api service environment",
                },
            }
        return {"available": True, "reason": None, "recovery": None}

    async def execute(self, *, audio, language: str = "en", **_) -> str:
        return await self.plugin.stt(audio, language=language)
