"""Edge TTS plugin — text-to-speech via Microsoft's Edge voices.

No subprocess and no API key: the `edge-tts` pip package runs in-process. But
the SYNTHESIS is not local — `Communicate` opens a WSS connection to
Microsoft's speech endpoint and the text is sent there, so this provider
declares `trust = "service"` and answers to the cloud-consent gate. An earlier
version of this docstring said "no external service ... always available
offline-free", which is false in both halves and is very likely why nobody
declared `trust` for so long: `is_cloud` reads an undeclared, hostless provider
as LOCAL, so every line of text sent here bypassed the gate.

Registers at priority 0, which orders it within whatever chain the machine
configures; `[capabilities.speak] providers` decides the actual order.
"""

from __future__ import annotations

import uuid

from emptyos.capabilities import Provider
from emptyos.capabilities.audio import AUDIO_DIR
from emptyos.sdk import BasePlugin

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


# One rule, shared with BaseApp.speak's provider routing. If the two disagree,
# speak() can steer a Chinese line here and this module then picks an English
# voice for it \u2014 the same garbled output, one layer further on.
from emptyos.speechlang import detect_speech_language as _detect_language  # noqa: E402


class EdgeTTSPlugin(BasePlugin):
    name = "edge_tts"

    async def connect(self):
        speak = self.kernel.capabilities.get("speak")
        speak.add_provider(EdgeTTSProvider(), priority=0)


class EdgeTTSProvider(Provider):
    """Microsoft Edge TTS — free, no API key, but NOT local.

    ``in-process`` describes where the client library runs, not where the
    synthesis happens: ``edge_tts.Communicate`` opens a WSS connection to
    Microsoft's speech endpoint and the text is sent there. Declaring
    ``trust`` is what makes ``is_cloud`` say so — the inference path reads an
    empty ``host`` as ``False`` (local), so without this line every line of
    text sent here skipped the consent gate, and ``tts_cache._speak_local``
    (whose whole contract is "never send this text off the box") would happily
    pick this provider. See CLAUDE.md rule 18 and rented-compute.md.
    """

    name = "edge-tts"
    trust = "service"

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
                "recovery": {
                    "kind": "service",
                    "id": "edge-tts",
                    "url": "",
                    "hint": "Run `pip install edge-tts` — free in-process TTS, no API key",
                },
            }

    async def execute(
        self, *, text: str, voice: str = "", speed: float = 1.0, language: str = "", **_
    ) -> str:
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
