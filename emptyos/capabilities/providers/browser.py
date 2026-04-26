"""Browser-side capability providers.

These providers don't run server-side — they ask a connected browser to
capture audio (Web Speech API for STT) or images (getUserMedia for camera)
and stream the result back over the WebSocket realtime channel.

Why: a containerized public demo has no microphone or camera attached to
the server. The visitor's device does. This pattern lets the daemon use
the visitor's hardware without shipping audio bytes server-side.

Limitations:
- Requires a browser tab open AND connected to the realtime WebSocket
- Requires the visitor to grant permission (one-time per origin per
  capability — browser handles the prompt)
- listen uses Web Speech API which has no AEC, so half-duplex is enforced:
  the browser-side handler pauses any TTS playback while capturing
- see returns a single frame (snapshot mode); video streaming would be a
  different provider

Falls through gracefully when no browser is connected — the provider's
available() returns False and the capability chain moves to the next
provider (typically `human` for typed input).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from emptyos.capabilities import Provider

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


class BrowserListenProvider(Provider):
    """Capture transcribed speech from the visitor's browser via Web Speech API.

    Free, runs in Chrome / Edge / Safari (with prefix). Quality varies by
    browser/OS — Chrome's is decent for general English.
    """

    name = "browser-speech"
    is_cloud = False

    def __init__(self, kernel: Kernel, default_lang: str = "en-US", default_timeout: float = 30.0):
        self.kernel = kernel
        self.default_lang = default_lang
        self.default_timeout = default_timeout

    def _realtime(self):
        # Realtime is registered as a service by the kernel boot; tolerate it
        # being absent in test/CLI contexts.
        return getattr(self.kernel, "realtime", None)

    async def available(self) -> dict:
        rt = self._realtime()
        if rt is None:
            return {
                "available": False,
                "reason": "realtime service not running",
                "recovery": {"kind": "service", "id": "realtime",
                             "hint": "Realtime is core; this shouldn't normally happen"},
            }
        if rt.client_count == 0:
            return {
                "available": False,
                "reason": "no browser tab connected to /ws",
                "recovery": {"kind": "human", "id": "browser",
                             "hint": "Open the EmptyOS web UI in a browser tab"},
            }
        return {"available": True, "reason": None, "recovery": None}

    async def execute(
        self, *,
        prompt: str = "",
        language: str = "",
        timeout: float = 0,
        **kwargs,
    ) -> str:
        """Send a capture_request to the browser, return the transcribed text.

        prompt   — optional instruction shown to the user (e.g. "Say a question")
        language — BCP-47 language tag for SpeechRecognition (e.g. 'en-US', 'zh-CN')
        timeout  — seconds to wait before giving up
        """
        rt = self._realtime()
        if rt is None or rt.client_count == 0:
            raise RuntimeError("browser-speech: no browser connected")

        try:
            response = await rt.request_capture(
                capability="listen",
                mode="speech",
                prompt=prompt,
                language=language or self.default_lang,
                timeout=timeout or self.default_timeout,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("browser-speech: timed out waiting for browser to capture")

        if response.get("error"):
            raise RuntimeError(f"browser-speech: {response['error']}")

        text = (response.get("text") or "").strip()
        return text


class BrowserSeeProvider(Provider):
    """Single-frame snapshot from the visitor's browser via getUserMedia.

    Returns a base64-encoded image data URL. Apps that need raw bytes can
    decode it (data URL parsing is in emptyos.sdk.utils.parse_data_url).

    NOT YET WIRED into setup.py — landing in v0.3.0 once the protocol +
    UI affordance for camera permission stabilize. Class is here so the
    contract is reviewable now.
    """

    name = "browser-webcam"
    is_cloud = False

    def __init__(self, kernel: Kernel, default_timeout: float = 30.0):
        self.kernel = kernel
        self.default_timeout = default_timeout

    def _realtime(self):
        return getattr(self.kernel, "realtime", None)

    async def available(self) -> dict:
        rt = self._realtime()
        if rt is None or rt.client_count == 0:
            return {"available": False, "reason": "no browser tab connected to /ws"}
        return {"available": True}

    async def execute(self, *, mode: str = "snapshot", prompt: str = "", **kwargs) -> str:
        rt = self._realtime()
        if rt is None or rt.client_count == 0:
            raise RuntimeError("browser-webcam: no browser connected")
        response = await rt.request_capture(
            capability="see", mode=mode, prompt=prompt, timeout=self.default_timeout,
        )
        if response.get("error"):
            raise RuntimeError(f"browser-webcam: {response['error']}")
        return response.get("image") or ""
