"""Local pronounce provider — HTTP client to the pronounce-api service.

Mirrors the voice-api/Whisper provider shape: cached /health probe, lazy
session, structured recovery hints for the Capability Inspector.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import aiohttp

from emptyos.capabilities import Provider


class LocalPronounceProvider(Provider):
    """Pronunciation scoring via the local pronounce-api service on :8603.

    `audio` may be a path (str | Path) or raw bytes. The service expects
    base64-encoded audio inside a JSON body — we encode here so the
    capability contract stays "pass the bytes, get back a dict".
    """

    name = "local"

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.host = plugin._host() if hasattr(plugin, "_host") else "http://127.0.0.1:8603"

    async def available(self) -> bool:
        health = await self.plugin.health()
        return bool(health) and bool(health.get("ready"))

    async def health(self) -> dict:
        h = await self.plugin.health()
        if not h:
            return {
                "available": False,
                "reason": f"pronounce-api service unreachable at {self.host}",
                "recovery": {
                    "kind": "plugin",
                    "id": "pronounce",
                    "launcher": f"Start the pronounce plugin or service at {self.host}",
                },
            }
        state = h.get("model_state", "idle")
        if state in ("downloading", "loading"):
            return {
                "available": False,
                "reason": (
                    f"pronunciation model is {state}"
                    + (f" — {h.get('model_detail')}" if h.get("model_detail") else "")
                ),
                "recovery": {
                    "kind": "service",
                    "id": "pronounce",
                    "url": self.host,
                    "hint": "Wait for the first-run model download to complete (~1.2 GB).",
                },
            }
        if state == "error":
            return {
                "available": False,
                "reason": f"pronunciation model failed to load: {h.get('model_detail', '')}",
                "recovery": {
                    "kind": "service",
                    "id": "pronounce",
                    "url": self.host,
                    "hint": "Check the service log; the model files may be missing or torch is misconfigured.",
                },
            }
        if not h.get("ready"):
            return {
                "available": False,
                "reason": "pronunciation model not ready yet",
                "recovery": None,
            }
        return {"available": True, "reason": None, "recovery": None}

    async def execute(
        self,
        *,
        audio: str | bytes | Path,
        reference_text: str,
        language: str = "en-us",
        **_: Any,
    ) -> dict:
        if isinstance(audio, (str, Path)):
            raw = Path(audio).read_bytes()
        elif isinstance(audio, bytes):
            raw = audio
        else:
            raise TypeError(f"audio must be path or bytes, got {type(audio).__name__}")

        payload = {
            "audio_b64": base64.b64encode(raw).decode("ascii"),
            "reference_text": reference_text,
            "language": language,
        }
        return await self.plugin.score(payload)
