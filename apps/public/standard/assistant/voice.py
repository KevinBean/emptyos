"""Assistant — voice I/O endpoints, PWA manifest, and `eos ask` CLI.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the TTS/STT REST shims that proxy ``self.speak`` /
``self.listen`` for the frontend, the PWA `manifest.json`, and the
top-level `eos ask` CLI command.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._build_context`` / ``self._build_system``
(context.py) inside `cmd_ask`.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import cli_command, web_route

if TYPE_CHECKING:
    from .app import AssistantApp  # noqa: F401 — for type hints only


# ─── Bind to AssistantApp class as ──────────────────────────────────
#   api_tts      = _voice.api_tts
#   api_audio    = _voice.api_audio
#   api_stt      = _voice.api_stt
#   api_manifest = _voice.api_manifest
#   cmd_ask      = _voice.cmd_ask
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────


@web_route("POST", "/api/tts")
async def api_tts(self, request):
    """Text-to-speech via platform speak capability."""
    data = await request.json()
    text = data.get("text", "")
    if not text:
        return {"error": "text required"}
    try:
        result = await self.speak(text)
        if isinstance(result, (str, Path)):
            return {
                "audio_url": f"/assistant/api/audio/{Path(result).name}",
                "path": str(result),
            }
        return {"error": "unexpected TTS result type"}
    except Exception as e:
        return {"error": f"TTS failed: {e}"}


@web_route("GET", "/api/audio/{filename}")
async def api_audio(self, request):
    """Serve TTS-generated audio. ``api_tts`` returns URLs under this prefix;
    no handler existed before — clients silently 404'd. Delegates to the
    shared ``BaseApp.serve_shared_audio`` helper; reader/radio/dictionary
    can migrate to the same helper next time they're touched."""
    return self.serve_shared_audio(request.path_params["filename"])


@web_route("POST", "/api/stt")
async def api_stt(self, request):
    """Speech-to-text via platform listen capability."""
    data = await request.json()
    audio_path = data.get("audio_path", "")
    if not audio_path:
        return {"error": "audio_path required"}
    try:
        text = await self.listen(audio_path)
        return {"text": text}
    except Exception as e:
        return {"error": f"STT failed: {e}"}


@web_route("GET", "/manifest.json")
async def api_manifest(self, request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "name": "EmptyOS Assistant",
            "short_name": "Assistant",
            "description": "AI chat with vault integration",
            "start_url": "/assistant/",
            "display": "standalone",
            "background_color": "#1a1a2e",
            "theme_color": "#1a1a2e",
            "icons": [
                {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
    )


@cli_command("ask", help="Ask EmptyOS anything")
async def cmd_ask(self, question: str = ""):
    if not question:
        print("  Usage: eos ask 'your question here'")
        return
    context = await self._build_context(question)
    system = await self._build_system()
    prompt = f"Vault context:\n{context}\n\n{question}" if context else question
    response = await self.think(prompt, system=system, domain="text", temperature=0.4)
    print(f"\n  {response}\n")
