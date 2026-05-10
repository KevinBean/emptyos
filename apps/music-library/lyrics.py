"""Lyrics — AI songwriting assistant.

Generates complete lyrics from a description, saves to vault song directory.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import slugify

if TYPE_CHECKING:
    from .app import MusicLibraryApp

STYLES = [
    {"id": "pop", "label": "Pop"},
    {"id": "rock", "label": "Rock"},
    {"id": "indie-folk", "label": "Indie Folk"},
    {"id": "hip-hop", "label": "Hip Hop"},
    {"id": "r-and-b", "label": "R&B"},
    {"id": "electronic", "label": "Electronic / EDM"},
    {"id": "country", "label": "Country"},
    {"id": "jazz", "label": "Jazz"},
    {"id": "blues", "label": "Blues"},
    {"id": "metal", "label": "Metal"},
    {"id": "punk", "label": "Punk"},
    {"id": "classical", "label": "Classical / Orchestral"},
    {"id": "reggae", "label": "Reggae"},
    {"id": "latin", "label": "Latin"},
    {"id": "k-pop", "label": "K-Pop"},
    {"id": "lo-fi", "label": "Lo-Fi"},
    {"id": "acoustic", "label": "Acoustic"},
    {"id": "ballad", "label": "Ballad"},
]

LYRICS_SYSTEM = (
    "You are a talented songwriter. Write vivid, emotionally resonant lyrics "
    "with clear structure. Do NOT explain or add commentary — return lyrics only."
)

LYRICS_PROMPT = (
    "Write complete song lyrics based on this description:\n"
    "{description}\n\n"
    "{style_hint}{lang_hint}\n"
    "Include: song title, verse 1, chorus, verse 2, chorus, bridge, final chorus.\n"
    "Return the lyrics only, with section labels like [Verse 1], [Chorus], etc."
)


class LyricsMixin:
    def __init__(self, app: MusicLibraryApp):
        self.app = app

    async def generate(self, description: str, style: str = "", language: str = "en") -> dict:
        lang_hint = f" Write in {language}." if language != "en" else ""
        style_hint = f" Style: {style}." if style else ""

        prompt = LYRICS_PROMPT.format(
            description=description,
            style_hint=style_hint,
            lang_hint=lang_hint,
        )
        lyrics = await self.app.think(prompt, system=LYRICS_SYSTEM, domain="text", temperature=0.9)

        title = description[:40]
        for line in lyrics.split("\n"):
            line = line.strip()
            if line and not line.startswith("["):
                title = line.strip("# ").strip()
                break

        result = {
            "title": title,
            "description": description,
            "style": style,
            "lyrics": lyrics,
        }
        await self.app.emit("music:lyrics_created", {"title": title})
        return result

    async def save_to_vault(self, result: dict) -> str:
        title_slug = slugify(result["title"], max_len=40)
        filename = f"{title_slug}.md"
        path = self.app._songs_dir() / filename
        self.app._songs_dir().mkdir(parents=True, exist_ok=True)
        content = (
            f"---\ntitle: {result['title']}\nstatus: draft\n"
            f"created: {date.today().isoformat()}\n"
            f"style: {result.get('style', '')}\n---\n\n"
            f"# {result['title']}\n\n"
            f"> {result['description']}\n\n"
            f"{result['lyrics']}\n"
        )
        await self.app.write(str(path), content)
        return str(path)

    async def history(self) -> list[dict]:
        events = await self.app.kernel.events.history(event_type="music:lyrics_created", limit=20)
        return [{"title": e["data"].get("title", ""), "timestamp": e["timestamp"]} for e in events]
