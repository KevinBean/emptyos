"""Vault I/O mixin — load, save, parse, slug + path helpers, symbol library.

This mixin owns everything that touches the vault for the explore app:
  * path/slug helpers (`_slug`, `_path_for`, `_asset_path_for`)
  * the reusable SVG symbol library (`_symbols_dir`, `_list_symbols`,
    `_build_symbol_defs`, `_inject_symbols`, `_seed_demo_symbols`)
  * note load/save/parse (`_load_page`, `_save_page`, `_parse_loaded`,
    `_load_legacy`)
  * fallback SVG + frontmatter coercion helpers

Generation lives in `generation.py` and calls these via ``self.``.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from emptyos.sdk import parse_frontmatter, strip_frontmatter

from ._helpers import DEFAULT_FOLDER, DEMO_SYMBOLS


class VaultIOMixin:
    def _seed_demo_symbols(self) -> None:
        """Seed the symbol library with demo content if it's empty.
        Idempotent — never overwrites existing files, never re-creates after
        the user has deleted everything (sentinel `.seeded` flag)."""
        sd = self._symbols_dir()
        if sd is None:
            return
        sd.mkdir(parents=True, exist_ok=True)
        sentinel = sd / ".seeded"
        if sentinel.exists():
            return
        for sid, content in DEMO_SYMBOLS.items():
            target = sd / f"{sid}.svg"
            if not target.exists():
                target.write_text(content, encoding="utf-8")
        sentinel.write_text("", encoding="utf-8")

    def _vault_dir(self) -> str:
        return self.vault_config("explore_dir", DEFAULT_FOLDER)

    @staticmethod
    def _slug(topic: str) -> str:
        s = topic.lower().strip()
        s = re.sub(r"[^a-z0-9一-鿿]+", "-", s)
        return s.strip("-")[:80] or "page"

    def _path_for(self, topic: str, parents: list[str] | None = None) -> str:
        """Vault path for a study note.

        Top-level studies live at `{dir}/{slug}.md`. Sub-topics nest under
        their immediate parent: `{dir}/{parent_slug}/{slug}.md`. This keeps
        a fresh "Nut" study distinct from "Guitar > Nut" while still
        matching the vault viewer's folder idiom.
        """
        slug = self._slug(topic)
        if parents:
            parent_slug = self._slug(parents[-1])
            if parent_slug and parent_slug != slug:
                return f"{self._vault_dir()}/{parent_slug}/{slug}.md"
        return f"{self._vault_dir()}/{slug}.md"

    def _asset_path_for(self, topic: str, parents: list[str] | None = None) -> str:
        """Standalone SVG file path — separates pixels from prose so the SVG
        is reusable across notes/apps and viewable in the vault viewer directly."""
        slug = self._slug(topic)
        if parents:
            parent_slug = self._slug(parents[-1])
            if parent_slug and parent_slug != slug:
                return f"{self._vault_dir()}/_assets/{parent_slug}/{slug}.svg"
        return f"{self._vault_dir()}/_assets/{slug}.svg"

    @staticmethod
    def _safe_load_callouts(raw) -> list:
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return []

    @staticmethod
    def _unescape_legacy(s: str) -> str:
        """Decode `\\uXXXX` escapes left in old notes that were written with
        ascii-only JSON. Idempotent for normal text."""
        if not s or "\\u" not in s:
            return s
        try:
            return s.encode("ascii", errors="ignore").decode("unicode_escape")
        except Exception:
            return s

    @staticmethod
    def _symbol_slug(name: str) -> str:
        """Sanitize a user-chosen symbol name into a stable filename + id."""
        s = (name or "").lower().strip()
        s = re.sub(r"[^a-z0-9-]+", "-", s)
        return s.strip("-")[:60] or "symbol"

    def _symbols_dir(self) -> Path | None:
        folder = self.vault_config_path("explore_dir", DEFAULT_FOLDER)
        if folder is None:
            return None
        return folder / "_symbols"

    def _list_symbols(self) -> list[dict]:
        """Catalog of symbols available for `<use>` in new diagrams."""
        sd = self._symbols_dir()
        if sd is None or not sd.exists():
            return []
        out: list[dict] = []
        for f in sorted(sd.glob("*.svg")):
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            vb_m = re.search(r"viewBox\s*=\s*['\"]([^'\"]+)['\"]", content)
            desc_m = re.search(
                r"<desc>([^<]+)</desc>", content, re.DOTALL
            )
            out.append({
                "id": f.stem,
                "name": f.stem.replace("-", " "),
                "viewBox": (vb_m.group(1) if vb_m else "0 0 800 500"),
                "description": (desc_m.group(1).strip() if desc_m else ""),
            })
        return out

    def _build_symbol_defs(self) -> str:
        """Build a `<defs>` block to inline at the top of generated SVGs so
        `<use href="#id">` resolves locally. Each library entry becomes a
        `<symbol id="id" viewBox="...">...inner...</symbol>`."""
        sd = self._symbols_dir()
        if sd is None or not sd.exists():
            return ""
        symbols: list[str] = []
        for f in sorted(sd.glob("*.svg")):
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            vb_m = re.search(r"viewBox\s*=\s*['\"]([^'\"]+)['\"]", content)
            vb = vb_m.group(1) if vb_m else "0 0 800 500"
            inner_m = re.search(
                r"<svg[^>]*>(.*)</svg>\s*$", content, re.DOTALL
            )
            inner = inner_m.group(1).strip() if inner_m else ""
            if not inner:
                continue
            symbols.append(
                f'<symbol id="{f.stem}" viewBox="{vb}">{inner}</symbol>'
            )
        if not symbols:
            return ""
        return "<defs>" + "".join(symbols) + "</defs>"

    def _inject_symbols(self, svg: str) -> str:
        """Prepend the symbol library `<defs>` inside an SVG's root, so any
        `<use href="#id">` references resolve."""
        if not svg or "<svg" not in svg:
            return svg
        defs = self._build_symbol_defs()
        if not defs:
            return svg
        # Insert just after the opening <svg ...> tag
        m = re.search(r"<svg\b[^>]*>", svg)
        if not m:
            return svg
        end = m.end()
        return svg[:end] + defs + svg[end:]

    async def _load_page(
        self, topic: str, parents: list[str] | None = None,
    ) -> dict | None:
        # Try the parent-nested path first (new layout), then the flat path
        # (back-compat with notes saved before the hierarchy change).
        for path in self._candidate_paths(topic, parents):
            try:
                content = await self.read(path)
                return await self._parse_loaded(content, fallback_topic=topic)
            except Exception:
                continue
        # Legacy notes saved under title-slug — try a vault scan as fallback
        return await self._load_legacy(topic)

    def _candidate_paths(
        self, topic: str, parents: list[str] | None,
    ) -> list[str]:
        nested = self._path_for(topic, parents)
        flat = self._path_for(topic, None)
        out = [nested]
        if flat != nested:
            out.append(flat)
        return out

    async def _save_page(
        self, page: dict, topic: str | None = None, verified: bool = False
    ) -> None:
        title = page.get("title", "Untitled")
        breadcrumb = page.get("breadcrumb") or [title]
        parents = breadcrumb[:-1] if len(breadcrumb) > 1 else []
        if not topic:
            topic = breadcrumb[-1] if breadcrumb else title
        today = date.today().isoformat()
        path = self._path_for(topic, parents)

        # Preserve original created date if note already exists
        created = today
        try:
            existing = await self.read(path)
            existing_fm = parse_frontmatter(existing)
            if existing_fm.get("created"):
                created = str(existing_fm["created"])
        except Exception:
            pass

        # Verified pages also tag `kb` so the KB app picks them up automatically.
        tags = ["explore"]
        if verified:
            tags.append("kb")

        mode = page.get("mode") or "svg"

        # ensure_ascii=False keeps non-ASCII (Chinese, accents, etc.) readable
        # in the YAML — the simple parser doesn't decode `\uXXXX` escapes, so
        # ASCII-encoded JSON would round-trip as literal backslash-u garbage.
        def _q(s: str) -> str:
            return json.dumps(s, ensure_ascii=False)

        # Read existing frontmatter so we can preserve the OTHER mode's
        # artifacts when we save (carries `image_url` while writing svg mode,
        # etc.).
        prior_fm: dict = {}
        try:
            prior_fm = parse_frontmatter(await self.read(path)) or {}
        except Exception:
            pass

        active_callouts = page.get("callouts") or []
        # Per-mode callout snapshots: each mode owns its anchor coords.
        if mode == "svg":
            svg_callouts = active_callouts
            image_callouts = (
                page.get("image_callouts")
                or self._safe_load_callouts(prior_fm.get("image_callouts"))
                or []
            )
        else:
            image_callouts = active_callouts
            svg_callouts = (
                page.get("svg_callouts")
                or self._safe_load_callouts(prior_fm.get("svg_callouts"))
                or []
            )

        # Cross-mode artifacts: keep image fields when writing svg, keep svg
        # asset when writing image.
        carry_image_url = (
            page.get("image_url") if mode == "image"
            else (page.get("image_url") or prior_fm.get("image_url") or "")
        )
        carry_image_prompt = (
            page.get("image_prompt") if mode == "image"
            else (page.get("image_prompt") or prior_fm.get("image_prompt") or "")
        )

        # Block-style YAML frontmatter (per project convention)
        fm_lines = [
            "---",
            f"title: {_q(title)}",
            f"slug: {self._slug(topic)}",
            f"topic: {_q(topic)}",
            f"mode: {mode}",
            f"created: {created}",
            f"updated: {today}",
            f"draft: {'false' if verified else 'true'}",
            f"verified: {'true' if verified else 'false'}",
        ]
        if carry_image_url:
            fm_lines.append(f"image_url: {_q(carry_image_url)}")
        if carry_image_prompt:
            fm_lines.append(f"image_prompt: {_q(carry_image_prompt)}")
        if svg_callouts:
            fm_lines.append(
                f"svg_callouts: {_q(json.dumps(svg_callouts, ensure_ascii=False))}"
            )
        if image_callouts:
            fm_lines.append(
                f"image_callouts: {_q(json.dumps(image_callouts, ensure_ascii=False))}"
            )
        if verified:
            fm_lines.append("kind: concept")
            fm_lines.append("domain: explore")
        if parents:
            fm_lines.append("parents:")
            for p in parents:
                fm_lines.append(f"  - {_q(p)}")
        fm_lines.append("tags:")
        for t in tags:
            fm_lines.append(f"  - {t}")
        fm_lines.append("---")
        fm_yaml = "\n".join(fm_lines) + "\n\n"

        # Diagram section depends on mode: SVG asset embed vs. PNG embed.
        diagram_section = "## Diagram\n\n(no diagram)\n\n"
        if mode == "svg":
            svg_content = page.get("svg") or ""
            if svg_content.strip():
                asset_path = self._asset_path_for(topic, parents)
                try:
                    await self.write(asset_path, svg_content)
                    diagram_section = f"## Diagram\n\n![[{asset_path}]]\n\n"
                except Exception:
                    diagram_section = f"## Diagram\n\n```svg\n{svg_content}\n```\n\n"
        elif mode == "image":
            slug = self._slug(topic)
            parent_seg = (
                f"{self._slug(parents[-1])}/" if parents and self._slug(parents[-1]) != slug else ""
            )
            png_path = f"{self._vault_dir()}/_assets/{parent_seg}{slug}.png"
            diagram_section = f"## Diagram\n\n![[{png_path}]]\n\n"

        body = (
            f"# {title}\n\n"
            f"## Subtitle\n\n{page.get('subtitle', '')}\n\n"
            f"{diagram_section}"
            f"## Callouts\n\n```json\n"
            f"{json.dumps(page.get('callouts', []), indent=2, ensure_ascii=False)}\n"
            f"```\n\n"
            f"## Caption\n\n{page.get('caption', '')}\n"
        )
        await self.write(path, fm_yaml + body)

    async def _load_legacy(self, topic: str) -> dict | None:
        """Vault-scan fallback for notes saved under title-slug before the
        topic-slug fix. Matches if frontmatter `topic` equals the typed topic."""
        folder = self.vault_config_path("explore_dir", DEFAULT_FOLDER)
        if not folder or not folder.exists():
            return None
        topic_norm = topic.strip()
        for f in folder.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            fm = parse_frontmatter(content)
            # YAML `topic:` (empty) parses to None — coerce before .strip()
            if (fm.get("topic") or "").strip() == topic_norm:
                # Re-route through normal load using the matched file's slug
                fake_path = f"{self._vault_dir()}/{f.stem}.md"
                try:
                    raw = await self.read(fake_path)
                except Exception:
                    continue
                return await self._parse_loaded(raw, fallback_topic=topic)
        return None

    async def _parse_loaded(self, content: str, fallback_topic: str) -> dict:
        fm = parse_frontmatter(content)
        body = strip_frontmatter(content)

        def section(name: str) -> str:
            m = re.search(
                rf"##\s+{re.escape(name)}\s*\n+(.*?)(?=\n##\s+|\Z)",
                body, re.DOTALL,
            )
            return m.group(1).strip() if m else ""

        def fenced(text: str, lang: str) -> str:
            m = re.search(rf"```{lang}\s*\n(.*?)\n```", text, re.DOTALL)
            return m.group(1).strip() if m else ""

        diagram = section("Diagram")
        callouts_text = section("Callouts")
        caption = section("Caption")
        subtitle = section("Subtitle")

        # Resolve SVG: prefer wikilink-embedded asset; fall back to inline fence
        svg = ""
        wiki_m = re.search(r"!\[\[([^\]]+\.svg)\]\]", diagram)
        if wiki_m:
            try:
                svg = await self.read(wiki_m.group(1).strip())
            except Exception:
                svg = ""
        if not svg:
            svg = fenced(diagram, "svg")
        if not svg:
            svg = self._fallback_svg()
        try:
            callouts = json.loads(fenced(callouts_text, "json") or "[]")
        except Exception:
            callouts = []

        title = self._unescape_legacy(fm.get("title") or fallback_topic)
        parents = fm.get("parents") or []
        if isinstance(parents, str):
            parents = [parents] if parents else []
        parents = [self._unescape_legacy(p) for p in parents]
        verified = str(fm.get("verified", "")).lower() == "true"
        mode = fm.get("mode") or "svg"

        # Both modes' artifacts can coexist on a note. Hydrate everything;
        # the active `mode` decides which `callouts` set the UI gets.
        image_url = fm.get("image_url") or ""
        if not image_url and mode == "image":
            slug_val = fm.get("slug") or self._slug(fallback_topic)
            image_url = f"/explore/api/asset/{slug_val}.png"

        svg_callouts = self._safe_load_callouts(fm.get("svg_callouts"))
        image_callouts = self._safe_load_callouts(fm.get("image_callouts"))
        if not svg_callouts and mode == "svg":
            svg_callouts = callouts
        if not image_callouts and mode == "image":
            image_callouts = callouts
        active_callouts = image_callouts if mode == "image" else svg_callouts

        return {
            "title": title,
            "subtitle": subtitle or fm.get("subtitle", ""),
            "mode": mode,
            "svg": svg,
            "image_url": image_url,
            "image_prompt": fm.get("image_prompt", ""),
            "callouts": active_callouts or callouts,
            "svg_callouts": svg_callouts,
            "image_callouts": image_callouts,
            "caption": caption or fm.get("caption", ""),
            "breadcrumb": list(parents) + [title],
            "saved": True,
            "verified": verified,
        }

    @staticmethod
    def _fallback_svg() -> str:
        return (
            "<svg viewBox='0 0 800 500' xmlns='http://www.w3.org/2000/svg'>"
            "<rect width='800' height='500' fill='#f5efe6'/>"
            "<text x='400' y='250' text-anchor='middle' fill='#8a7456' "
            "font-family='serif' font-size='24'>"
            "(illustration unavailable — try again)</text></svg>"
        )
