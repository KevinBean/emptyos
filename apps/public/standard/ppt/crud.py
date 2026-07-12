"""ppt — deck lifecycle CRUD + boards integration.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: deck note IO (create/read/update/delete), the deck list + boards list_all/set_field, and the starter-body draft. Source of truth for where a deck note lives and its on-disk shape.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._path_for / self._ppt_dir (spine) for vault paths.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from emptyos.sdk import web_route
from .parser import (
    DEFAULT_VISUAL_STYLE,
    _count_slides,
    _normalize_elements,
    _normalize_visual_style,
    _notes_hash,
    _slugify,
    _split_frontmatter,
    _starter_body,
    build_outline_system,
    parse_deck,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import PptApp  # noqa: F401 — for type hints only


# ─── Bind to PptApp class as ────────────────────────────────
#   list_decks   = _crud.list_decks
#   list_all     = _crud.list_all
#   set_field    = _crud.set_field
#   create_deck  = _crud.create_deck
#   delete_deck  = _crud.delete_deck
#   get_deck     = _crud.get_deck
#   save_deck    = _crud.save_deck
#   _draft_body  = _crud._draft_body
#   api_list     = _crud.api_list
#   api_create   = _crud.api_create
#   api_get      = _crud.api_get
#   api_delete   = _crud.api_delete
#   api_save     = _crud.api_save
#   api_preview  = _crud.api_preview
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def list_decks(self) -> list[dict]:
    """Return all decks tagged `deck`, sorted by updated desc."""
    rows = self.vault_query(tags=["deck"]) or []
    out = []
    default_visual_style = self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE)
    for r in rows:
        props = r.get("properties") or {}
        path = r.get("path", "")
        slug = Path(path).stem
        slide_count = 0
        try:
            text = await self.read(path)
            slide_count = _count_slides(text)
        except Exception:
            pass
        out.append(
            {
                "id": slug,
                "path": path,
                "title": props.get("title") or slug,
                "theme": props.get("theme") or self.app_config("default_theme", "dark"),
                "aspect": props.get("aspect") or self.app_config("default_aspect", "16:9"),
                "visual_style": _normalize_visual_style(props.get("visual_style"), default_visual_style),
                "created": props.get("created", ""),
                "updated": props.get("updated", ""),
                "slide_count": slide_count,
                "system": str(props.get("system", "")).lower() in ("true", "1", "yes"),
            }
        )
    # System decks pinned to top; user decks below sorted by updated desc.
    out.sort(key=lambda d: (
        0 if d.get("system") else 1,
        -(int(d.get("updated", "").replace("-", "") or 0) if d.get("updated") else 0),
    ))
    return out


async def list_all(self) -> list[dict]:
    """Boards-as-view-layer contract."""
    return await self.list_decks()


async def set_field(self, id: str, field: str, value) -> dict:
    if field not in self.SETTABLE_FIELDS:
        return {"error": f"field '{field}' not settable"}
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    if field == "visual_style":
        value = _normalize_visual_style(
            value,
            self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE),
        )
    self.vault_update(path, {field: value})
    await self.emit("ppt:updated", {"id": id, "field": field})
    return {"ok": True}


async def create_deck(
    self,
    title: str,
    outline: str = "",
    allowed_elements: list[str] | None = None,
    visual_style: str = "",
) -> dict:
    """Create a new deck. If outline is provided, draft slides via think().

    `allowed_elements` filters the AI's slide-surface palette (bullets,
    quote, table, code, image, vault, screenshot, embed, divider). Stored
    in frontmatter so subsequent regenerations honor the same palette.
    """
    slug = _slugify(title)
    rel = f"{self._ppt_dir()}/{slug}.md"
    if self._path_for(slug):
        slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
        rel = f"{self._ppt_dir()}/{slug}.md"
    elements = _normalize_elements(allowed_elements)
    default_visual_style = self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE)
    body = (
        await self._draft_body(title, outline, elements)
        if outline.strip()
        else _starter_body(title)
    )
    fm = {
        "title": title,
        "type": "deck",
        "lifecycle": "living",
        "theme": self.app_config("default_theme", "dark"),
        "aspect": self.app_config("default_aspect", "16:9"),
        "visual_style": _normalize_visual_style(visual_style, default_visual_style),
        "created": datetime.now().strftime("%Y-%m-%d"),
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "allowed_elements": elements,
        "tags": ["deck"],
    }
    self.vault_create_note(rel, fm, body)
    await self.emit("ppt:created", {"id": slug, "title": title})
    return {"id": slug, "path": rel, "title": title}


async def delete_deck(self, id: str) -> dict:
    """Delete a deck note from the vault. Idempotent — missing id returns ok.

    Refuses to delete decks marked `system: true` in frontmatter (the
    bundled tutorial / readme deck). Editing them is fine; deleting isn't.
    """
    path = self._path_for(id)
    if not path:
        return {"ok": True, "missing": True}
    props = self.vault_get_properties(path) or {}
    if str(props.get("system", "")).lower() in ("true", "1", "yes"):
        return {"error": f"Deck '{id}' is a system deck (tutorial / readme) and cannot be deleted. Edit its content instead."}
    try:
        (self.vault_root / path).unlink()
    except FileNotFoundError:
        pass
    await self.emit("ppt:deleted", {"id": id, "path": path})
    return {"ok": True, "id": id}


async def get_deck(self, id: str) -> dict:
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    text = await self.read(path)
    # Resolve embed_base: deck frontmatter > app config > empty (current host).
    # Stored in fm so a deck can pin its rendering target without changing
    # machine config (e.g. shared deck pointing at the public demo VPS).
    fm_peek, _ = _split_frontmatter(text)
    embed_base = (
        str(fm_peek.get("embed_base") or "").strip()
        or str(self.app_config("embed_base", "")).strip()
    )
    parsed = parse_deck(
        text,
        asset_url_prefix=f"/ppt/api/asset/{id}",
        embed_base=embed_base,
    )
    props = self.vault_get_properties(path) or {}
    default_visual_style = self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE)
    slides = parsed["slides"]
    # If narration was generated, attach a per-slide audio_url that the
    # renderer auto-plays on slide enter. Existence-checked so missing
    # files don't break playback.
    narration_stale = False
    narration_on = parsed["frontmatter"].get("narration") or props.get("narration")
    if narration_on and str(narration_on).lower() not in ("false", "0", "no", ""):
        vault = self.kernel.config.notes_path
        deck_dir = vault / f"{self._ppt_dir()}/{id}" if vault else None
        # Build hash → mp3 filename map from sidecars. Legacy decks
        # narrated before sidecars existed have no .txt files → fall back
        # to positional matching for those.
        hash_to_file: dict[str, str] = {}
        has_sidecars = False
        if deck_dir and deck_dir.exists():
            for txt in deck_dir.glob("narration-*.txt"):
                has_sidecars = True
                mp3 = txt.with_suffix(".mp3")
                if not mp3.exists():
                    continue
                try:
                    h = txt.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if h:
                    hash_to_file[h] = mp3.name
        matched = 0
        for i, slide in enumerate(slides, start=1):
            fname = None
            if has_sidecars:
                spoken = slide.get("say") or slide.get("notes") or ""
                fname = hash_to_file.get(_notes_hash(spoken))
            else:
                legacy = f"narration-{i:02d}.mp3"
                if deck_dir and (deck_dir / legacy).exists():
                    fname = legacy
            if fname:
                slide["audio_url"] = f"/ppt/api/asset/{id}/{fname}"
                matched += 1
        # Stale flag = narration is on but no slide matched any audio.
        # UI can prompt the user to re-narrate.
        narration_stale = matched == 0 and (
            bool(hash_to_file)
            or (deck_dir and deck_dir.exists() and any(deck_dir.glob("narration-*.mp3")))
        )
    return {
        "id": id,
        "path": path,
        "raw": text,
        "frontmatter": parsed["frontmatter"],
        "properties": props,
        "slides": slides,
        "narration_stale": narration_stale,
        "theme": parsed["frontmatter"].get("theme")
        or props.get("theme")
        or self.app_config("default_theme", "dark"),
        "aspect": parsed["frontmatter"].get("aspect")
        or props.get("aspect")
        or self.app_config("default_aspect", "16:9"),
        "visual_style": _normalize_visual_style(
            parsed["frontmatter"].get("visual_style") or props.get("visual_style"),
            default_visual_style,
        ),
    }


async def save_deck(self, id: str, raw: str) -> dict:
    """Overwrite a deck's full markdown (frontmatter + body).

    Narration audio is paired by Notes-hash sidecar (see `narrate_deck`),
    so unchanged slides keep their audio across edits/reorders. Slides
    whose Notes were edited simply lose their audio_url at render time
    until re-narrated; orphaned mp3s sit in the deck folder until the
    next narrate run overwrites them.
    """
    path = self._path_for(id)
    if not path:
        return {"error": "Deck not found"}
    await self.write(path, raw)
    self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
    await self.emit("ppt:updated", {"id": id})
    return {"ok": True}


async def _draft_body(
    self, title: str, outline: str, allowed_elements: list[str] | None = None
) -> str:
    system = build_outline_system(allowed_elements)
    prompt = f"Topic: {title}\n\nOutline / hints:\n{outline}\n\nReturn the deck markdown."
    try:
        text = await self.think(prompt, system=system, temperature=0.6)
    except Exception:
        return _starter_body(title)
    text = text.strip()
    if text.startswith("```"):
        # strip code fences if the model ignored the rule
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text


@web_route("GET", "/api/decks")
async def api_list(self, request):
    return await self.list_decks()


@web_route("POST", "/api/decks")
async def api_create(self, request):
    body = await request.json()
    return await self.create_deck(
        body.get("title", ""),
        body.get("outline", ""),
        body.get("allowed_elements"),
        body.get("visual_style", ""),
    )


@web_route("GET", "/api/decks/{id}")
async def api_get(self, request):
    return await self.get_deck(request.path_params["id"])


@web_route("DELETE", "/api/decks/{id}")
async def api_delete(self, request):
    return await self.delete_deck(request.path_params["id"])


@web_route("PUT", "/api/decks/{id}")
async def api_save(self, request):
    body = await request.json()
    return await self.save_deck(request.path_params["id"], body.get("raw", ""))


@web_route("PATCH", "/api/decks/{id}")
async def api_update_fields(self, request):
    body = await request.json()
    deck_id = request.path_params["id"]
    updates = {k: v for k, v in body.items() if k in self.SETTABLE_FIELDS}
    if not updates:
        return {"error": "No settable fields supplied"}
    for field, value in updates.items():
        result = await self.set_field(deck_id, field, value)
        if result.get("error"):
            return result
    return {"ok": True, "id": deck_id, "updated": sorted(updates)}


@web_route("POST", "/api/decks/{id}/preview")
async def api_preview(self, request):
    body = await request.json()
    deck_id = request.path_params["id"]
    raw = body.get("raw", "")
    fm_peek, _ = _split_frontmatter(raw)
    embed_base = (
        str(fm_peek.get("embed_base") or "").strip()
        or str(self.app_config("embed_base", "")).strip()
    )
    parsed = parse_deck(
        raw,
        asset_url_prefix=f"/ppt/api/asset/{deck_id}",
        embed_base=embed_base,
    )
    fm = parsed["frontmatter"]
    default_visual_style = self.app_config("default_visual_style", DEFAULT_VISUAL_STYLE)
    return {
        "id": deck_id,
        "frontmatter": fm,
        "slides": parsed["slides"],
        "theme": fm.get("theme") or self.app_config("default_theme", "dark"),
        "aspect": fm.get("aspect") or self.app_config("default_aspect", "16:9"),
        "visual_style": _normalize_visual_style(fm.get("visual_style"), default_visual_style),
    }
