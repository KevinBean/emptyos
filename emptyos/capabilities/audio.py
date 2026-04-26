"""Shared TTS audio directory.

All speak providers write generated audio here so any app's
`/api/audio/{filename}` route can serve it. Kept in sync with
`services/voice-api/server.py` which uses the same path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

AUDIO_DIR = Path(tempfile.gettempdir()) / "emptyos-voice"
AUDIO_DIR.mkdir(exist_ok=True)

AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}
