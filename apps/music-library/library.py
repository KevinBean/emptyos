"""Library — song & album catalogue from vault.

Uses VaultLibrary (SDK) to query all notes tagged 'song' or 'album'
across the entire vault. Songs can live anywhere — project dirs, album
dirs, inbox — location doesn't matter, tags are identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import VaultLibrary

if TYPE_CHECKING:
    from .app import MusicLibraryApp


# Per-song sidecar metadata files written by music-studio. They live in the
# same folder as the song .md but must not count toward album-detection —
# otherwise a single dated-song folder containing character.md + scenes.md
# looks like a 3-track album and screenshot linkage stops resolving.
SIDECAR_MD = {"character.md", "scenes.md"}


class SongLibrary(VaultLibrary):
    tag = "song"
    fields = {
        "title": str, "artist": str, "album": str, "track_number": str,
        "status": str, "genre": str, "language": str,
        "energy": str, "mood": str, "theme": str, "persona": str, "voice": str,
        "bpm": str, "key": str, "duration": str,
        "created": str, "published": str,
        # External-source provenance — exposed raw so the music-studio source
        # panel can render service-specific links (Open in Suno / YouTube).
        # Without them in `fields`, VaultLibrary strips them from `detail()`
        # output and the panel falls back to "No external source linked yet"
        # even when the frontmatter has the URLs.
        "source_url": str, "suno_url": str, "suno_model": str,
        "youtube_url": str, "youtube_id": str,
    }
    aliases = {
        "genre": ["style"],
        # `source_url` reads from any of these alternate keys when the
        # canonical `source_url` field is absent. Note: aliases only fire
        # for fields listed in `fields` above.
        "source_url": ["source-url", "suno_url", "suno-url"],
    }
    sort_key = "created"
    sort_reverse = True
    search_fields = ["title", "genre", "album", "mood", "theme", "artist", "language"]


class AlbumLibrary(VaultLibrary):
    tag = "album"
    fields = {
        "title": str, "artist": str, "status": str,
        "style": str, "genre": str,
        "created": str, "track_count": str,
        "tags": list,
    }
    aliases = {
        "track_count": ["tracks"],
        "genre": ["style"],
    }
    sort_key = "created"
    sort_reverse = True
    search_fields = ["title", "artist", "style"]


AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".webm"}


def _md_count(d) -> int:
    try:
        return sum(1 for f in d.iterdir() if f.is_file() and f.suffix.lower() == ".md")
    except OSError:
        return 1


def _has_license(song: dict, license_field: str = "") -> bool:
    """Check if a song has a copyright/license record.

    When license_field is set, check that field for a valid URL.
    Always checks the explicit 'license' field as fallback.
    """
    if license_field:
        val = song.get(license_field, "").strip().strip('"').strip("'")
        if val.startswith("http"):
            return True
    lic = song.get("license", "").strip()
    return bool(lic)


class LibraryMixin:

    def __init__(self, app: MusicLibraryApp):
        self.app = app
        extra = app.app_config("extra_fields", [])
        self.songs = SongLibrary(app, extra_fields=extra or None)
        self.albums = AlbumLibrary(app)
        self._license_field = app.app_config("license_field", "")

    async def list_songs(self, status_filter: str = "") -> list[dict]:
        filters = {"status": status_filter} if status_filter else {}
        items = self.songs.list(**filters)
        return self._enrich(items)

    def _enrich(self, items: list[dict]) -> list[dict]:
        """Add has_lyrics, has_audio, audio_count, modified, provenance per row.

        Single pass with per-directory audio cache so 200 songs cost ~one stat
        per song + one dir scan per unique parent. Idempotent — if a row already
        has these fields they're left alone.
        """
        from pathlib import Path
        vault = self.app.kernel.config.notes_path
        if not vault:
            return items

        dir_audio_cache: dict[str, list] = {}

        def _audio_for_song(song_path: Path) -> tuple[bool, int]:
            parent = song_path.parent
            key = str(parent)
            if key not in dir_audio_cache:
                try:
                    dir_audio_cache[key] = [
                        f for f in parent.iterdir()
                        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
                    ]
                except OSError:
                    dir_audio_cache[key] = []
            stem_l = song_path.stem.lower()
            files = dir_audio_cache[key]
            md_count_in_dir = _md_count(parent) if parent.is_dir() else 0
            # Dated-folder layout — note inside its own subdir alongside its audio.
            # All audio in that subdir belongs to the song, regardless of file stem
            # (e.g. ``无所住.md`` + ``无所住.mp3`` + ``no_vocals.mp3`` + ``vocals.mp3``).
            if md_count_in_dir <= 1:
                matches = files
            else:
                matches = [
                    f for f in files
                    if (f.stem.lower() == stem_l
                        or f.stem.lower().startswith(stem_l)
                        or stem_l.startswith(f.stem.lower()))
                ]
            return (bool(matches), len(matches))

        for s in items:
            path_str = s.get("path", "")
            if not path_str:
                continue
            full = vault / path_str
            try:
                stat = full.stat()
                s["modified"] = int(stat.st_mtime)
                s["modified_iso"] = ""  # cheap; UI can format
                # Cheap has_lyrics: file size > minimal frontmatter (~120 bytes)
                # File-IO-free; not 100% precise but avoids reading every song.
                s["has_lyrics"] = stat.st_size > 250
            except OSError:
                s["modified"] = 0
                s["has_lyrics"] = False

            try:
                has_audio, count = _audio_for_song(full)
                s["has_audio"] = has_audio
                s["audio_count"] = count
            except Exception:
                s["has_audio"] = False
                s["audio_count"] = 0

            # Provenance — only present when frontmatter declares ai_provider/ai_model.
            # SongLibrary.fields doesn't declare these by default; users can add via
            # [apps.music-studio] extra_fields in emptyos.toml. Field absent = None.
            prov_provider = (s.get("ai_provider") or "").strip() if isinstance(s.get("ai_provider"), str) else ""
            prov_model = (s.get("ai_model") or "").strip() if isinstance(s.get("ai_model"), str) else ""
            if prov_provider or prov_model:
                s["provenance"] = {
                    "provider": prov_provider,
                    "model": prov_model,
                    "mode": s.get("ai_mode") or "cloud",
                }
            else:
                s["provenance"] = None

        return items

    async def list_albums(self) -> list[dict]:
        return self.albums.list()

    async def album_songs(self, album_title: str) -> list[dict]:
        all_songs = self.songs.list()
        return [s for s in all_songs if album_title in s.get("album", "")]

    async def detail(self, filename: str) -> dict:
        result = self.songs.detail(filename)
        if result is None:
            return {"error": "not found"}
        # Rename body → lyrics for music context
        result["lyrics"] = result.pop("body", "")
        return result

    async def search(self, q: str) -> list[dict]:
        return self.songs.search(q)

    async def stats(self, songs: list[dict] | None = None) -> dict:
        all_songs = songs or self.songs.list()
        album_list = self.albums.list()

        # Build stats from the single song list
        status_counts: dict[str, int] = {}
        genre_counts: dict[str, int] = {}
        lang_counts: dict[str, int] = {}
        albums_map: dict[str, int] = {}
        licensed = 0
        unlicensed = 0

        for s in all_songs:
            for val, bucket in [(s.get("status", ""), status_counts),
                                (s.get("genre", ""), genre_counts),
                                (s.get("language", ""), lang_counts)]:
                v = str(val or "unknown").strip()
                if v:
                    bucket[v] = bucket.get(v, 0) + 1
            album = s.get("album", "").strip()
            if album:
                album_clean = album.replace("[[", "").replace("]]", "")
                albums_map[album_clean] = albums_map.get(album_clean, 0) + 1
            if self._license_field:
                if _has_license(s, self._license_field):
                    licensed += 1
                else:
                    unlicensed += 1

        result = {
            "total_songs": len(all_songs),
            "total_albums": len(album_list),
            "by_status": status_counts,
            "by_genre": genre_counts,
            "by_language": lang_counts,
            "by_album": albums_map,
        }
        if self._license_field:
            result["licensed"] = licensed
            result["unlicensed"] = unlicensed
        return result

    async def suggest_next(self) -> dict:
        songs = self.songs.list()
        if not songs:
            return {"suggestion": "No songs in library yet."}
        lines = [f"- {s['title']} [{s['status']}] {s.get('genre', '')}" for s in songs[:25]]
        prompt = (
            "Based on this song library, suggest ONE song to work on next and why. "
            "Prefer drafts or in-progress over finished. Be concise (2 sentences).\n\n"
            + "\n".join(lines)
        )
        result = await self.app.think(prompt, domain="text")
        return {"suggestion": result}

    async def audio_files(self, filename: str) -> list[dict]:
        """Find audio files for a song.

        - Dated-folder layout (note in its own subdir): all audio in that
          subdir, sorted same-stem first then by name.
        - Flat song at songs_dir root: only audio whose stem matches or
          prefix-matches the song stem (so unrelated audio in the songs root
          doesn't get attributed to every flat song).
        - Album folders: same as dated-folder — all audio in the folder.
        """
        path = self.songs._find_file(filename)
        if not path:
            return []
        song_stem = path.stem.lower()
        song_dir = path.parent
        vault = self.app.kernel.config.notes_path
        songs_root = None
        try:
            songs_root_cfg = self.app.vault_config_path("songs_dir", "Music/Songs")
            songs_root = songs_root_cfg.resolve() if songs_root_cfg else None
        except Exception:
            songs_root = None
        is_flat = songs_root is not None and song_dir.resolve() == songs_root
        # Album folders host multiple song notes alongside their audio — each
        # note must only claim its own stem-matching files. Dated-folder layout
        # (one .md in the dir) keeps the permissive "all audio belongs to me".
        is_shared_dir = is_flat or _md_count(song_dir) > 1

        files = []
        for f in sorted(song_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
                continue
            fs = f.stem.lower()
            same_stem = fs == song_stem
            prefix = fs.startswith(song_stem) or song_stem.startswith(fs)
            if is_shared_dir and not (same_stem or prefix):
                continue
            rel = str(f.relative_to(vault)).replace("\\", "/") if vault else f.name
            if same_stem:
                rank = 0 if f.suffix.lower() == ".mp3" else 1
            elif prefix:
                rank = 2
            else:
                rank = 3
            files.append({
                "name": f.name,
                "path": rel,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                "ext": f.suffix.lower(),
                "_rank": rank,
            })
        files.sort(key=lambda x: (x["_rank"], x["name"]))
        for f in files:
            del f["_rank"]
        return files

    async def copyright_files(self, filename: str) -> list[dict]:
        """Find generation screenshots + other copyright proof images for a song.

        AI-music tracks generated externally typically have a service screenshot
        named like ``Screenshot 2026-01-03 at 4.45.44 am.png`` (Mac) or
        ``Glass Wall Screenshot 2026-01-19 203955.png`` (album folders, prefix
        is the song title). For dated-folder songs (one .md per folder) we
        return every screenshot. For album folders (multiple .md per folder)
        we filter by song title / stem prefix.
        """
        path = self.songs._find_file(filename)
        if not path:
            return []
        song_dir = path.parent
        if not song_dir.exists():
            return []
        vault = self.app.kernel.config.notes_path
        img_exts = {".png", ".jpg", ".jpeg", ".webp"}

        # Decide if we should filter by song name. If the dir holds >1 .md it's
        # an album folder; only return screenshots whose name contains the song
        # title or stem.
        try:
            md_count = sum(
                1 for f in song_dir.iterdir()
                if f.is_file() and f.suffix.lower() == ".md"
                and f.name.lower() not in SIDECAR_MD
            )
        except OSError:
            md_count = 1

        # Read song title for matching against screenshot names.
        song_stem = path.stem.lower()
        song_title = ""
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.split("\n")[:30]:
                if line.lower().startswith("title:"):
                    song_title = line.split(":", 1)[1].strip().strip('"').strip("'").lower()
                    break
        except OSError:
            pass

        out = []
        for f in sorted(song_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in img_exts:
                continue
            name_l = f.name.lower()
            if "screenshot" not in name_l and "screen" not in name_l:
                continue
            # Filter for album folders: name must reference the song.
            if md_count > 1:
                title_first = song_title.split("/")[0].split("(")[0].strip()
                if not (
                    song_stem and song_stem in name_l
                    or title_first and title_first in name_l
                ):
                    continue
            try:
                rel = str(f.relative_to(vault)).replace("\\", "/") if vault else f.name
                out.append({
                    "name": f.name,
                    "path": rel,
                    "size_kb": round(f.stat().st_size / 1024),
                    "modified": int(f.stat().st_mtime),
                })
            except (OSError, ValueError):
                pass
        return out

    async def cover_art(self, filename: str) -> str | None:
        """Find a cover image in the same directory as a song note.

        Returns vault-relative path to the best cover image, or None.
        In album folders (multiple .md per dir), only matches images
        whose name contains this song's stem — otherwise every song
        in the album would share the first screenshot found.
        """
        path = self.songs._find_file(filename)
        if not path:
            return None
        song_dir = path.parent
        vault = self.app.kernel.config.notes_path
        img_exts = {".png", ".jpg", ".jpeg", ".webp"}
        is_shared = _md_count(song_dir) > 1
        song_stem = path.stem.lower()

        candidates = []
        for f in song_dir.iterdir():
            if f.suffix.lower() not in img_exts:
                continue
            name_l = f.name.lower()
            is_cover = "cover" in name_l and "vertical" not in name_l and "wide" not in name_l
            is_screenshot = "screenshot" in name_l
            if not (is_cover or is_screenshot):
                continue
            if is_shared and song_stem and song_stem not in name_l:
                continue
            if is_cover:
                rank = 0 if "final" in name_l else 1
            else:
                rank = 5
            candidates.append((rank, f))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        best = candidates[0][1]
        if vault:
            return str(best.relative_to(vault)).replace("\\", "/")
        return best.name

    async def all_covers(self) -> dict[str, str | None]:
        """Find cover art for all songs in one pass, grouped by directory.

        Per-directory image scan is cached; per-song selection then filters
        by song stem when the dir is shared (album folder), so each song
        in an album gets its own screenshot rather than all sharing one.
        """
        vault = self.app.kernel.config.notes_path
        if not vault:
            return {}
        songs = self.songs.list()
        img_exts = {".png", ".jpg", ".jpeg", ".webp"}
        result: dict[str, str | None] = {}
        # Cache: dir_key → (is_shared, [(rank, name_l, abs_path), ...])
        dir_cache: dict[str, tuple[bool, list]] = {}

        for s in songs:
            path_str = s.get("path", "")
            if not path_str:
                result[s["file"]] = None
                continue
            full = vault / path_str
            dir_key = str(full.parent)
            if dir_key not in dir_cache:
                images: list = []
                try:
                    for f in full.parent.iterdir():
                        if not f.is_file() or f.suffix.lower() not in img_exts:
                            continue
                        name_l = f.name.lower()
                        is_cover = "cover" in name_l and "vertical" not in name_l and "wide" not in name_l
                        is_screenshot = "screenshot" in name_l
                        if not (is_cover or is_screenshot):
                            continue
                        rank = (0 if "final" in name_l else 1) if is_cover else 5
                        images.append((rank, name_l, f))
                except Exception:
                    pass
                dir_cache[dir_key] = (_md_count(full.parent) > 1, images)

            is_shared, images = dir_cache[dir_key]
            song_stem = full.stem.lower()
            picks = [
                (rank, f) for rank, name_l, f in images
                if not is_shared or (song_stem and song_stem in name_l)
            ]
            if not picks:
                result[s["file"]] = None
                continue
            picks.sort(key=lambda x: x[0])
            try:
                result[s["file"]] = str(picks[0][1].relative_to(vault)).replace("\\", "/")
            except ValueError:
                result[s["file"]] = picks[0][1].name
        return result

    def resolve_audio_path(self, vault_rel_path: str):
        """Resolve a vault-relative audio path to an absolute Path."""
        vault = self.app.kernel.config.notes_path
        if not vault:
            return None
        full = (vault / vault_rel_path).resolve()
        if not str(full).startswith(str(vault.resolve())):
            return None
        if full.exists() and full.suffix.lower() in AUDIO_EXTS:
            return full
        return None

    async def update(self, filename: str, data: dict) -> dict:
        result = self.songs.update(filename, data)
        if result.get("ok"):
            await self.app.emit("music:updated", {"file": filename})
        return result
