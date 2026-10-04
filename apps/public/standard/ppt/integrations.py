"""ppt — cross-app handoffs + hub/voice surfaces.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: exports to podcast/post, canvas-board import, the hub recent-decks panel, and the voice-present intent. Source of truth for ppt's outward wiring.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.get_deck / self.create_deck (crud); self.call_app for podcast/publish/boards.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import datetime
from emptyos.sdk import web_route
from .parser import DEFAULT_VISUAL_STYLE, _normalize_visual_style, _slugify
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PptApp  # noqa: F401 — for type hints only


# ─── Bind to PptApp class as ────────────────────────────────
#   to_podcast          = _integrations.to_podcast
#   to_post             = _integrations.to_post
#   from_canvas         = _integrations.from_canvas
#   panel_recent_decks  = _integrations.panel_recent_decks
#   voice_present       = _integrations.voice_present
#   api_to_podcast      = _integrations.api_to_podcast
#   api_to_post         = _integrations.api_to_post
#   api_from_canvas     = _integrations.api_from_canvas
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def to_podcast(self, id: str) -> dict:
    """Turn a deck into a two-host podcast episode via the podcast app.

    Builds a text 'context' from the deck's slide headings + speaker notes,
    delegates to `podcast._full_generate` with the en_mf voice pair. The
    podcast app handles scripting, TTS, and history. Returns the new
    episode's id so the user can jump to /podcast/.
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    deck = await self.get_deck(id)
    title = deck["frontmatter"].get("title") or id
    # Build a rough script source: each slide as a section.
    chunks = []
    for i, s in enumerate(deck["slides"], 1):
        chunks.append(f"## Slide {i}\n{s.get('md', '')}")
        if s.get("notes"):
            chunks.append(f"Speaker notes: {s['notes']}")
    context = "\n\n".join(chunks)
    try:
        result = await self.call_app(
            "podcast",
            "_full_generate",
            topic=title,
            voice_a="emma",
            voice_b="michael",
            context=context,
            segments=12,
            words=65,
            language="en",
            with_cover=True,
            with_video=False,
        )
    except Exception as e:
        return {"error": f"podcast call failed: {e}"}
    return {
        "ok": True,
        "episode_id": result.get("id") if isinstance(result, dict) else None,
        "open": "/podcast/",
    }


async def to_post(self, id: str) -> dict:
    """Render a deck as a long-form blog post under the publish source folder.

    Each slide becomes a section: heading + body + flowing paragraph from
    speaker notes. Image refs (`![[name]]`) are preserved — they resolve
    through the ppt asset endpoint. Writes a `type: post` note to
    `<publish.source_folder>/<deck-id>.md`. The publish app's scanner
    picks it up automatically; user marks `publish: true` when ready.
    """
    deck = await self.get_deck(id)
    if "error" in deck:
        return deck
    vault = self.kernel.config.notes_path
    if not vault:
        return {"error": "No vault configured"}
    publish_dir = self.vault_config("publish_source_dir", "30_Resources/Published")
    title = deck["frontmatter"].get("title") or id
    body_parts = []
    for i, s in enumerate(deck["slides"]):
        md = s.get("md") or ""
        notes = s.get("notes") or ""
        # First slide's `# Title` is the post title (skip in body).
        if i == 0 and md.lstrip().startswith("# "):
            continue
        body_parts.append(md.strip())
        if notes:
            body_parts.append(notes.strip())
        body_parts.append("")
    body = "\n\n".join(body_parts).strip()
    fm_lines = [
        "---",
        f"title: {title}",
        "type: post",
        "lifecycle: living",
        f"created: {datetime.now().strftime('%Y-%m-%d')}",
        f"updated: {datetime.now().strftime('%Y-%m-%d')}",
        "publish: false",
        f"source_deck: {id}",
        "tags:",
        "  - post",
        "  - deck-derived",
        "---",
        "",
        body,
        "",
    ]
    rel_path = f"{publish_dir.rstrip('/')}/{id}.md"
    self.vault_write_at(rel_path, "\n".join(fm_lines))
    await self.emit("ppt:exported", {"id": id, "as": "post", "path": rel_path})
    return {"ok": True, "path": rel_path, "open": "/publish/"}


