"""Music Library — browse, play, and lyric-write for vault songs.

Pure-vault + LLM app. No GPU, no ComfyUI. Strangers cloning EmptyOS get a
working music browser out of the box. Heavier ComfyUI-driven generation
(audio composition, music videos) lives in the personal music-studio app.
"""

from __future__ import annotations

from pathlib import Path

from urllib.parse import quote

from fastapi.responses import FileResponse

from emptyos.sdk import BaseApp, cli_command, web_route


def _quote_url_path(p: str) -> str:
    """URL-encode a vault-relative path while preserving slashes.

    Vault paths contain spaces, parens, middle dots (·), and CJK chars
    that browsers won't fetch unless percent-encoded.
    """
    return quote(p, safe="/")

from . import library, lyrics
from .lyrics import STYLES

MIME_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac",
    ".webm": "audio/webm",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class MusicLibraryApp(BaseApp):

    def __init__(self, kernel, manifest):
        super().__init__(kernel, manifest)
        self._library = library.LibraryMixin(self)
        self._lyrics = lyrics.LyricsMixin(self)

    def _songs_dir(self) -> Path:
        return self.vault_config_path("songs_dir", "Music/Songs") or Path(".")

    # ── Public methods (for call_app) ──

    async def list_songs(self, status: str = "") -> list[dict]:
        return await self._library.list_songs(status)

    async def song_detail(self, filename: str) -> dict:
        return await self._library.detail(filename)

    async def audio_files(self, filename: str) -> dict:
        return await self._library.audio_files(filename)

    async def cover_art(self, filename: str) -> str:
        return await self._library.cover_art(filename)

    async def generate_lyrics(self, description: str, style: str = "", language: str = "en") -> dict:
        return await self._lyrics.generate(description, style, language)

    # ── CLI ──

    @cli_command("music", help="Song library")
    async def cmd_music(self, action: str = "list", status: str = ""):
        songs = await self._library.list_songs(status)
        if not songs:
            print("  No songs found")
            return
        print(f"\n  {len(songs)} songs\n")
        for s in songs[:20]:
            status_str = f"[{s['status']}]" if s.get("status") else ""
            print(f"  {s['title'][:40]:<40} {status_str}")
        print()

    @cli_command("lyrics", help="Generate song lyrics")
    async def cmd_lyrics(self, description: str = "", style: str = ""):
        if not description:
            print("  Usage: eos lyrics 'a melancholy love song' [--style 'indie folk']")
            return
        print("  Writing lyrics...")
        result = await self._lyrics.generate(description, style)
        print(f"\n  {result['title']}\n")
        for line in result["lyrics"].split("\n")[:20]:
            print(f"  {line}")
        if len(result["lyrics"].split("\n")) > 20:
            print("  ...")

    # ── Library API ──

    @web_route("GET", "/api/list")
    async def api_list(self, request):
        status = request.query_params.get("status", "")
        return await self._library.list_songs(status)

    @web_route("GET", "/api/detail/{filename}")
    async def api_detail(self, request):
        return await self._library.detail(request.path_params["filename"])

    @web_route("GET", "/api/search")
    async def api_search(self, request):
        return await self._library.search(request.query_params.get("q", ""))

    @web_route("GET", "/api/albums")
    async def api_albums(self, request):
        return await self._library.list_albums()

    @web_route("GET", "/api/albums/{album_title}/songs")
    async def api_album_songs(self, request):
        return await self._library.album_songs(request.path_params["album_title"])

    @web_route("GET", "/api/stats")
    async def api_stats(self, request):
        songs = await self._library.list_songs()
        return await self._library.stats(songs)

    @web_route("GET", "/api/suggest-next")
    async def api_suggest_next(self, request):
        return await self._library.suggest_next()

    @web_route("GET", "/api/audio/{filename}")
    async def api_audio_files(self, request):
        return await self._library.audio_files(request.path_params["filename"])

    @web_route("GET", "/api/song-summary/{filename}")
    async def api_song_summary(self, request):
        """One-shot fetch for a detail panel: detail + audio + cover + copyright.

        Music-studio (personal) extends this with mvs/drafts via call_app
        — the library doesn't know about visual generation."""
        filename = request.path_params["filename"]
        detail = await self._library.detail(filename)
        if isinstance(detail, dict) and detail.get("error"):
            return detail
        audio = await self._library.audio_files(filename)
        cover_path = await self._library.cover_art(filename)
        copyright_files = await self._library.copyright_files(filename)
        song_key = filename[:-3] if filename.endswith(".md") else filename
        return {
            "detail": detail,
            "audio": audio,
            "cover": f"/music-library/api/image/{_quote_url_path(cover_path)}" if cover_path else None,
            "cover_path": cover_path or "",
            "copyright": copyright_files,
            "song_key": song_key,
        }

    @web_route("GET", "/api/cover/{filename}")
    async def api_cover(self, request):
        path = await self._library.cover_art(request.path_params["filename"])
        if not path:
            return {"url": None}
        return {"url": f"/music-library/api/image/{_quote_url_path(path)}"}

    @web_route("GET", "/api/covers")
    async def api_covers_batch(self, request):
        covers = await self._library.all_covers()
        return {
            k: f"/music-library/api/image/{_quote_url_path(v)}" if v else None
            for k, v in covers.items()
        }

    @web_route("GET", "/api/image/{img_path:path}")
    async def api_serve_image(self, request):
        img_path = request.path_params["img_path"]
        vault = self.kernel.config.notes_path
        if not vault:
            return {"error": "no vault"}
        full = (vault / img_path).resolve()
        if not str(full).startswith(str(vault.resolve())):
            return {"error": "forbidden"}
        mime = MIME_TYPES.get(full.suffix.lower())
        if mime and full.exists():
            return FileResponse(str(full), media_type=mime)
        return {"error": "not found"}

    @web_route("GET", "/api/stream/{audio_path:path}")
    async def api_stream_audio(self, request):
        audio_path = request.path_params["audio_path"]
        full = self._library.resolve_audio_path(audio_path)
        if not full:
            return {"error": "not found"}
        mime = MIME_TYPES.get(full.suffix.lower(), "application/octet-stream")
        return FileResponse(str(full), media_type=mime, filename=full.name)

    @web_route("POST", "/api/update/{filename}")
    async def api_update(self, request):
        data = await request.json()
        return await self._library.update(request.path_params["filename"], data)

    # ── Lyrics API ──

    @web_route("POST", "/api/lyrics/generate")
    async def api_lyrics_generate(self, request):
        data = await request.json()
        description = data.get("description", "")
        if not description:
            return {"error": "description required"}
        result = await self._lyrics.generate(description, data.get("style", ""), data.get("language", "en"))
        if data.get("save", False):
            result["saved_to"] = await self._lyrics.save_to_vault(result)
        return result

    @web_route("GET", "/api/lyrics/history")
    async def api_lyrics_history(self, request):
        return await self._lyrics.history()

    @web_route("GET", "/api/lyrics/styles")
    async def api_lyrics_styles(self, request):
        return STYLES

    @web_route("POST", "/api/lyrics/save")
    async def api_lyrics_save(self, request):
        data = await request.json()
        for field in ("title", "lyrics"):
            if not data.get(field):
                return {"error": f"{field} required"}
        result = {
            "title": data["title"],
            "description": data.get("description", ""),
            "style": data.get("style", ""),
            "lyrics": data["lyrics"],
        }
        path = await self._lyrics.save_to_vault(result)
        return {"saved_to": path, "title": result["title"]}

    # ── Page ──

    @web_route("GET", "/")
    async def page_home(self, request):
        from fastapi.responses import HTMLResponse
        page = Path(self.manifest.path) / "pages" / "index.html"
        if page.exists():
            return HTMLResponse(page.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Music Library</h1><p>Pages missing.</p>", status_code=404)
