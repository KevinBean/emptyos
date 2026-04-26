"""Edge TTS plugin — in-process text-to-speech via Microsoft Edge voices.

No subprocess, no external service, no API key. Uses the `edge-tts` pip
package directly. Registers at priority 0 (tried first in the speak chain)
because it's the fastest cold-start option and always available offline-free.
"""

from __future__ import annotations

import uuid

from emptyos.sdk import BasePlugin
from emptyos.capabilities import Provider
from emptyos.capabilities.audio import AUDIO_DIR

# Short aliases → full Edge voice names. Matches the aliases used by the
# voice-api service so callers can swap providers without changing voice ids.
VOICE_ALIASES = {
    "emma": "en-GB-SoniaNeural",
    "michael": "en-US-GuyNeural",
    "sarah": "en-US-JennyNeural",
    "george": "en-GB-RyanNeural",
}

DEFAULT_VOICES = {
    "en": "en-US-JennyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
}


def _detect_language(text: str) -> str:
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    ja = sum(1 for c in text if '\u3040' <= c <= '\u30ff')
    if ja > len(text) * 0.1:
        return "ja"
    if cjk > len(text) * 0.1:
        return "zh"
    return "en"


class EdgeTTSPlugin(BasePlugin):
    name = "edge_tts"

    async def connect(self):
        speak = self.kernel.capabilities.get("speak")
        speak.add_provider(EdgeTTSProvider(), priority=0)


class EdgeTTSProvider(Provider):
    """Microsoft Edge TTS — in-process, free, no API key."""

    name = "edge-tts"

    async def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    async def health(self) -> dict:
        try:
            import edge_tts  # noqa: F401
            return {"available": True, "reason": None, "recovery": None}
        except ImportError:
            return {
                "available": False,
                "reason": "edge-tts package not installed",
                "recovery": {"kind": "service", "id": "edge-tts", "url": "",
                             "hint": "Run `pip install edge-tts` — free in-process TTS, no API key"},
            }

    async def execute(self, *, text: str, voice: str = "", speed: float = 1.0, language: str = "", **_) -> str:
        import edge_tts

        if voice in VOICE_ALIASES:
            voice = VOICE_ALIASES[voice]
        if not voice or not voice.startswith(("en-", "zh-", "ja-", "es-", "fr-", "de-")):
            if not language:
                language = _detect_language(text)
            voice = DEFAULT_VOICES.get(language, DEFAULT_VOICES["en"])

        # edge-tts accepts rate as "+N%" / "-N%"; convert speed multiplier.
        rate = f"{int((speed - 1.0) * 100):+d}%" if speed != 1.0 else "+0%"
        out_path = AUDIO_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(out_path))
        return str(out_path)
