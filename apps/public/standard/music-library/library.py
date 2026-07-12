"""Thin re-export of the shared music library mixin.

The implementation lives in `emptyos.sdk.music_library` so the personal
`music-studio` app can share the same SongLibrary / AlbumLibrary /
LibraryMixin without copy-paste drift. This file exists to keep
`from . import library` working in `app.py`.
"""

from emptyos.sdk.music_library import (
    AUDIO_EXTS,
    SIDECAR_MD,
    AlbumLibrary,
    LibraryMixin,
    SongLibrary,
)

__all__ = ["AUDIO_EXTS", "SIDECAR_MD", "AlbumLibrary", "LibraryMixin", "SongLibrary"]
