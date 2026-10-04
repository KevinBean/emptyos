"""Presentations — markdown-driven decks for talks.

One vault note per deck under {vault}/30_Resources/EmptyOS/ppt/<slug>.md.
Slides split on a horizontal rule (`---` on its own line). Each slide is
plain markdown; `Notes:` lines or `<!-- notes: ... -->` blocks become
speaker notes (hidden by default in present mode).

Architecture:
- `parse_deck` is a pure function — used by editor preview, presenter, and
  HTML export. No side effects.
- Vault is the source of truth. Renderer (eos-deck.js) is shared with the
  podcast app — manual mode here, timed mode there.
- Standalone HTML export inlines the renderer + slides JSON so the file
  works offline (file:// or any static host).
"""

from __future__ import annotations

from pathlib import Path

from emptyos.sdk import BaseApp, cli_command

from . import crud as _crud
from . import generate as _generate
from . import integrations as _integrations
from . import media as _media

from .parser import (  # noqa: F401  (re-exported for tests / external imports)
    ALL_ELEMENTS,
    DEFAULT_VISUAL_STYLE,
    DECK_OUTLINE_SYSTEM,
    DEFAULT_ELEMENTS,
    INTENTS,
    VISUAL_STYLES,
    _STANDALONE_HTML,
    _count_slides,
    _HR_RE,
    _html_escape,
    _IMAGE_PLACEHOLDER_RE,
    _load_deck_js,
    _normalize_elements,
    _normalize_visual_style,
    _slugify,
    _extract_notes,
    _notes_hash,
    _SAY_LINE_RE,
    _split_frontmatter,
    _starter_body,
    SPEAKIFY_SYSTEM,
    build_gen_from_plan_system,
    build_outline_system,
    build_plan_system,
    parse_deck,
)


class PptApp(BaseApp):

    SETTABLE_FIELDS = {"title", "theme", "aspect", "visual_style"}

    async def setup(self):
        await super().setup()

    def _ppt_dir(self) -> str:
        return self.vault_config("ppt_dir", "30_Resources/EmptyOS/ppt")

    def _path_for(self, id: str) -> str | None:
        rows = self.vault_query(tags=["deck"]) or []
        for r in rows:
            if Path(r.get("path", "")).stem == id:
                return r["path"]
        return None

    @cli_command("list")
    async def cli_list(self):
        for d in await self.list_decks():
            print(f"{d['id']:30s}  {d['slide_count']:3d} slides  {d['title']}")

    @cli_command("export")
    async def cli_export(self, deck_id: str):
        result = await self.export_html(deck_id)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Exported → {result['path']}")

    # ── Crud (extracted to crud.py) ──
    list_decks  = _crud.list_decks
    list_all    = _crud.list_all
    set_field   = _crud.set_field
    create_deck = _crud.create_deck
    _draft_body = _crud._draft_body
    delete_deck = _crud.delete_deck
    get_deck    = _crud.get_deck
    save_deck   = _crud.save_deck
    api_list    = _crud.api_list
    api_create  = _crud.api_create
    api_get     = _crud.api_get
    api_delete  = _crud.api_delete
    api_save    = _crud.api_save
    api_update_fields = _crud.api_update_fields
    api_preview = _crud.api_preview

    # ── Generate (extracted to generate.py) ──
    plan_deck              = _generate.plan_deck
    generate_from_plan     = _generate.generate_from_plan
    regenerate_deck        = _generate.regenerate_deck
    _call_think            = _generate._call_think
    api_plan               = _generate.api_plan
    api_generate_from_plan = _generate.api_generate_from_plan
    api_regenerate         = _generate.api_regenerate
    deliver_work           = _generate.deliver_work
    api_elements           = _generate.api_elements
    api_intents            = _generate.api_intents

    # ── Integrations (extracted to integrations.py) ──
    to_podcast         = _integrations.to_podcast
    to_post            = _integrations.to_post
    from_canvas        = _integrations.from_canvas
    panel_recent_decks = _integrations.panel_recent_decks
    voice_present      = _integrations.voice_present
    api_to_podcast     = _integrations.api_to_podcast
    api_to_post        = _integrations.api_to_post
    api_from_canvas    = _integrations.api_from_canvas

    # ── Media (extracted to media.py) ──
    resolve_images       = _media.resolve_images
    _resolve_one_image   = _media._resolve_one_image
    _find_vault_image    = _media._find_vault_image
    _screenshot_url      = _media._screenshot_url
    narrate_deck         = _media.narrate_deck
    set_narration        = _media.set_narration
    speakify_deck        = _media.speakify_deck
    _render_deck_html    = _media._render_deck_html
    _write_vault_binary  = _media._write_vault_binary
    export_html          = _media.export_html
    export_pdf           = _media.export_pdf
    api_resolve_images   = _media.api_resolve_images
    api_narrate          = _media.api_narrate
    api_narration_toggle = _media.api_narration_toggle
    api_speakify         = _media.api_speakify
    api_export           = _media.api_export
    api_asset            = _media.api_asset
