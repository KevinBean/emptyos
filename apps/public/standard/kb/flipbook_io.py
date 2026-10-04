"""Vault I/O mixin — load, save, parse, slug + path helpers, symbol library.

Ported from apps/explore/vault_io.py during the kb⇔explore merge. Owns
flipbook note storage: path helpers, the reusable SVG symbol library, and
note load/save/parse. Flipbook notes are kb-tagged so the rest of the kb
app (queries, network graph, hub panel) sees them.

Cross-module: imported by app.py to compose KBApp. Generation lives in
flipbook_gen.py and calls these helpers via ``self.``.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk import parse_frontmatter, strip_frontmatter

from .shared import DEFAULT_FOLDER, DEMO_SYMBOLS

# Legacy folders checked on load as a read-only fallback. Covers two prior
# storage locations after the explore→kb merge waves:
#   - "30_Resources/Explore"         pre-Wave-2 (apps/explore/)
#   - "30_Resources/EmptyOS/kb/flipbook"  Wave-2 transitional location
# The Wave-3 migration script moves files out of these into kb/notes/; the
# fallback covers any notes the user skipped during either migration.
_LEGACY_FOLDERS = (
    "30_Resources/Explore",
    "30_Resources/EmptyOS/kb/flipbook",
)

# Markers wrap the regenerated flipbook block so user-authored prose outside
# them survives every regeneration. Notes saved before this fix have no
# markers — their entire body IS the flipbook block and we wrap it lazily on
# the next save (see `_extract_user_body`).
FLIPBOOK_START_MARKER = "<!-- flipbook:start -->"
FLIPBOOK_END_MARKER = "<!-- flipbook:end -->"


def _has_visual_frontmatter(fm: dict | None) -> bool:
    """A note carries flipbook visual artifacts when any of these are set."""
    if not isinstance(fm, dict):
        return False
    return bool(
        fm.get("svg_callouts")
        or fm.get("image_callouts")
        or fm.get("image_url")
    )


def _extract_user_body(body: str, prior_fm: dict | None) -> str:
    """Return the user-authored prefix of a vault note body.

    Three cases:
      1. Body contains ``FLIPBOOK_START_MARKER`` → everything before the marker.
         (Subsequent saves preserve hand-authored prose written above the
         flipbook block.)
      2. Body lacks markers but frontmatter has flipbook visual fields → the
         note is a legacy flipbook (pre-fix-shape); its entire body is the
         flipbook block, so the user prefix is empty. The next save wraps the
         block with markers.
      3. Otherwise → the entire body is user prose (the user just authored a
         kb note that happens to slugify to the same path as the requested
         flipbook topic). Preserve it verbatim.
    """
    body = body or ""
    if FLIPBOOK_START_MARKER in body:
        idx = body.index(FLIPBOOK_START_MARKER)
        return body[:idx].rstrip()
    if _has_visual_frontmatter(prior_fm):
        return ""
    return body.rstrip()


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
        """Storage root for flipbook notes — same folder as the rest of the
        kb corpus after the Wave-3 consolidation. Override via
        ``[apps.kb] notes_dir`` in ``emptyos.toml``. ``flipbook_dir`` is no
        longer honored (use ``notes_dir`` instead — flipbook notes are now
        peer kb notes that happen to carry visual frontmatter)."""
        # _notes_dir is defined on KBApp (the host class); reading it via
        # ``self`` keeps the two storage roots in lockstep.
        return self._notes_dir()

    def _flipbook_root(self) -> Path | None:
        """Absolute path to the flipbook storage root.

        After Wave 3 this is the same folder as kb's note corpus
        (``self._notes_dir()`` relative to the vault). Returns None when no
        vault is configured, so callers can branch cleanly without an
        AttributeError on a missing vault.
        """
        vault = self.kernel.config.notes_path
        if not vault:
            return None
        return vault / self._vault_dir()

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
        folder = self._flipbook_root()
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
        target_slug: str | None = None, target_path: str | None = None,
    ) -> dict | None:
        # Explicit path wins — used when the resolver in flipbook_gen matched
        # a kb note that doesn't live under ``_vault_dir()``.
        if target_path:
            try:
                content = await self.read(target_path)
                return await self._parse_loaded(content, fallback_topic=topic)
            except Exception:
                pass
        # Wave 3.6: explicit slug wins over topic-derived slug — used when
        # generating a visual for an existing kb concept whose title
        # doesn't slugify to the same string as its filename.
        if target_slug:
            explicit_path = f"{self._vault_dir()}/{target_slug}.md"
            try:
                content = await self.read(explicit_path)
                return await self._parse_loaded(content, fallback_topic=topic)
            except Exception:
                # Fall through to the topic-derived path lookup; the slug
                # may not yet have a flipbook block.
                pass
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

    async def _resolve_source_note(self, topic: str) -> list[dict]:
        """Find kb-tagged notes whose identity matches the typed ``topic``.

        Matches on any of: frontmatter ``title``, ``slug``, ``topic``, or
        filename stem — all compared case-insensitively, with the
        filename-stem and ``slug`` comparisons also normalised through
        ``_slug`` so "Test Collision" pairs up with ``test-collision.md``.

        Returns the list of candidate ``{path, slug, props}`` dicts (0, 1,
        or many entries). The caller decides what to do with multiple
        matches — the resolver itself never picks one.
        """
        if not topic:
            return []
        topic_lower = topic.strip().lower()
        norm = self._slug(topic)
        seen: set[str] = set()
        matches: list[dict] = []
        for note in (self.vault_query(tags=["kb"]) or []):
            path = note.get("path", "")
            if not path or path in seen:
                continue
            props = note.get("properties", {}) or {}
            stem = Path(path).stem
            title_fm = str(props.get("title") or "").strip().lower()
            slug_fm = str(props.get("slug") or "").strip().lower()
            topic_fm = str(props.get("topic") or "").strip().lower()
            if (
                title_fm == topic_lower
                or topic_fm == topic_lower
                or slug_fm == norm
                or stem.lower() == norm
            ):
                seen.add(path)
                matches.append({"path": path, "slug": stem, "props": props})
        return matches

    async def _save_page(
        self, page: dict, topic: str | None = None, verified: bool = False,
        target_slug: str | None = None, target_path: str | None = None,
    ) -> None:
        title_from_page = page.get("title", "Untitled")
        breadcrumb = page.get("breadcrumb") or [title_from_page]
        parents = breadcrumb[:-1] if len(breadcrumb) > 1 else []
        if not topic:
            topic = breadcrumb[-1] if breadcrumb else title_from_page
        today = date.today().isoformat()
        now_iso = datetime.now().isoformat(timespec="seconds")

        # Path resolution priority: explicit target_path > target_slug >
        # topic-derived. ``target_path`` carries a non-default folder when
        # the resolver in flipbook_gen matched a kb note that doesn't live
        # in ``_vault_dir()`` (e.g. ``kb/sources/foo.md``).
        if target_path:
            path = target_path
            if not target_slug:
                target_slug = Path(target_path).stem
        elif target_slug:
            path = f"{self._vault_dir()}/{target_slug}.md"
        else:
            path = self._path_for(topic, parents)

        # Read prior content so we can preserve the user-authored prefix,
        # other apps' tags, and the source note's identity fields. The
        # flipbook subsystem only owns its block — never the surrounding
        # note's prose or unrelated frontmatter.
        prior_fm: dict = {}
        prior_body = ""
        try:
            existing = await self.read(path)
            prior_fm = parse_frontmatter(existing) or {}
            prior_body = strip_frontmatter(existing) or ""
        except Exception:
            pass

        user_body = _extract_user_body(prior_body, prior_fm)

        created = today
        if prior_fm.get("created"):
            created = str(prior_fm["created"])

        # Identity fields belong to the source note when one exists. The
        # LLM-suggested title/topic only win on a brand-new file — otherwise
        # regenerating a flipbook view would silently rename the user's
        # hand-authored note.
        title = str(prior_fm.get("title") or title_from_page or topic)
        topic_value = str(prior_fm.get("topic") or topic)

        fm_slug = target_slug or str(prior_fm.get("slug") or self._slug(topic))

        mode = page.get("mode") or "svg"

        # ensure_ascii=False keeps non-ASCII (Chinese, accents, etc.) readable
        # in the YAML — the simple parser doesn't decode `\uXXXX` escapes, so
        # ASCII-encoded JSON would round-trip as literal backslash-u garbage.
        def _q(s: str) -> str:
            return json.dumps(s, ensure_ascii=False)

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

        # Tags: ensure ``kb`` is present, preserve every other tag the
        # source note already carries. Hard-overwriting ``tags = ["kb"]``
        # was the bug that silently dropped user-applied tags on save.
        prior_tags = prior_fm.get("tags") or []
        if isinstance(prior_tags, str):
            prior_tags = [t.strip() for t in prior_tags.split(",") if t.strip()]
        merged_tags: list[str] = []
        for t in prior_tags:
            ts = str(t).strip()
            if ts and ts not in merged_tags:
                merged_tags.append(ts)
        if not any(str(t).strip().lower() == "kb" for t in merged_tags):
            merged_tags.append("kb")

        # ``kind`` / ``domain`` belong to the source note when prior set
        # them — only assert flipbook defaults when verified AND no prior.
        # Otherwise verifying a flipbook view of a ``kind: formula`` note
        # would silently downgrade it to ``kind: concept``.
        kind_val = prior_fm.get("kind")
        domain_val = prior_fm.get("domain")
        if verified and not kind_val:
            kind_val = "concept"
        if verified and not domain_val:
            domain_val = "flipbook"

        fm_lines = [
            "---",
            f"title: {_q(title)}",
            f"slug: {fm_slug}",
            f"topic: {_q(topic_value)}",
            f"mode: {mode}",
            f"created: {created}",
            f"updated: {today}",
            f"draft: {'false' if verified else 'true'}",
            f"verified: {'true' if verified else 'false'}",
            f"source_note: {fm_slug}",
            f"generated_at: {now_iso}",
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
        if kind_val:
            fm_lines.append(f"kind: {_q(str(kind_val))}")
        if domain_val:
            fm_lines.append(f"domain: {_q(str(domain_val))}")
        if parents:
            fm_lines.append("parents:")
            for p in parents:
                fm_lines.append(f"  - {_q(p)}")
        fm_lines.append("tags:")
        for t in merged_tags:
            fm_lines.append(f"  - {t}")
        fm_lines.append("---")
        fm_yaml = "\n".join(fm_lines) + "\n\n"

        # Diagram section depends on mode: SVG asset embed vs. PNG embed.
        # Asset filenames track the canonical slug (target_slug if given)
        # so a "Generate visual" for `snr-social-perception` writes the
        # SVG at `_assets/snr-social-perception.svg` rather than at the
        # title-derived `_assets/signal-to-noise-theory-of-social-perception.svg`.
        asset_slug = target_slug or self._slug(topic_value)
        diagram_section = "## Diagram\n\n(no diagram)\n\n"
        if mode == "svg":
            svg_content = page.get("svg") or ""
            if svg_content.strip():
                if target_slug or target_path:
                    asset_path = f"{self._vault_dir()}/_assets/{asset_slug}.svg"
                else:
                    asset_path = self._asset_path_for(topic_value, parents)
                try:
                    await self.write(asset_path, svg_content)
                    diagram_section = f"## Diagram\n\n![[{asset_path}]]\n\n"
                except Exception:
                    diagram_section = f"## Diagram\n\n```svg\n{svg_content}\n```\n\n"
        elif mode == "image":
            parent_seg = (
                f"{self._slug(parents[-1])}/"
                if (not (target_slug or target_path)) and parents
                and self._slug(parents[-1]) != asset_slug
                else ""
            )
            png_path = f"{self._vault_dir()}/_assets/{parent_seg}{asset_slug}.png"
            diagram_section = f"## Diagram\n\n![[{png_path}]]\n\n"

        flipbook_block = (
            f"# {title}\n\n"
            f"## Subtitle\n\n{page.get('subtitle', '')}\n\n"
            f"{diagram_section}"
            f"## Callouts\n\n```json\n"
            f"{json.dumps(page.get('callouts', []), indent=2, ensure_ascii=False)}\n"
            f"```\n\n"
            f"## Caption\n\n{page.get('caption', '')}\n"
        )

        # Compose final body. Markers delimit the block so the next save can
        # find and replace it without touching the user-authored prefix.
        if user_body.strip():
            body_out = (
                f"{user_body}\n\n"
                f"{FLIPBOOK_START_MARKER}\n"
                f"{flipbook_block}"
                f"{FLIPBOOK_END_MARKER}\n"
            )
        else:
            body_out = (
                f"{FLIPBOOK_START_MARKER}\n"
                f"{flipbook_block}"
                f"{FLIPBOOK_END_MARKER}\n"
            )
        await self.write(path, fm_yaml + body_out)
        # `self.write` only emits `vault:changed`, so the in-memory index
        # updates asynchronously — a save-then-read (e.g. flipbook generate →
        # immediate note fetch) could miss the just-written note and surface
        # stale/empty frontmatter (source_note, generated_at). Index it
        # synchronously so the note is queryable the instant the save returns.
        self.vault_force_index(path)

    async def _load_legacy(self, topic: str) -> dict | None:
        """Vault-scan fallback. Covers two cases:

        1. Notes saved under title-slug before the topic-slug fix (matched
           by frontmatter `topic` equal to the typed topic).
        2. Notes left in the legacy `30_Resources/Explore/` folder by users
           who skipped the migration step. The kb-tag set already includes
           them via VaultIndex, but the path-shaped load path still expects
           them at the new location — this fallback bridges both.
        """
        topic_norm = topic.strip()
        candidate_folders: list[Path] = []
        new_folder = self._flipbook_root()
        if new_folder and new_folder.exists():
            candidate_folders.append(new_folder)
        # Legacy folders — read-only fallback for unmigrated notes
        if self.vault_root:
            for legacy in _LEGACY_FOLDERS:
                p = self.vault_root / legacy
                if p.exists():
                    candidate_folders.append(p)
        for folder in candidate_folders:
            for f in folder.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                fm = parse_frontmatter(content)
                if (fm.get("topic") or "").strip() == topic_norm:
                    # Re-route through normal load using the matched file's
                    # relative path so embedded asset wikilinks resolve.
                    rel = self.vault_rel(f) or f"{folder}/{f.stem}.md"
                    try:
                        raw = await self.read(rel)
                    except Exception:
                        continue
                    return await self._parse_loaded(raw, fallback_topic=topic)
        return None

    async def _parse_loaded(self, content: str, fallback_topic: str) -> dict:
        fm = parse_frontmatter(content)
        full_body = strip_frontmatter(content)

        # When markers are present, restrict section parsing to the marked
        # block so any user-authored prose above the flipbook (which may
        # contain its own ``##`` headers) can't be mis-read as flipbook
        # sections. Legacy notes (no markers) parse the whole body as-is.
        body = full_body
        if FLIPBOOK_START_MARKER in body and FLIPBOOK_END_MARKER in body:
            start = body.index(FLIPBOOK_START_MARKER) + len(FLIPBOOK_START_MARKER)
            end = body.index(FLIPBOOK_END_MARKER)
            if end > start:
                body = body[start:end]

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
            image_url = f"/kb/api/flipbook/asset/{slug_val}.png"

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
