"""Meeting Capture plugin — local system-audio (loopback) + mic recording.

Fills the one gap EmptyOS's `listen` capability doesn't cover: capturing the
audio of a live call (Zoom/Teams/in-person) — *both sides* — by tapping the
system output (WASAPI loopback) alongside the mic. Everything downstream is
already built: the recorded WAV feeds the existing `braindump` pipeline
(transcribe → summarize → extract actions → review), so this plugin only owns
the capture, never transcription or summary.

Registers a `meeting-capture` service (the plugin instance itself). The service
API is `start(sources) → {ok, session_id}`, `stop() → {ok, wav_path, duration_s}`,
`status()`, `devices()`.

`soundcard` (pure-cffi, cross-platform WASAPI/CoreAudio/PulseAudio loopback) is
imported lazily inside `available()` / capture threads — if it's missing the
service reports unavailable and braindump hides the Meeting surface. Purely local
(no HTTP host) → `is_cloud` stays False; the consent gate is not involved. The
real gate is the app-level dark flag + the explicit start/stop (triggered, never
ambient — mirrors braindump's own line against a surveillance recorder).

The capture threads accumulate 16-bit PCM per source; `stop()` mixes them into a
single mono 16 kHz WAV via the pure `emptyos.sdk.audio.mix_streams_to_wav`
(unit-tested without real audio).
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from emptyos.sdk import BasePlugin
from emptyos.sdk.audio import mix_streams_to_wav

CAPTURE_DIR = Path(tempfile.gettempdir()) / "emptyos-meeting"
CAPTURE_DIR.mkdir(exist_ok=True)

# Labels the UI + service speak; "loopback" = everyone else on the call,
# "mic" = you. Order matters only for the human-readable device list.
VALID_SOURCES = ("loopback", "mic")


class MeetingCapturePlugin(BasePlugin):
    name = "meeting-capture"
    _session: dict | None = None
    _avail: bool | None = None

    async def connect(self):
        # Service-only plugin — the loader registers this instance under the
        # `meeting-capture` service name from the manifest. No capability provider.
        self._session = None

    async def available(self) -> bool:
        """Probe soundcard once, off the event loop, with a bound.

        A bare ``import soundcard`` here wedged the whole daemon on
        2026-08-01. Two facts make this import loop-hostile:
        ``soundcard/mediafoundation.py`` calls ``platform.win32_ver()`` at
        *module scope*, which shells out to ``cmd /c ver``; and the health
        plugin's watchdog awaits ``available()`` on every plugin each tick, so
        it runs on the event loop. py-spy caught the main thread parked in
        ``subprocess.communicate`` inside this import
        (``data/wedge-evidence/20260801T080139Z``).

        Why it stalled that once is **not** established — the import measures
        0.1s standalone, both console-attached and DETACHED_PROCESS, so the
        obvious console-less explanation was tested and did not reproduce. It
        does not need to be established: spawning a subprocess on the loop is
        a wedge whenever it is slow for any reason, and the tick rate means
        a single stall takes the whole event bus with it.

        So: never import on the loop, and never let a slow probe outlive the
        tick. A probe that cannot answer is not an available device.
        """
        if self._avail is None:
            def _probe() -> bool:
                try:
                    import soundcard  # noqa: F401
                except Exception:
                    return False
                return True

            try:
                self._avail = await asyncio.wait_for(
                    asyncio.to_thread(_probe), timeout=10,
                )
            except (TimeoutError, asyncio.TimeoutError):
                self._avail = False
        return self._avail

    # ── config knobs ────────────────────────────────────────────────────
    def _samplerate(self) -> int:
        return int(self.config("samplerate", 16000))

    def _blocksize(self) -> int:
        return max(512, int(self.config("blocksize", 4000)))

    def _max_frames(self) -> int:
        mins = float(self.config("max_minutes", 120))
        return int(mins * 60 * self._samplerate()) if mins > 0 else 0

    def _default_sources(self) -> list[str]:
        raw = self.config("default_sources", ["loopback", "mic"])
        if isinstance(raw, str):
            raw = [s.strip() for s in raw.split(",")]
        return [s for s in raw if s in VALID_SOURCES] or ["loopback", "mic"]

    # ── device resolution (lazy; opens the audio subsystem) ──────────────
    def _resolve_mic(self, source: str):
        """Return a soundcard Microphone for a source, or raise with a clear msg."""
        import soundcard as sc

        if source == "loopback":
            spk = sc.default_speaker()
            if spk is None:
                raise RuntimeError("no default speaker to loop back")
            return sc.get_microphone(spk.name, include_loopback=True)
        if source == "mic":
            mic = sc.default_microphone()
            if mic is None:
                raise RuntimeError("no default microphone")
            return mic
        raise ValueError(f"unknown source {source!r}")

    async def devices(self) -> dict:
        """UI probe: which sources are actually resolvable on this machine."""
        if not await self.available():
            return {"available": False, "sources": []}

        def _probe() -> dict:
            import soundcard as sc

            out: dict[str, Any] = {"available": True, "sources": [], "recording": self._session is not None}
            try:
                spk = sc.default_speaker()
                if spk is not None:
                    self._resolve_mic("loopback")  # raises if loopback unsupported
                    out["sources"].append({"id": "loopback", "label": f"System audio · {spk.name}"})
            except Exception as e:
                out["loopback_error"] = str(e)
            try:
                mic = sc.default_microphone()
                if mic is not None:
                    out["sources"].append({"id": "mic", "label": f"Microphone · {mic.name}"})
            except Exception as e:
                out["mic_error"] = str(e)
            return out

        return await asyncio.to_thread(_probe)

    # ── capture thread ──────────────────────────────────────────────────
    def _capture_thread(self, mic, slot: dict, stop: threading.Event,
                        samplerate: int, block: int, max_frames: int):
        import numpy as np

        try:
            with mic.recorder(samplerate=samplerate, blocksize=block) as rec:
                while not stop.is_set():
                    data = rec.record(numframes=block)  # (n, ch) float32 in [-1, 1]
                    if getattr(data, "ndim", 1) == 2 and data.shape[1] > 1:
                        mono = data.mean(axis=1)          # downmix stereo loopback
                    else:
                        mono = np.asarray(data).reshape(-1)
                    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2")
                    slot["chunks"].append(pcm.tobytes())
                    slot["frames"] += pcm.size
                    if max_frames and slot["frames"] >= max_frames:
                        stop.set()  # honour the cap; other threads see it too
        except Exception as e:  # a dead source shouldn't kill the whole capture
            slot["error"] = str(e)

    # ── service API ─────────────────────────────────────────────────────
    async def start(self, sources: list[str] | None = None) -> dict:
        if not await self.available():
            return {"ok": False, "error": "soundcard not installed"}
        if self._session is not None:
            return {"ok": False, "error": "already recording"}

        want = [s for s in (sources or self._default_sources()) if s in VALID_SOURCES]
        if not want:
            return {"ok": False, "error": "no valid sources"}

        samplerate, block, max_frames = self._samplerate(), self._blocksize(), self._max_frames()
        stop = threading.Event()
        slots: dict[str, dict] = {}
        started_labels: list[str] = []
        for src in want:
            try:
                mic = await asyncio.to_thread(self._resolve_mic, src)
            except Exception as e:
                slots[src] = {"chunks": [], "frames": 0, "error": str(e), "thread": None}
                continue
            slot = {"chunks": [], "frames": 0, "error": None, "thread": None}
            th = threading.Thread(
                target=self._capture_thread,
                args=(mic, slot, stop, samplerate, block, max_frames),
                daemon=True, name=f"meeting-cap-{src}",
            )
            slot["thread"] = th
            slots[src] = slot
            th.start()
            started_labels.append(src)

        if not started_labels:
            errs = "; ".join(f"{k}: {v['error']}" for k, v in slots.items() if v.get("error"))
            return {"ok": False, "error": errs or "no source could start"}

        self._session = {
            "id": uuid.uuid4().hex[:10],
            "stop": stop,
            "started": time.monotonic(),
            "samplerate": samplerate,
            "slots": slots,
        }
        return {"ok": True, "session_id": self._session["id"], "sources": started_labels}

    async def status(self) -> dict:
        s = self._session
        if s is None:
            return {"recording": False}
        return {
            "recording": True,
            "session_id": s["id"],
            "elapsed_s": round(time.monotonic() - s["started"], 1),
            "sources": [k for k, v in s["slots"].items() if v.get("thread")],
        }

    async def stop(self) -> dict:
        s = self._session
        if s is None:
            return {"ok": False, "error": "not recording"}
        self._session = None  # release the single-session lock up front
        s["stop"].set()

        def _finalize() -> dict:
            streams, per_source, errors = [], {}, {}
            for src, slot in s["slots"].items():
                th = slot.get("thread")
                if th is not None:
                    th.join(timeout=5.0)
                if slot.get("error"):
                    errors[src] = slot["error"]
                data = b"".join(slot["chunks"])
                if data:
                    streams.append(data)
                    per_source[src] = len(data) // 2  # frames
            wav = mix_streams_to_wav(streams, samplerate=s["samplerate"])
            frames = max([0, *per_source.values()])
            duration_s = round(frames / s["samplerate"], 1) if s["samplerate"] else 0.0
            out_path = CAPTURE_DIR / f"meeting_{s['id']}.wav"
            out_path.write_bytes(wav)
            return {
                "ok": True, "wav_path": str(out_path), "bytes": len(wav),
                "duration_s": duration_s, "sources": list(per_source),
                "errors": errors,
            }

        return await asyncio.to_thread(_finalize)
