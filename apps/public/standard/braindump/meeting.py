"""braindump — meeting-capture endpoints (system-audio loopback + mic).

Thin bridge between the optional `meeting-capture` service plugin (owns the
recording) and braindump's existing audio pipeline (owns transcribe → summarize
→ extract → review). A stopped recording lands as a `raw.wav` artifact and runs
through the *exact* same `_run_pipeline` path as an uploaded voice memo — so the
whole review UI downstream is unchanged.

Soft dependency: the plugin is looked up via ``get_optional`` — braindump loads
and works without it (the Meeting surface just stays hidden). Gated on
braindump's own dark flag; recording is explicit start/stop, never ambient.

Cross-module callers reach these via ``self.X`` after re-binding in app.py.
Do not import from ``.app`` (it imports us, which would cycle).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:  # for type hints only
    from .app import BrainDumpApp  # noqa: F401


# ─── Bind to BrainDumpApp class as ───────────────────────────────────────────
#   _meeting_svc              = _meeting._meeting_svc
#   api_meeting_capabilities  = _meeting.api_meeting_capabilities
#   api_meeting_start         = _meeting.api_meeting_start
#   api_meeting_status        = _meeting.api_meeting_status
#   api_meeting_stop          = _meeting.api_meeting_stop
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────────────


def _meeting_svc(self):
    """The meeting-capture plugin service, or None when absent/uninstalled."""
    return self.kernel.services.get_optional("meeting-capture")


@web_route("GET", "/api/meeting/capabilities")
async def api_meeting_capabilities(self, request):
    """UI probe on load: is the Meeting surface usable, and which sources.
    Never errors — a missing plugin just reports available=False."""
    if not self._enabled():
        return {"enabled": False, "available": False, "sources": []}
    svc = self._meeting_svc()
    if svc is None:
        return {"enabled": True, "available": False, "sources": []}
    try:
        info = await svc.devices()
    except Exception as e:
        return {"enabled": True, "available": False, "sources": [], "error": str(e)}
    return {"enabled": True, "available": bool(info.get("available")),
            "sources": info.get("sources", []), "recording": bool(info.get("recording"))}


@web_route("POST", "/api/meeting/start")
async def api_meeting_start(self, request):
    """Begin capturing system audio + mic. Body: {sources?: ["loopback","mic"]}."""
    if not self._enabled():
        return {"error": "disabled"}
    svc = self._meeting_svc()
    if svc is None:
        return {"error": "meeting capture unavailable"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    sources = body.get("sources") if isinstance(body, dict) else None
    return await svc.start(sources=sources)


@web_route("GET", "/api/meeting/status")
async def api_meeting_status(self, request):
    svc = self._meeting_svc()
    if svc is None:
        return {"recording": False}
    return await svc.status()


@web_route("POST", "/api/meeting/stop")
async def api_meeting_stop(self, request):
    """Stop the recording, hand the WAV to the standard audio pipeline, and
    return a run_id the existing poll/review UI drives. The temp WAV is deleted
    once copied into the run dir (leave no trace outside the reviewable run)."""
    if not self._enabled():
        return {"error": "disabled"}
    svc = self._meeting_svc()
    if svc is None:
        return {"error": "meeting capture unavailable"}
    res = await svc.stop()
    if not res.get("ok"):
        return {"error": res.get("error") or "capture failed"}

    from pathlib import Path
    wav = Path(res["wav_path"])
    try:
        audio_bytes = wav.read_bytes()
    finally:
        wav.unlink(missing_ok=True)  # temp file — the run dir is the only copy
    if not audio_bytes:
        return {"error": "empty recording"}

    handle = self.runs("runs").new()
    run_id = handle.run_id
    handle.write_artifact("raw.wav", audio_bytes)
    inputs = {"mode": "audio", "audio_artifact": "raw.wav"}
    asyncio.create_task(self.emit("braindump:started", {"mode": "meeting"}))
    asyncio.create_task(self._run_pipeline(inputs, run_id=run_id))
    return {"run_id": run_id, "status": "running",
            "duration_s": res.get("duration_s", 0), "sources": res.get("sources", [])}
