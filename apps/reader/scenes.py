"""Scenes mixin — per-paragraph image generation, world card, character anchoring.

Tier-1 consistency: world card injected verbatim into every prompt, with
exact-phrase character anchors when the character appears in the paragraph.
Tier-3 consistency (optional): canonical portrait per character, fed via
IP-Adapter workflow so faces stay coherent across re-rolls.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from emptyos.sdk import parse_llm_json

from ._helpers import _split_paragraphs


log = logging.getLogger("emptyos.reader.scenes")


SCENE_SYSTEM = """You write a single concise visual prompt for an AI image
model. You receive: a world card (setting + recurring characters), a window
of context paragraphs (previous + following), and the current paragraph.

Generate a prompt for THIS paragraph specifically — but stay consistent
with the world card so recurring characters and settings look the same
across the book.

Describe ONLY what is visually present:
setting, light, atmosphere, key objects, framing, character appearance.

Do NOT:
- include text, captions, or watermarks
- describe interior thoughts or inaudible dialogue
- name characters that aren't visually described in either the paragraph
  or the world card
- contradict the world card (if the world says "hexagonal underground
  rooms", do not depict open sky unless the paragraph clearly says so)
- exceed 70 words
"""

WORLD_CARD_SYSTEM = """You derive a visually-anchored world card from the
opening of a book. This card is fed verbatim into every scene-generation
prompt — so character/setting descriptions need to be SPECIFIC ENOUGH that
re-using them produces a recognizably similar visual on each generation.

Return JSON with shape:
{
  "setting": "vivid physical description with concrete nouns: materials, lighting, key objects, colors. 25-50 words.",
  "era": "time period or aesthetic descriptor (e.g. '1909 Edwardian-imagined techno-future', '1850s American frontier')",
  "characters": [
    {
      "name": "...",
      "anchor": "12-25 word locked visual phrase. Include: build, age range, hair, face, clothing colors, distinguishing features. Use the SAME WORDS every time so re-generation looks similar."
    }
  ],
  "style_hint": "1 phrase visual mood (e.g. 'claustrophobic Edwardian techno-mystic', 'sun-bleached sparse')"
}

CRITICAL — the `anchor` field is fed verbatim into image prompts. It must
be PURE VISUAL DESCRIPTION using concrete words FLUX understands well:
hair color, clothing color, build, age, posture. Avoid abstract words.

Do NOT:
- exceed 4 characters
- add interpretive prose outside the JSON
- wrap in markdown fences
- describe abstract concepts (the Machine, capitalism) — only physical things
- describe internal traits (sad, clever, proud) — only what a camera could see
"""


class ScenesMixin:
    def _scenes_dir(self) -> Path:
        d = Path(self.kernel.config.data_dir) / "apps" / "reader" / "scenes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _canon_dir(self, slug: str) -> Path:
        safe = re.sub(r"[^\w-]", "_", slug)[:60]
        d = Path(self.kernel.config.data_dir) / "apps" / "reader" / "canon" / safe
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _ensure_character_portrait(self, slug: str, character: dict) -> Path | None:
        """Generate one canonical portrait per character (lazy, idempotent).

        Used as the IP-Adapter reference image when that character appears in
        a scene paragraph. Same face every time — no longer relies on FLUX
        re-rolling appearance from prose alone.
        """
        name = (character.get("name") or "").strip()
        anchor = (character.get("anchor") or character.get("appearance") or "").strip()
        if not name or not anchor:
            return None
        safe_name = re.sub(r"[^\w-]", "_", name)[:40]
        canon_dir = self._canon_dir(slug)
        portrait = canon_dir / f"{safe_name}.png"
        if portrait.exists():
            return portrait
        portrait_prompt = (
            f"portrait of {anchor}, "
            "neutral grey backdrop, eye-level three-quarter view, "
            "soft even daylight, sharp focus, detailed face, no text, no watermark"
        )
        try:
            filename = await self.draw(portrait_prompt, style="portrait")
        except Exception as e:
            log.warning("portrait draw failed for %s: %s", name, e)
            return None
        if not filename:
            return None
        if await self._download_comfyui_image(str(filename), portrait):
            log.info("canonical portrait saved: %s", portrait)
            return portrait
        return None

    def _character_in_text(self, text: str, characters: list[dict]) -> dict | None:
        """Return the character whose name appears EARLIEST in *text*, else None."""
        text_lower = text.lower()
        best = None
        best_pos = 1_000_000
        for c in characters:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            for token in [name] + name.split():
                if len(token) < 3:
                    continue
                pos = text_lower.find(token.lower())
                if pos >= 0 and pos < best_pos:
                    best_pos = pos
                    best = c
                    break
        return best

    def _resolve_paragraph_subject(
        self, slug: str, paragraph_index: int, paragraph_text: str, characters: list[dict]
    ) -> dict | None:
        """Decide which character is the visual subject of a paragraph.

        Strategy (cheapest first):
          1. Whole-book presence map (if it's been built). O(1) lookup.
          2. Direct name match in this paragraph.
          3. Recency fallback: walk back up to 3 paragraphs; whichever character
             was last named is the assumed subject (catches dialogue chains).
          4. Give up — return None.
        """
        pmap = self.state_data.get("presence_maps", {}).get(slug, {})
        names_here = pmap.get(str(paragraph_index)) if pmap else None
        if names_here:
            for c in characters:
                if (c.get("name") or "") in names_here:
                    return c

        hit = self._character_in_text(paragraph_text, characters)
        if hit:
            return hit

        book = self._resolve_slug(slug)
        if book and paragraph_index > 0:
            _meta, body = self._read_book_body(book)
            paras = _split_paragraphs(body)
            for i in range(paragraph_index - 1, max(-1, paragraph_index - 4), -1):
                if 0 <= i < len(paras):
                    prev_hit = self._character_in_text(paras[i], characters)
                    if prev_hit:
                        return prev_hit
        return None

    async def _character_ref_for_paragraph(
        self, slug: str, paragraph_text: str, paragraph_index: int = -1
    ) -> tuple[str | None, str | None]:
        """Return (comfyui_input_filename, character_name) for the visual subject
        of this paragraph, resolved via name match → recency → presence map."""
        card = self.state_data.get("world_cards", {}).get(slug) or {}
        characters = card.get("characters") or []
        if not characters or not paragraph_text:
            return None, None
        best = self._resolve_paragraph_subject(slug, paragraph_index, paragraph_text, characters)
        if best is None:
            return None, None
        portrait = await self._ensure_character_portrait(slug, best)
        if not portrait:
            return None, None
        try:
            comfyui = self.service("comfyui")
        except Exception:
            comfyui = None
        if not comfyui or not hasattr(comfyui, "upload_image"):
            return None, None
        try:
            input_name = await comfyui.upload_image(str(portrait))
            return input_name, best.get("name")
        except Exception as e:
            log.warning("ip-adapter ref upload failed: %s", e)
            return None, None

    async def _download_comfyui_image(self, filename: str, dest: Path) -> bool:
        """Fetch a generated image from ComfyUI and write it to *dest*."""
        try:
            src = Path(filename)
            if src.exists():
                import shutil
                shutil.copy2(str(src), str(dest))
                return True
        except Exception:
            pass
        try:
            comfyui = self.service("comfyui")
        except Exception:
            comfyui = None
        if not comfyui:
            return False
        try:
            session = getattr(comfyui, "_session", None)
            if not session:
                return False
            url = ""
            if hasattr(comfyui, "get_image_url"):
                url = await comfyui.get_image_url(filename)
            if not url:
                host = comfyui.config("host", "http://localhost:8188") if hasattr(comfyui, "config") else "http://localhost:8188"
                url = f"{host}/view?filename={filename}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    dest.write_bytes(await resp.read())
                    return True
        except Exception as e:
            log.warning("comfyui download failed: %s", e)
        return False

    def get_cached_scene(self, slug: str, paragraph_index: int) -> str | None:
        url = self.state_data.get("scene_cache", {}).get(f"{slug}:{paragraph_index}")
        if isinstance(url, str) and url.startswith("/reader/scene/"):
            return url
        return None

    async def _ensure_world_card(self, slug: str) -> dict:
        """Derive a per-book world card on first use, cache forever."""
        cards = self.state_data.setdefault("world_cards", {})
        if slug in cards:
            return cards[slug]
        book = self._resolve_slug(slug)
        if not book:
            return {}
        meta, body = self._read_book_body(book)
        paragraphs = _split_paragraphs(body)
        sample = []
        total = 0
        for p in paragraphs:
            if p.startswith("##") or len(p) < 60:
                continue
            sample.append(p)
            total += len(p)
            if len(sample) >= 6 or total > 3000:
                break
        if not sample:
            cards[slug] = {}
            self.save_state(self.state_data)
            return {}
        prelude = (
            f"Book: {book['title']}\n"
            f"Author: {meta.get('author', 'unknown')}\n"
            f"Year: {meta.get('published', 'unknown')}\n\n"
            "Opening passages:\n\n" + "\n\n".join(sample)
        )
        try:
            raw = await self.think(
                prelude, system=WORLD_CARD_SYSTEM, domain="reason", temperature=0.2
            )
            card = parse_llm_json(raw) or {}
        except Exception as e:
            log.warning("world card derivation failed: %s", e)
            card = {}
        cards[slug] = card
        self.save_state(self.state_data)
        return card

    def _world_card_text(self, card: dict) -> str:
        if not card:
            return ""
        parts = []
        if card.get("setting"):
            parts.append(f"Setting: {card['setting']}")
        if card.get("era"):
            parts.append(f"Era/aesthetic: {card['era']}")
        if card.get("characters"):
            people = "; ".join(
                f"{c.get('name')}: {c.get('anchor') or c.get('appearance', '')}"
                for c in (card.get("characters") or [])[:4]
                if c.get("name")
            )
            if people:
                parts.append(f"Recurring characters (use these visual phrases verbatim): {people}")
        if card.get("style_hint"):
            parts.append(f"Visual mood: {card['style_hint']}")
        return "\n".join(parts)

    def _present_character_anchors(self, card: dict, paragraph_text: str) -> list[str]:
        """Return locked visual phrases for characters whose names appear in this paragraph."""
        if not card or not paragraph_text:
            return []
        anchors = []
        text_lower = paragraph_text.lower()
        for c in (card.get("characters") or [])[:6]:
            name = (c.get("name") or "").strip()
            anchor = (c.get("anchor") or c.get("appearance") or "").strip()
            if not name or not anchor:
                continue
            tokens = [t.lower() for t in name.split() if len(t) > 2]
            if name.lower() in text_lower or any(t in text_lower for t in tokens):
                anchors.append(f"{name}: {anchor}")
        return anchors

    def _scene_context_window(
        self, slug: str, paragraph_index: int, before: int = 2, after: int = 1
    ) -> tuple[list[str], list[str]]:
        book = self._resolve_slug(slug)
        if not book:
            return [], []
        _meta, body = self._read_book_body(book)
        paras = _split_paragraphs(body)
        lo = max(0, paragraph_index - before)
        hi = min(len(paras), paragraph_index + after + 1)
        before_ps = paras[lo:paragraph_index]
        after_ps = paras[paragraph_index + 1:hi]
        before_ps = [p for p in before_ps if not p.startswith("##")]
        after_ps = [p for p in after_ps if not p.startswith("##")]
        return before_ps, after_ps

    async def generate_scene(
        self, slug: str, paragraph_index: int, text: str, force: bool = False
    ) -> dict:
        cache_key = f"{slug}:{paragraph_index}"
        cache = self.state_data["scene_cache"]
        if not force:
            cached = cache.get(cache_key)
            if isinstance(cached, str) and cached.startswith("/reader/scene/"):
                return {"url": cached, "cached": True}
        world = await self._ensure_world_card(slug)
        world_text = self._world_card_text(world)
        before_ps, after_ps = self._scene_context_window(slug, paragraph_index)

        present_anchors = self._present_character_anchors(world, text)

        sections: list[str] = []
        if world_text:
            sections.append("## World card\n" + world_text)
        if present_anchors:
            sections.append(
                "## Characters in THIS paragraph (use these phrases verbatim — do not paraphrase)\n"
                + "\n".join(f"- {a}" for a in present_anchors)
            )
        if before_ps:
            ctx_before = "\n\n".join(p[:400] for p in before_ps)
            sections.append("## Previous paragraphs (context)\n" + ctx_before)
        sections.append("## Current paragraph (the SUBJECT — visualize THIS)\n" + text.strip()[:1500])
        if after_ps:
            ctx_after = "\n\n".join(p[:300] for p in after_ps)
            sections.append("## Following paragraphs (continuity hints)\n" + ctx_after)

        prompt_for_image = await self.think(
            "\n\n".join(sections),
            system=SCENE_SYSTEM,
            domain="text",
            temperature=0.4,
        )
        style_hints = self.setting("reader.scene_style", "atmospheric, soft light, no text")
        full_prompt = f"{prompt_for_image.strip()}, {style_hints}"
        preset = self.setting("reader.scene_preset", "illustration") or ""
        draw_kwargs: dict = {}
        if preset:
            draw_kwargs["style"] = preset

        filename = ""
        used_ref_for: str | None = None
        ipa_workflow = self.kernel.config.get("plugins.comfyui.ipadapter_workflow", "")
        if ipa_workflow:
            ref_input, char_name = await self._character_ref_for_paragraph(slug, text, paragraph_index)
            if ref_input:
                try:
                    comfyui = self.service("comfyui")
                    if comfyui and hasattr(comfyui, "generate_from_workflow"):
                        filename = await comfyui.generate_from_workflow(
                            workflow_key="ipadapter",
                            prompt=full_prompt,
                            image_filename=ref_input,
                            seed=0,
                            width=1024,
                            height=1024,
                        )
                        if filename:
                            used_ref_for = char_name
                except Exception as e:
                    log.warning("ipadapter workflow failed, falling back: %s", e)
                    filename = ""

        if not filename:
            try:
                filename = await self.draw(full_prompt, **draw_kwargs)
            except Exception as e:
                log.warning("scene generation failed: %s", e)
                return {"error": str(e)}
        if not filename:
            return {"error": "draw returned no image — is ComfyUI running?"}

        safe_slug = re.sub(r"[^\w-]", "_", slug)[:60]
        dest = self._scenes_dir() / f"{safe_slug}-p{paragraph_index}.png"
        if not await self._download_comfyui_image(str(filename), dest):
            return {"error": f"could not retrieve image '{filename}' from ComfyUI"}
        try:
            ref_line = f"CHARACTER REF (IP-Adapter): {used_ref_for}\n" if used_ref_for else "CHARACTER REF: none (bare FLUX)\n"
            (dest.with_suffix(".prompt.txt")).write_text(
                f"PRESET: {preset}\n{ref_line}\nFINAL PROMPT:\n{full_prompt}\n\nLLM INPUT:\n{chr(10).join(sections)}\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        public_url = f"/reader/scene/{safe_slug}-p{paragraph_index}.png"
        cache[cache_key] = public_url
        self.save_state(self.state_data)
        await self.emit("reader:scene_generated", {"slug": slug, "paragraph": paragraph_index})
        return {"url": public_url, "prompt": full_prompt}
