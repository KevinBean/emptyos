"""Webcam plugin — local camera capture via OpenCV.

Registers a `WebcamSeeProvider` on the `see` capability and exposes a
`webcam` service for apps that want direct snapshot access via
`self.require("webcam").snapshot()`.

OpenCV is imported lazily inside `available()` / `execute()` — if `cv2`
is missing the provider reports unavailable and the capability falls
back to the next provider (ultimately `HumanSeeProvider`).

Purely local (no HTTP host), so `is_cloud` stays False and the consent
gate is not involved.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import uuid
from functools import partial
from pathlib import Path

from emptyos.sdk import BasePlugin
from emptyos.capabilities import Provider

CAPTURE_DIR = Path(tempfile.gettempdir()) / "emptyos-camera"
CAPTURE_DIR.mkdir(exist_ok=True)


def _cv_backend_const(name: str) -> int:
    """Map a friendly backend name to cv2's backend constant. Unknown → CAP_ANY."""
    import cv2
    return {
        "any": cv2.CAP_ANY,
        "dshow": cv2.CAP_DSHOW,
        "msmf": cv2.CAP_MSMF,
        "v4l2": cv2.CAP_V4L2,
        "avfoundation": cv2.CAP_AVFOUNDATION,
    }.get(name.strip().lower(), cv2.CAP_ANY)


class WebcamPlugin(BasePlugin):
    name = "webcam"
    _resolved: tuple[int, int] | None = None

    async def connect(self):
        see = self.kernel.capabilities.get("see")
        see.add_provider(WebcamSeeProvider(self), priority=0)

    async def available(self) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    def _resolution(self) -> tuple[int, int]:
        return int(self.config("width", 1280)), int(self.config("height", 720))

    def _warmup_frames(self) -> int:
        # Most sensors return a few dark/auto-exposing frames before
        # settling. Skip them so snapshots look like what the user sees.
        return max(0, int(self.config("warmup_frames", 3)))

    def _resolve_capture(self) -> tuple[int, int]:
        """Return ``(device_index, cv2_backend_const)``.

        Probes once and caches. Windows defaults are non-obvious: the MSMF
        backend often picks up a virtual camera (NVIDIA Broadcast, OBS vcam)
        at device 0 that opens but never yields frames, while the real USB
        webcam is at device 1 and only works on DSHOW. So on Windows with no
        explicit ``device`` configured, we probe DSHOW for the first index
        that actually returns a frame. Linux/macOS keep the simple default
        (index 0, CAP_ANY) unless the user overrides.
        """
        if self._resolved is not None:
            return self._resolved

        import cv2

        explicit_device = int(self.config("device", -1))
        backend_name = str(self.config("backend", "")).strip()

        if explicit_device >= 0:
            backend = _cv_backend_const(backend_name) if backend_name else cv2.CAP_ANY
            self._resolved = (explicit_device, backend)
            return self._resolved

        if sys.platform == "win32":
            preferred = _cv_backend_const(backend_name) if backend_name else cv2.CAP_DSHOW
            for idx in range(4):
                cap = cv2.VideoCapture(idx, preferred)
                try:
                    if not cap.isOpened():
                        continue
                    for _ in range(3):
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            self._resolved = (idx, preferred)
                            return self._resolved
                finally:
                    cap.release()

        self._resolved = (0, cv2.CAP_ANY)
        return self._resolved

    async def snapshot(self) -> str:
        """Grab one frame from the default camera. Returns JPEG path."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._snapshot_sync))

    def _snapshot_sync(self) -> str:
        import cv2  # lazy: plugin loads even when cv2 is missing

        device, backend = self._resolve_capture()
        width, height = self._resolution()
        warmup = self._warmup_frames()

        cap = cv2.VideoCapture(device, backend)
        if not cap.isOpened():
            raise RuntimeError(f"Camera device {device} (backend={backend}) could not be opened")
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            for _ in range(warmup):
                cap.read()
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(f"Camera device {device} (backend={backend}) returned no frame")
            out_path = CAPTURE_DIR / f"cam_{uuid.uuid4().hex[:8]}.jpg"
            if not cv2.imwrite(str(out_path), frame):
                raise RuntimeError(f"Failed to write snapshot to {out_path}")
            return str(out_path)
        finally:
            cap.release()


class WebcamSeeProvider(Provider):
    """Local webcam — fulfils `see` by grabbing one frame."""

    name = "webcam"

    def __init__(self, plugin: WebcamPlugin):
        self.plugin = plugin

    async def available(self) -> bool:
        return await self.plugin.available()

    async def execute(self, *, mode: str = "snapshot", **_) -> str:
        if mode != "snapshot":
            raise ValueError(f"webcam provider only supports mode='snapshot', got {mode!r}")
        return await self.plugin.snapshot()
