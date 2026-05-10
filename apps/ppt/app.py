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

import json as _json
import re
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, parse_llm_json, web_route

from .parser import (  # noqa: F401  (re-exported for tests / external imports)
    ALL_ELEMENTS,
    DECK_OUTLINE_SYSTEM,
    DEFAULT_ELEMENTS,
    INTENTS,
    _STANDALONE_HTML,
    _count_slides,
    _HR_RE,
    _html_escape,
    _IMAGE_PLACEHOLDER_RE,
    _load_deck_js,
    _normalize_elements,
    _slugify,
    _extract_notes,
    _SAY_LINE_RE,
    _split_frontmatter,
    _starter_body,
    SPEAKIFY_SYSTEM,
    build_gen_from_plan_system,
    build_outline_system,
    build_plan_system,
    parse_deck,
)


def _notes_hash(notes: str) -> str:
    """Stable short hash of a slide's Notes content. Whitespace-normalized.

    Pairs each `narration-NN.mp3` with a `narration-NN.txt` sidecar so the
    renderer can re-attach audio to the slide that actually authored it,
    even after slides are reordered or one slide's notes are edited.
    """
    import hashlib
    norm = " ".join((notes or "").split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


class PptApp(BaseApp):
    SETTABLE_FIELDS = {"title", "theme", "aspect"}

    async def setup(self):
        await super().setup()

    # ── public methods (callable from other apps) ────────────────

    async def list_decks(self) -> list[dict]:
        """Return all decks tagged `deck`, sorted by updated desc."""
        rows = self.vault_query(tags=["deck"]) or []
        out = []
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
        self.vault_update(path, {field: value})
        await self.emit("ppt:updated", {"id": id, "field": field})
        return {"ok": True}

    async def create_deck(
        self, title: str, outline: str = "", allowed_elements: list[str] | None = None
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
            "created": datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "allowed_elements": elements,
            "tags": ["deck"],
        }
        self.vault_create_note(rel, fm, body)
        await self.emit("ppt:created", {"id": slug, "title": title})
        return {"id": slug, "path": rel, "title": title}

    async def plan_deck(
        self,
        title: str,
        outline: str = "",
        audience: str = "",
        duration_min: int = 5,
        allowed_elements: list[str] | None = None,
        source_path: str = "",
    ) -> dict:
        """Stage 1 of plan-first generation: produce a structured plan as JSON.

        The plan contains intent, audience, per-slide surface + beat + headline.
        It is *not* persisted — the user reviews/edits, then calls
        `generate_from_plan` to materialize the deck.
        """
        elements = _normalize_elements(allowed_elements)
        system = build_plan_system(elements)
        source_text = ""
        if source_path:
            try:
                source_text = await self.read(source_path)
            except Exception:
                source_text = ""
            if len(source_text) > 6000:
                source_text = source_text[:6000] + "\n\n...[truncated]"
        parts = [
            f"Title (user input, refine if useful): {title}",
            f"Audience: {audience or 'unspecified — infer from outline'}",
            f"Target duration: {duration_min} minutes",
            f"Outline / hints:\n{outline.strip() or '(none — derive structure from title)'}",
        ]
        if source_text:
            parts.append("\nSource material to plan from:")
            parts.append(source_text.strip())
        parts.append("\nReturn the plan JSON now.")
        prompt = "\n\n".join(parts)
        try:
            text = await self.think(prompt, system=system, temperature=0.4)
        except Exception as e:
            return {"error": f"think failed: {e}"}
        plan = parse_llm_json(text, fallback=None)
        if not isinstance(plan, dict) or "slides" not in plan:
            return {"error": "could not parse plan JSON", "raw": text[:1000]}
        slides = plan.get("slides") or []
        if not isinstance(slides, list) or not slides:
            return {"error": "plan has no slides", "raw": text[:1000]}
        clean_slides = []
        for s in slides:
            if not isinstance(s, dict):
                continue
            surface = str(s.get("surface", "bullets")).strip().lower()
            if surface not in elements:
                surface = "bullets"
            clean_slides.append({
                "surface": surface,
                "beat": str(s.get("beat", "")).strip(),
                "headline": str(s.get("headline", "")).strip(),
            })
        intent = str(plan.get("intent", "")).strip().lower()
        if intent not in INTENTS:
            intent = "teach"
        return {
            "title": str(plan.get("title", title)).strip() or title,
            "subtitle": str(plan.get("subtitle", "")).strip(),
            "intent": intent,
            "audience": str(plan.get("audience", audience)).strip(),
            "duration_min": int(plan.get("duration_min", duration_min) or duration_min),
            "allowed_elements": elements,
            "slides": clean_slides,
        }

    async def generate_from_plan(self, plan: dict, source_path: str = "") -> dict:
        """Stage 2 of plan-first generation: render an approved plan to a deck.

        Saves a new deck note and returns its id + path.
        """
        if not isinstance(plan, dict):
            return {"error": "plan must be an object"}
        title = str(plan.get("title", "")).strip()
        if not title:
            return {"error": "plan.title is required"}
        slides = plan.get("slides") or []
        if not isinstance(slides, list) or not slides:
            return {"error": "plan.slides is required"}
        elements = _normalize_elements(plan.get("allowed_elements"))
        intent = str(plan.get("intent", "")).strip().lower()
        if intent not in INTENTS:
            intent = ""

        slug = _slugify(title)
        rel = f"{self._ppt_dir()}/{slug}.md"
        if self._path_for(slug):
            slug = f"{slug}-{datetime.now().strftime('%H%M%S')}"
            rel = f"{self._ppt_dir()}/{slug}.md"

        source_text = ""
        if source_path:
            try:
                source_text = await self.read(source_path)
            except Exception:
                source_text = ""
            if len(source_text) > 6000:
                source_text = source_text[:6000] + "\n\n...[truncated]"

        plan_for_llm = {
            "title": title,
            "subtitle": plan.get("subtitle", ""),
            "intent": intent,
            "audience": plan.get("audience", ""),
            "slides": [
                {
                    "surface": s.get("surface", "bullets"),
                    "headline": s.get("headline", ""),
                    "beat": s.get("beat", ""),
                }
                for s in slides
                if isinstance(s, dict)
            ],
        }
        system = build_gen_from_plan_system(elements, intent)
        prompt_parts = [
            "Approved plan (JSON):",
            "```json",
            _json.dumps(plan_for_llm, ensure_ascii=False, indent=2),
            "```",
        ]
        if source_text:
            prompt_parts.append("\nSource material to draw content from:")
            prompt_parts.append(source_text.strip())
        prompt_parts.append("\nWrite the deck markdown now. Honor every slide's surface.")
        prompt = "\n".join(prompt_parts)
        try:
            text = await self.think(prompt, system=system, temperature=0.6)
        except Exception as e:
            return {"error": f"think failed: {e}"}
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        fm = {
            "title": title,
            "type": "deck",
            "lifecycle": "living",
            "theme": self.app_config("default_theme", "dark"),
            "aspect": self.app_config("default_aspect", "16:9"),
            "created": datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "allowed_elements": elements,
            "intent": intent,
            "audience": plan.get("audience", ""),
            "tags": ["deck"],
        }
        self.vault_create_note(rel, fm, text)
        await self.emit("ppt:created", {"id": slug, "title": title, "from": "plan"})
        return {"id": slug, "path": rel, "title": title, "slides": len(slides)}

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

    # ── bridges to other apps ───────────────────────────────────

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
            "theme": self.app_config("default_theme", "dark"),
            "aspect": self.app_config("default_aspect", "16:9"),
            "created": datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().strftime("%Y-%m-%d"),
            "source_board": board_id,
            "tags": ["deck", "canvas-derived"],
        }
        self.vault_create_note(rel, fm, body)
        await self.emit("ppt:created", {"id": slug, "from": "canvas", "board": board_id})
        return {"ok": True, "id": slug, "slides": len(ordered), "open": f"/ppt/#{slug}"}

    async def regenerate_deck(
        self,
        id: str,
        direction: str,
        allowed_elements: list[str] | None = None,
        scope: str = "whole",
        slide_index: int | None = None,
        selection: str = "",
        selection_start: int | None = None,
        selection_end: int | None = None,
    ) -> dict:
        """Rewrite a deck via `self.think()` guided by a `direction` string.

        `scope` selects what the model rewrites:
          - "whole"     — full deck (default)
          - "slide"     — just slide at `slide_index` (0-based)
          - "selection" — just the substring [selection_start, selection_end]
                          in the raw markdown; falls back to literal match on
                          `selection` if offsets are missing
        """
        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        current = await self.read(path)
        fm, body = _split_frontmatter(current)
        title = fm.get("title") or id

        if allowed_elements is None:
            allowed_elements = fm.get("allowed_elements") or None
        elements = _normalize_elements(allowed_elements)
        system = build_outline_system(elements)

        # Scoped rewrites — same system prompt, different ask + splice strategy.
        if scope == "slide" and slide_index is not None:
            slides = _HR_RE.split(body)
            if slide_index < 0 or slide_index >= len(slides):
                return {"error": f"slide_index {slide_index} out of range (0-{len(slides) - 1})"}
            target = slides[slide_index].strip("\n")
            prompt = (
                f"Topic: {title}\n\n"
                f"Direction (apply to ONE slide only): {direction}\n\n"
                f"Slide to rewrite (slide {slide_index + 1} of {len(slides)}):\n\n"
                f"```markdown\n{target}\n```\n\n"
                "Return ONLY the rewritten slide's markdown — no `---` separators, no other slides, "
                "no frontmatter. Keep `Notes:` lines if they make sense. Same surface kind unless the "
                "direction asks otherwise."
            )
            new_chunk = await self._call_think(prompt, system)
            if new_chunk is None:
                return {"error": "think failed"}
            slides[slide_index] = new_chunk
            new_body = "\n\n---\n\n".join(s.strip("\n") for s in slides if s.strip())
        elif scope == "selection":
            if selection_start is not None and selection_end is not None and selection_end > selection_start:
                target = current[selection_start:selection_end]
                prompt = (
                    f"Topic: {title}\n\n"
                    f"Direction (apply to this snippet only): {direction}\n\n"
                    f"Snippet to rewrite:\n\n```markdown\n{target}\n```\n\n"
                    "Return ONLY the rewritten snippet — no other content, no frontmatter, "
                    "no slide separators unless the snippet already had them."
                )
                new_chunk = await self._call_think(prompt, system)
                if new_chunk is None:
                    return {"error": "think failed"}
                new_raw = current[:selection_start] + new_chunk + current[selection_end:]
                await self.write(path, new_raw)
                self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
                await self.emit("ppt:updated", {"id": id, "field": "selection"})
                return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(_split_frontmatter(new_raw)[1]))}
            elif selection and selection in current:
                prompt = (
                    f"Topic: {title}\n\n"
                    f"Direction (apply to this snippet only): {direction}\n\n"
                    f"Snippet to rewrite:\n\n```markdown\n{selection}\n```\n\n"
                    "Return ONLY the rewritten snippet."
                )
                new_chunk = await self._call_think(prompt, system)
                if new_chunk is None:
                    return {"error": "think failed"}
                new_raw = current.replace(selection, new_chunk, 1)
                await self.write(path, new_raw)
                self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
                await self.emit("ppt:updated", {"id": id, "field": "selection"})
                return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(_split_frontmatter(new_raw)[1]))}
            else:
                return {"error": "selection scope requires selection_start/end or matching selection text"}
        else:
            # whole deck
            prompt = (
                f"Topic: {title}\n\n"
                f"Direction: {direction}\n\n"
                "Existing deck (rewrite this; keep what works, change what the direction asks):\n\n"
                f"```markdown\n{current}\n```\n\n"
                "Return only the new deck markdown body (NO frontmatter — I'll re-attach it). "
                "Slide separators stay as `---` on their own line."
            )
            text = await self._call_think(prompt, system)
            if text is None:
                return {"error": "think failed"}
            new_body = text

        # Re-attach the original frontmatter so theme/aspect/title persist.
        from emptyos.runtime.vault_index import _serialize_fm

        fm["updated"] = datetime.now().strftime("%Y-%m-%d")
        fm["allowed_elements"] = elements
        new_raw = _serialize_fm(fm) + "\n\n" + new_body.lstrip()
        await self.write(path, new_raw)
        self.vault_update(path, {"updated": fm["updated"], "allowed_elements": elements})
        await self.emit("ppt:updated", {"id": id, "field": f"regenerated:{scope}"})
        return {"ok": True, "raw": new_raw, "slides": len(_HR_RE.split(new_body))}

    async def _call_think(self, prompt: str, system: str) -> str | None:
        """Single-shot think() with code-fence + frontmatter stripping."""
        try:
            text = await self.think(prompt, system=system, temperature=0.6)
        except Exception:
            return None
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = re.sub(r"^---\s*\n.*?\n---\s*\n+", "", text, flags=re.DOTALL)
        return text.strip()

    async def resolve_images(self, id: str) -> dict:
        """Replace every image placeholder in a deck with a real image file.

        Three placeholder kinds, dispatched to different sources:

        - `![image: <prompt>]`       → AI generation via `self.draw`
        - `![vault: <name-or-glob>]` → existing image from the vault, matched
                                       by filename substring or glob
        - `![screenshot: <url>]`     → headless Playwright snapshot of any URL

        Each resolved image is copied into a per-deck assets folder
        (`<vault>/<ppt_dir>/<deck-id>/slide-NN.png`) and the placeholder is
        rewritten to `![[<filename>]]`. Idempotent — already-resolved slides
        are left alone.
        """
        import shutil

        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        raw = await self.read(path)
        matches = list(_IMAGE_PLACEHOLDER_RE.finditer(raw))
        if not matches:
            return {"resolved": 0, "skipped": 0, "errors": [], "message": "No placeholders"}

        vault = self.kernel.config.notes_path
        if not vault:
            return {"error": "No vault configured"}
        deck_assets_rel = f"{self._ppt_dir()}/{id}"
        deck_assets_dir = vault / deck_assets_rel
        deck_assets_dir.mkdir(parents=True, exist_ok=True)

        out = raw
        resolved = 0
        errors: list[str] = []
        # Walk in reverse so substring offsets stay valid as we splice.
        for i, m in enumerate(reversed(matches), 1):
            kind = m.group(1).lower()
            arg = m.group(2).strip()
            n = len(matches) - i + 1
            filename = f"slide-{n:02d}.png"
            target = deck_assets_dir / filename
            try:
                src_path = await self._resolve_one_image(kind, arg, target, vault)
                if not src_path:
                    errors.append(f"slide {n} ({kind}): not resolvable")
                    continue
                if str(src_path) != str(target):
                    shutil.copy2(str(src_path), str(target))
                out = out[: m.start()] + f"![[{filename}]]" + out[m.end():]
                resolved += 1
            except Exception as e:
                errors.append(f"slide {n} ({kind}): {e}")

        if resolved:
            await self.write(path, out)
            self.vault_update(path, {"updated": datetime.now().strftime("%Y-%m-%d")})
            await self.emit("ppt:updated", {"id": id, "field": "images"})
        return {
            "resolved": resolved,
            "skipped": len(matches) - resolved - len(errors),
            "errors": errors,
        }

    async def _resolve_one_image(
        self, kind: str, arg: str, target: Path, vault: Path
    ) -> Path | None:
        """Dispatch one placeholder. Returns absolute source path, or `target` itself
        for sources that write directly to the destination (screenshot, image)."""
        if kind == "image":
            filename = await self.draw(arg)
            if not filename:
                return None
            ok = await self.download_drawn_image(filename, target)
            return target if ok else None
        if kind == "vault":
            return self._find_vault_image(arg, vault)
        if kind == "screenshot":
            ok = await self._screenshot_url(arg, target)
            return target if ok else None
        return None

    def _find_vault_image(self, query: str, vault: Path) -> Path | None:
        """Match an image file in the vault. Accepts:
        - exact relative path: `30_Resources/foo/bar.png`
        - bare filename: `bar.png`
        - filename stem substring: `cable-diagram` (matches `cable-diagram-v3.png`)
        Searches images-only (png/jpg/jpeg/gif/svg/webp). Returns first match
        sorted by mtime desc to prefer recent edits.
        """
        q = query.strip().strip('"').strip("'")
        if not q:
            return None
        # Exact relative path
        cand = vault / q
        if cand.exists() and cand.is_file():
            return cand
        # Glob / substring against filenames anywhere in the vault
        exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        q_lower = q.lower()
        hits: list[Path] = []
        for p in vault.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            name_lower = p.name.lower()
            if q_lower == name_lower or q_lower in name_lower:
                hits.append(p)
            if len(hits) >= 200:
                break
        if not hits:
            return None
        hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0]

    async def _screenshot_url(self, url: str, target: Path) -> bool:
        """Headless full-page screenshot via Playwright. Writes PNG to `target`.
        Public URLs only — auth-gated EmptyOS pages will capture the login page.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError("playwright not installed: pip install playwright && playwright install chromium")
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await ctx.new_page()
                await page.goto(url, wait_until="networkidle", timeout=20000)
                await page.wait_for_timeout(500)
                await page.screenshot(path=str(target), full_page=True)
                return target.exists()
            finally:
                await browser.close()

    async def narrate_deck(self, id: str, slide_index: int | None = None) -> dict:
        """Generate per-slide TTS narration. Speaker notes → audio files.

        For each slide whose `Notes:` block is non-empty, calls
        `self.speak(notes)` and copies the resulting audio into the per-deck
        assets folder as `narration-NN.mp3`. Sets `narration: true` in the
        deck's frontmatter so the renderer auto-plays them in present mode.

        Idempotent — re-running regenerates every track. To skip slides that
        already have a track, delete the deck folder first.
        """
        import shutil

        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        deck = await self.get_deck(id)
        if "error" in deck:
            return deck

        vault = self.kernel.config.notes_path
        if not vault:
            return {"error": "No vault configured"}
        deck_dir = vault / f"{self._ppt_dir()}/{id}"
        deck_dir.mkdir(parents=True, exist_ok=True)

        slides = deck.get("slides") or []
        generated = 0
        skipped = 0
        errors: list[str] = []
        # `slide_index` is 1-based when provided (matches what the UI shows).
        # None = narrate every slide. Out-of-range = error.
        if slide_index is not None:
            if slide_index < 1 or slide_index > len(slides):
                return {"error": f"slide_index {slide_index} out of range (1-{len(slides)})"}
        for idx, slide in enumerate(slides, start=1):
            if slide_index is not None and idx != slide_index:
                continue
            # `Say:` (audience-facing) wins over `Notes:` (presenter-only).
            # Notes still acts as a fallback so legacy decks keep working.
            spoken = (slide.get("say") or slide.get("notes") or "").strip()
            if not spoken:
                skipped += 1
                continue
            try:
                src = await self.speak(spoken)
            except Exception as e:
                errors.append(f"slide {idx}: speak failed: {e}")
                continue
            if not src:
                errors.append(f"slide {idx}: speak returned empty")
                continue
            try:
                src_path = Path(src) if not isinstance(src, Path) else src
                if not src_path.exists():
                    errors.append(f"slide {idx}: audio file missing at {src_path}")
                    continue
                target = deck_dir / f"narration-{idx:02d}.mp3"
                shutil.copy2(str(src_path), str(target))
                # Hash sidecar — lets get_deck re-pair audio to its authoring
                # slide after a reorder or single-slide edit.
                (deck_dir / f"narration-{idx:02d}.txt").write_text(
                    _notes_hash(spoken), encoding="utf-8"
                )
                generated += 1
            except Exception as e:
                errors.append(f"slide {idx}: copy failed: {e}")

        if generated:
            # Count actual mp3s on disk so per-slide narrates don't clobber
            # the total when re-running just one slide.
            total = sum(1 for _ in deck_dir.glob("narration-*.mp3"))
            self.vault_update(path, {
                "narration": True,
                "narration_count": total,
                "updated": datetime.now().strftime("%Y-%m-%d"),
            })
            await self.emit("ppt:updated", {"id": id, "field": "narration"})
        return {"generated": generated, "skipped": skipped, "errors": errors, "slide_index": slide_index}

    async def set_narration(self, id: str, enabled: bool) -> dict:
        """Toggle the `narration` frontmatter flag without touching audio files.

        Off → renderer skips auto-play (audio_url not attached) but mp3s/sidecars
        remain so flipping it back on restores playback instantly.
        """
        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        self.vault_update(path, {
            "narration": bool(enabled),
            "updated": datetime.now().strftime("%Y-%m-%d"),
        })
        await self.emit("ppt:updated", {"id": id, "field": "narration_toggle"})
        return {"ok": True, "narration": bool(enabled)}

    async def speakify_deck(self, id: str, slide_index: int | None = None, overwrite: bool = False) -> dict:
        """Rewrite presenter Notes into audience-facing Say: lines via think().

        Notes are coaching ("Walk through...", "Emphasize that...") — read aloud
        they sound like director's directions, not a talk. This converts each
        slide's notes into the actual sentences a speaker would say to the
        audience, in first person, then injects them as `Say:` lines into the
        deck markdown alongside the original Notes (which stay as memory aids).

        - slide_index: 1-based; None = every slide.
        - overwrite: re-speakify slides that already have a Say: line.
        """
        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        text = await self.read(path)
        fm, body = _split_frontmatter(text)
        chunks = _HR_RE.split(body)

        # Walk chunks, but only count non-empty ones as "slides" (matches
        # parse_deck's filtering so slide_index here means the same thing as
        # in narrate_deck and the UI).
        slide_positions: list[int] = []
        for i, c in enumerate(chunks):
            if c.strip():
                slide_positions.append(i)

        if slide_index is not None:
            if slide_index < 1 or slide_index > len(slide_positions):
                return {"error": f"slide_index {slide_index} out of range (1-{len(slide_positions)})"}

        rewritten = 0
        skipped = 0
        errors: list[str] = []

        for n, pos in enumerate(slide_positions, start=1):
            if slide_index is not None and n != slide_index:
                continue
            chunk = chunks[pos]
            clean_md, notes, existing_say = _extract_notes(chunk)
            if not notes:
                skipped += 1
                continue
            if existing_say and not overwrite:
                skipped += 1
                continue
            slide_text = clean_md.strip() or "(visual-only slide)"
            prompt = (
                f"Slide content:\n\n{slide_text}\n\n"
                f"Presenter notes (director's coaching, NOT to be spoken verbatim):\n\n{notes}\n\n"
                "Rewrite as the actual spoken script for this slide."
            )
            try:
                spoken = await self.think(prompt, system=SPEAKIFY_SYSTEM, temperature=0.5)
            except Exception as e:
                errors.append(f"slide {n}: think failed: {e}")
                continue
            spoken = (spoken or "").strip().strip('"').strip("'").replace("\n", " ").strip()
            if not spoken:
                errors.append(f"slide {n}: think returned empty")
                continue

            if existing_say:
                new_chunk = _SAY_LINE_RE.sub(f"Say: {spoken}", chunk, count=1)
            else:
                # Append a Say: line at the end of the chunk so it sits next to
                # any existing Notes: line.
                new_chunk = chunk.rstrip() + f"\n\nSay: {spoken}\n"
            chunks[pos] = new_chunk
            rewritten += 1

        if rewritten:
            from emptyos.runtime.vault_index import _serialize_fm
            fm["updated"] = datetime.now().strftime("%Y-%m-%d")
            # Rejoin with bare `---` — _HR_RE consumed only the dash line, not
            # the surrounding newlines, so each chunk keeps its own padding.
            new_body = "---".join(chunks)
            new_raw = _serialize_fm(fm) + "\n" + new_body.lstrip("\n")
            await self.write(path, new_raw)
            self.vault_update(path, {"updated": fm["updated"]})
            await self.emit("ppt:updated", {"id": id, "field": "speakify"})
        return {"rewritten": rewritten, "skipped": skipped, "errors": errors}

    async def export_html(self, id: str) -> dict:
        """Export a deck to standalone HTML in {vault}/30_Resources/Published/decks/.

        Embed targets are re-resolved with an export-tier `embed_base` default
        (`apps.ppt.export_embed_base`, falling back to https://demo.binbian.net)
        so iframes in the standalone bundle point at a publicly-reachable host
        rather than the localhost daemon that rendered them.
        """
        path = self._path_for(id)
        if not path:
            return {"error": "Deck not found"}
        text = await self.read(path)
        fm_peek, _ = _split_frontmatter(text)
        export_base = (
            str(fm_peek.get("embed_base") or "").strip()
            or str(self.app_config("export_embed_base", "")).strip()
            or str(self.app_config("embed_base", "")).strip()
            or "https://demo.binbian.net"
        )
        parsed = parse_deck(
            text,
            asset_url_prefix=f"/ppt/api/asset/{id}",
            embed_base=export_base,
        )
        deck = {
            "frontmatter": parsed["frontmatter"],
            "slides": parsed["slides"],
            "theme": parsed["frontmatter"].get("theme") or self.app_config("default_theme", "dark"),
            "aspect": parsed["frontmatter"].get("aspect") or self.app_config("default_aspect", "16:9"),
        }
        decks_dir = self.vault_config("published_decks_dir", "30_Resources/Published/decks")
        published_rel = f"{decks_dir}/{id}.html"
        vault = self.kernel.config.notes_path
        if not vault:
            return {"error": "No vault configured"}
        deck_js = _load_deck_js()
        if not deck_js:
            return {"error": "Renderer asset eos-deck.js not found"}
        slides_json = _json.dumps(
            [
                {"html": s["html"], "notes": s["notes"], "audio_url": s.get("audio_url", "")}
                for s in deck["slides"]
            ],
            ensure_ascii=False,
        )
        title = deck["frontmatter"].get("title") or id
        theme = deck["theme"]
        aspect = deck["aspect"]
        html = _STANDALONE_HTML.format(
            title=_html_escape(title),
            theme=theme,
            aspect=aspect,
            slides_json=slides_json,
            deck_js=deck_js,
        )
        self.vault_write_at(published_rel, html)
        return {"ok": True, "path": str(vault / published_rel), "rel": published_rel}

    # ── private helpers ─────────────────────────────────────────

    def _ppt_dir(self) -> str:
        return self.vault_config("ppt_dir", "30_Resources/EmptyOS/ppt")

    def _path_for(self, id: str) -> str | None:
        rows = self.vault_query(tags=["deck"]) or []
        for r in rows:
            if Path(r.get("path", "")).stem == id:
                return r["path"]
        return None

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

    # ── hub panel ───────────────────────────────────────────────

    async def panel_recent_decks(self) -> list[dict] | None:
        decks = await self.list_decks()
        if not decks:
            return None
        return [
            {"label": d["title"], "link": f"/ppt/#{d['id']}", "detail": f"{d['slide_count']} slides"}
            for d in decks[:3]
        ]

    # ── voice intent ────────────────────────────────────────────

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

    # ── CLI ─────────────────────────────────────────────────────

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

    # ── HTTP API ────────────────────────────────────────────────

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
        )

    @web_route("GET", "/api/elements")
    async def api_elements(self, request):
        """List the available slide-surface elements + sensible defaults."""
        return {
            "all": ALL_ELEMENTS,
            "defaults": list(DEFAULT_ELEMENTS),
            "labels": {
                "bullets": "Bullet lists",
                "quote": "Pull quotes",
                "table": "Tables",
                "code": "Code blocks",
                "image": "AI-generated images",
                "vault": "Existing vault images",
                "screenshot": "URL screenshots",
                "embed": "Live page embeds",
                "divider": "Section dividers",
                "mermaid": "Diagrams (flowchart / sequence / mindmap)",
                "chart": "Charts (bar / line / pie / doughnut)",
                "narration": "Auto-narration (TTS of slide notes)",
                "audio": "Audio clips (vault / URL)",
                "video": "Video (vault / YouTube / URL)",
            },
        }

    @web_route("GET", "/api/intents")
    async def api_intents(self, request):
        """List deck intents — used by the plan-first new-deck flow."""
        return {
            "intents": [
                {"id": k, "label": v["label"], "guidance": v["guidance"]}
                for k, v in INTENTS.items()
            ],
        }

    @web_route("POST", "/api/plan")
    async def api_plan(self, request):
        body = await request.json()
        return await self.plan_deck(
            title=body.get("title", ""),
            outline=body.get("outline", ""),
            audience=body.get("audience", ""),
            duration_min=int(body.get("duration_min", 5) or 5),
            allowed_elements=body.get("allowed_elements"),
            source_path=body.get("source_path", ""),
        )

    @web_route("POST", "/api/generate-from-plan")
    async def api_generate_from_plan(self, request):
        body = await request.json()
        return await self.generate_from_plan(
            plan=body.get("plan") or {},
            source_path=body.get("source_path", ""),
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

    @web_route("POST", "/api/decks/{id}/export")
    async def api_export(self, request):
        return await self.export_html(request.path_params["id"])

    @web_route("POST", "/api/decks/{id}/resolve-images")
    async def api_resolve_images(self, request):
        return await self.resolve_images(request.path_params["id"])

    @web_route("POST", "/api/decks/{id}/narrate")
    async def api_narrate(self, request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        idx = body.get("slide_index")
        if idx is not None:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                return {"error": "slide_index must be an integer"}
        return await self.narrate_deck(request.path_params["id"], slide_index=idx)

    @web_route("POST", "/api/decks/{id}/narration-toggle")
    async def api_narration_toggle(self, request):
        body = await request.json()
        return await self.set_narration(request.path_params["id"], bool(body.get("enabled")))

    @web_route("POST", "/api/decks/{id}/speakify")
    async def api_speakify(self, request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        idx = body.get("slide_index")
        if idx is not None:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                return {"error": "slide_index must be an integer"}
        return await self.speakify_deck(
            request.path_params["id"],
            slide_index=idx,
            overwrite=bool(body.get("overwrite")),
        )

    @web_route("POST", "/api/decks/{id}/regenerate")
    async def api_regenerate(self, request):
        body = await request.json()
        return await self.regenerate_deck(
            request.path_params["id"],
            body.get("direction", ""),
            body.get("allowed_elements"),
        )

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
        return {
            "id": deck_id,
            "frontmatter": fm,
            "slides": parsed["slides"],
            "theme": fm.get("theme") or self.app_config("default_theme", "dark"),
            "aspect": fm.get("aspect") or self.app_config("default_aspect", "16:9"),
        }

    @web_route("GET", "/api/asset/{deck_id}/{name}")
    async def api_asset(self, request):
        """Serve an image referenced by a deck.

        Resolves `name` against (1) a per-deck assets folder and (2) the deck
        directory itself, so users can drop images either next to the .md or
        under `<deck>/assets/`. Path-traversal-safe via name validation.
        """
        from starlette.responses import FileResponse, JSONResponse

        deck_id = request.path_params["deck_id"]
        name = request.path_params["name"]
        if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            return JSONResponse({"error": "bad name"}, status_code=400)
        vault = self.kernel.config.notes_path
        if not vault:
            return JSONResponse({"error": "no vault"}, status_code=404)
        ppt_dir = vault / self._ppt_dir()
        for candidate in (ppt_dir / "assets" / name, ppt_dir / name, ppt_dir / deck_id / name):
            if candidate.exists() and candidate.is_file():
                ext = candidate.suffix.lower().lstrip(".")
                mime = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "svg": "image/svg+xml",
                    "webp": "image/webp",
                    "mp3": "audio/mpeg",
                    "wav": "audio/wav",
                    "m4a": "audio/mp4",
                    "ogg": "audio/ogg",
                    "flac": "audio/flac",
                    "mp4": "video/mp4",
                    "webm": "video/webm",
                    "mov": "video/quicktime",
                }.get(ext, "application/octet-stream")
                return FileResponse(str(candidate), media_type=mime)
        return JSONResponse({"error": "not found"}, status_code=404)