async def from_canvas(self, board_id: str, title: str = "") -> dict:
    """Build a new deck from a canvas board.

    Nodes are sorted top-to-bottom, left-to-right. Each node becomes a
    slide with the node's text as the body. The first node (or a
    provided `title`) becomes the title slide.
    """
    try:
        board = await self.call_app("canvas", "load_board", board_id)
    except Exception as e:
        return {"error": f"canvas call failed: {e}"}
    nodes = (board or {}).get("nodes") or []
    if not nodes:
        return {"error": f"Canvas board '{board_id}' is empty"}
    ordered = sorted(
        nodes,
        key=lambda n: (round((n.get("y") or 0) / 50), n.get("x") or 0),
    )
    deck_title = title.strip() or f"Deck from {board_id}"
    slug = _slugify(deck_title)
    rel = f"{self._ppt_dir()}/{slug}.md"
    if self._path_for(slug):
        slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
        rel = f"{self._ppt_dir()}/{slug}.md"

    slides_md = [f"# {deck_title}\n*Drafted from canvas board `{board_id}`*"]
    for n in ordered:
        text = (n.get("text") or "").strip()
        if not text:
            continue
        # Each node body becomes one slide. If it has multiple paragraphs,
        # the first line becomes a heading.
        lines = text.split("\n", 1)
        head = lines[0].strip()
        rest = lines[1].strip() if len(lines) > 1 else ""
        slides_md.append(f"## {head}\n\n{rest}".strip())
    body = "\n\n---\n\n".join(slides_md)

    fm = {
        "title": deck_title,
        "type": "deck",
        "lifecycle": "living",
        "theme": self.setting_or_config("ppt.default_theme", "dark", config_key="default_theme"),
        "aspect": self.setting_or_config("ppt.default_aspect", "16:9", config_key="default_aspect"),
        "visual_style": _normalize_visual_style(
            self.setting_or_config("ppt.default_visual_style", DEFAULT_VISUAL_STYLE, config_key="default_visual_style")
        ),
        "created": datetime.now().strftime("%Y-%m-%d"),
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "source_board": board_id,
        "tags": ["deck", "canvas-derived"],
    }
    self.vault_create_note(rel, fm, body)
    await self.emit("ppt:created", {"id": slug, "from": "canvas", "board": board_id})
    return {"ok": True, "id": slug, "slides": len(ordered), "open": f"/ppt/#{slug}"}


async def panel_recent_decks(self) -> list[dict] | None:
    decks = await self.list_decks()
    if not decks:
        return None
    return [
        {"label": d["title"], "link": f"/ppt/#{d['id']}", "detail": f"{d['slide_count']} slides"}
        for d in decks[:3]
    ]


async def voice_present(self, query: str) -> dict:
    decks = await self.list_decks()
    if not decks:
        return {"say": "You don't have any decks yet."}
    q = (query or "").lower().strip()
    match = next((d for d in decks if q in d["title"].lower()), None) if q else decks[0]
    if not match:
        return {"say": f"No deck matched '{query}'."}
    return {
        "say": f"Opening {match['title']}.",
        "card": {
            "renderer": "entity-card",
            "data": {
                "title": match["title"],
                "subtitle": f"{match['slide_count']} slides",
                "fields": [{"label": "Open", "value": f"/ppt/present.html?id={match['id']}"}],
            },
        },
    }


@web_route("POST", "/api/decks/{id}/to-podcast")
async def api_to_podcast(self, request):
    return await self.to_podcast(request.path_params["id"])


@web_route("POST", "/api/decks/{id}/to-post")
async def api_to_post(self, request):
    return await self.to_post(request.path_params["id"])


@web_route("POST", "/api/from-canvas")
async def api_from_canvas(self, request):
    body = await request.json()
    return await self.from_canvas(
        board_id=body.get("board_id", "inbox"),
        title=body.get("title", ""),
    )
