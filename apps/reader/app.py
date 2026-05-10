"""Reader — read books with AI scene visualization, concept map, and dictionary lookup.

Books live in the vault as markdown (single file per book, or a directory of
chapters). Reading state — current book, paragraph, highlights — lives in
``data/apps/reader/state.json``. Scene visuals + concept maps are generated
on demand via the ``draw`` and ``think`` capabilities and cached per book.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from emptyos.sdk import (
    BaseApp,
    cli_command,
    parse_frontmatter,
    parse_llm_json,
    strip_frontmatter,
    web_route,
)

from ._helpers import (
    DEFAULT_BOOKS_DIR,
    DEFAULT_NOTES_DIR,
    _slugify,
    _split_paragraphs,
)
from .production import ProductionMixin
from .scenes import ScenesMixin

log = logging.getLogger("emptyos.reader")


READER_PASSAGE_QA_SYSTEM = (
    "You are a reading companion. Given a single passage from a book and "
    "a question (or no question, in which case unpack the passage), respond "
    "in 3-6 plain-prose sentences that name the passage's main idea and any "
    "important subtext.\n\n"
    "Do NOT:\n"
    "- Use bullet points, headings, or markdown.\n"
    "- Quote the passage back at length — paraphrase a key phrase only when "
    "  needed to anchor the answer.\n"
    "- Speculate beyond what the passage supports — say 'unclear' instead.\n"
    "- Add a 'In summary' or 'In conclusion' tail."
)

PRESENCE_SYSTEM = """You read short numbered paragraph fragments and decide
which named characters from the provided cast are physically present (or are
the subject of pronouns like he/she/they) in each paragraph.

Return JSON only: {"<idx>": ["<character_name>", ...], ...}

Rules:
- Use ONLY names from the provided cast list — do not invent characters.
- Resolve pronouns from preceding context: if paragraph 12 says "she touched
  the switch" and paragraph 11 was about Vashti, then 12 → ["Vashti"].
- A character mentioned-but-not-present (e.g. "she remembered her father") is
  NOT included.
- If unclear, return [] for that index — never guess.
- Output ONE JSON object covering EVERY paragraph index in the input.
- No prose, no markdown fences, no explanations.
"""

CONCEPT_SYSTEM = """You extract entities and relations from a passage of prose
for a concept-map graph. Return JSON with shape:
{"nodes": [{"id": "snake_case", "label": "Display Name", "kind": "person|place|object|idea"}, ...],
 "edges": [{"from": "id", "to": "id", "label": "verb phrase"}, ...]}

Do NOT:
- invent entities the passage doesn't mention
- return more than 8 nodes for one passage
- output anything outside the JSON object
- wrap the JSON in markdown fences
"""


class ReaderApp(ScenesMixin, ProductionMixin, BaseApp):
    async def setup(self):
        await super().setup()
        self.state_data = self.load_state(
            {
                "current": None,
                "progress": {},
                "scene_cache": {},
                "concept_cache": {},
                "world_cards": {},
                "presence_maps": {},
                "voice_maps": {},  # {slug: {character_name: voice_id}}
            }
        )
        self.state_data.setdefault("world_cards", {})
        self.state_data.setdefault("presence_maps", {})
        self.state_data.setdefault("voice_maps", {})
        # Drop legacy scene_cache entries that hold raw ComfyUI filenames — they
        # were never web-accessible. New entries hold "/reader/scene/<file>" URLs.
        sc = self.state_data.get("scene_cache", {})
        stale = [k for k, v in sc.items() if not (isinstance(v, str) and v.startswith("/reader/scene/"))]
        if stale:
            for k in stale:
                sc.pop(k, None)
            self.save_state(self.state_data)

    # ── Helpers ──────────────────────────────────────────────────

    def _books_dir_rel(self) -> str:
        return self.vault_config("books_dir", DEFAULT_BOOKS_DIR)

    def _books_dir(self) -> Path | None:
        return self.vault_config_path("books_dir", DEFAULT_BOOKS_DIR)

    def _list_books(self) -> list[dict]:
        d = self._books_dir()
        if not d or not d.exists():
            return []
        out = []
        for entry in sorted(d.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.is_file() and entry.suffix.lower() == ".md":
                out.append({"slug": _slugify(entry.stem), "title": entry.stem, "path": entry.name, "kind": "file"})
            elif entry.is_dir():
                # directory of chapter files
                chapters = sorted(c.name for c in entry.glob("*.md") if not c.name.startswith("_"))
                if chapters:
                    out.append({"slug": _slugify(entry.name), "title": entry.name, "path": entry.name, "kind": "dir", "chapters": chapters})
        return out

    def _resolve_slug(self, slug: str) -> dict | None:
        for b in self._list_books():
            if b["slug"] == slug:
                return b
        return None

    def _read_book_body(self, book: dict, chapter: str | None = None) -> tuple[dict, str]:
        rel = f"{self._books_dir_rel()}/{book['path']}"
        if book["kind"] == "dir":
            ch = chapter or (book.get("chapters") or [""])[0]
            if not ch:
                return {}, ""
            rel = f"{rel}/{ch}"
        full = self.vault_root / rel
        if not full.exists():
            return {}, ""
        raw = full.read_text(encoding="utf-8", errors="replace")
        return parse_frontmatter(raw), strip_frontmatter(raw)

    # ── Public methods (callable via self.call_app) ──────────────

    async def list_books(self) -> list[dict]:
        return self._list_books()

    async def open_book(self, slug: str, chapter: str | None = None) -> dict:
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        meta, body = self._read_book_body(book, chapter)
        paras = _split_paragraphs(body)
        progress = self.state_data["progress"].get(slug, {})
        self.state_data["current"] = {"slug": slug, "chapter": chapter}
        self.save_state(self.state_data)
        await self.emit(
            "reader:opened",
            {"slug": slug, "title": book["title"], "chapter": chapter},
        )
        return {
            "slug": slug,
            "title": book["title"],
            "kind": book["kind"],
            "chapters": book.get("chapters", []),
            "chapter": chapter,
            "frontmatter": meta,
            "paragraphs": paras,
            "progress": progress,
        }

    async def set_progress(self, slug: str, paragraph: int, chapter: str | None = None) -> dict:
        prog = self.state_data["progress"].setdefault(slug, {})
        prog["paragraph"] = int(paragraph)
        if chapter is not None:
            prog["chapter"] = chapter
        prog["updated"] = datetime.now(timezone.utc).isoformat()
        self.save_state(self.state_data)
        await self.emit("reader:progress", {"slug": slug, "paragraph": int(paragraph), "chapter": chapter})
        return {"ok": True, "progress": prog}


    async def generate_concepts(self, slug: str, paragraph_index: int, text: str) -> dict:
        cache_key = f"{slug}:{paragraph_index}"
        cache = self.state_data["concept_cache"]
        if cache_key in cache:
            return {"graph": cache[cache_key], "cached": True}
        try:
            raw = await self.think(
                text.strip()[:2000],
                system=CONCEPT_SYSTEM,
                domain="reason",
                temperature=0.2,
            )
            graph = parse_llm_json(raw) or {"nodes": [], "edges": []}
        except Exception as e:
            log.warning("concept extraction failed: %s", e)
            return {"error": str(e), "graph": {"nodes": [], "edges": []}}
        if not isinstance(graph, dict):
            graph = {"nodes": [], "edges": []}
        graph.setdefault("nodes", [])
        graph.setdefault("edges", [])
        cache[cache_key] = graph
        self.save_state(self.state_data)
        return {"graph": graph}

    async def highlight(self, slug: str, paragraph_index: int, text: str, note: str = "") -> dict:
        book = self._resolve_slug(slug)
        title = book["title"] if book else slug
        await self.emit(
            "reader:highlighted",
            {
                "slug": slug,
                "title": title,
                "paragraph": paragraph_index,
                "text": text[:500],
                "note": note,
            },
        )
        # Optional fan-out to media app for unified highlights
        try:
            await self.call_app(
                "media",
                "add_highlight",
                text=text,
                note=note,
                source={"title": title, "type": "book", "slug": slug},
            )
            forwarded = True
        except Exception:
            forwarded = False
        return {"ok": True, "forwarded_to_media": forwarded}

    async def build_presence_map(self, slug: str, batch_size: int = 12) -> dict:
        """Whole-book character-presence scan (Tier C consistency).

        Walks every paragraph in batches, asks an LLM which cast members are
        present or implied via pronouns. Stores result in
        ``state_data["presence_maps"][slug] = {paragraph_idx: [names]}``.
        Cached forever — re-run via ``regen=1`` query param.

        Returns a summary {built: N, characters_seen: {...}}.
        """
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        card = await self._ensure_world_card(slug)
        cast = [
            (c.get("name") or "").strip()
            for c in (card.get("characters") or [])
            if c.get("name")
        ]
        if not cast:
            return {"error": "no characters in world card; cannot build presence map"}

        _meta, body = self._read_book_body(book)
        paragraphs = _split_paragraphs(body)

        pmap: dict[str, list[str]] = {}
        seen_counts: dict[str, int] = {n: 0 for n in cast}
        total = len(paragraphs)
        # Sliding window: include 1 prior paragraph in each batch as pronoun
        # context, but only emit decisions for the new ones.
        for start in range(0, total, batch_size):
            end = min(total, start + batch_size)
            ctx_start = max(0, start - 1)
            chunk_lines = []
            for i in range(ctx_start, end):
                p = paragraphs[i]
                if p.startswith("##"):
                    continue
                chunk_lines.append(f"[{i}] {p[:600]}")
            if not chunk_lines:
                continue
            user_msg = (
                f"Cast: {', '.join(cast)}\n\n"
                f"Decide which cast members are present in EACH numbered paragraph "
                f"(indices {start}–{end - 1}; earlier indices are context only).\n\n"
                + "\n\n".join(chunk_lines)
            )
            try:
                raw = await self.think(
                    user_msg, system=PRESENCE_SYSTEM, domain="reason", temperature=0.1
                )
                parsed = parse_llm_json(raw) or {}
            except Exception as e:
                log.warning("presence batch %d failed: %s", start, e)
                continue
            if not isinstance(parsed, dict):
                continue
            for idx_str, names in parsed.items():
                if not isinstance(names, list):
                    continue
                clean = [n for n in names if isinstance(n, str) and n in cast]
                if clean:
                    pmap[str(idx_str)] = clean
                    for n in clean:
                        seen_counts[n] = seen_counts.get(n, 0) + 1

        self.state_data.setdefault("presence_maps", {})[slug] = pmap
        self.save_state(self.state_data)
        return {
            "ok": True,
            "slug": slug,
            "paragraphs_scanned": total,
            "paragraphs_with_subjects": len(pmap),
            "character_appearances": seen_counts,
        }


    async def book_map(self, slug: str) -> dict:
        """Aggregate the relation graph across every paragraph the user has read.

        Reads from ``concept_cache`` only — does NOT call the LLM. The map grows
        as the user reads, and shows what's been extracted so far. Edges are
        deduplicated by (from, to, label) and gain a ``weight`` count.
        """
        book = self._resolve_slug(slug)
        if not book:
            return {"error": f"book '{slug}' not found"}
        cache = self.state_data.get("concept_cache", {})
        nodes_by_id: dict[str, dict] = {}
        edges_by_key: dict[tuple, dict] = {}
        paragraphs_seen = 0

        for key, graph in cache.items():
            if not key.startswith(f"{slug}:"):
                continue
            paragraphs_seen += 1
            for n in graph.get("nodes", []) or []:
                nid = str(n.get("id") or n.get("label") or "").strip()
                if not nid:
                    continue
                cur = nodes_by_id.setdefault(nid, {"id": nid, "label": n.get("label") or nid, "kind": n.get("kind") or "", "weight": 0})
                cur["weight"] += 1
            for e in graph.get("edges", []) or []:
                f, t, lbl = e.get("from"), e.get("to"), (e.get("label") or "").strip()
                if not f or not t:
                    continue
                k = (str(f), str(t), lbl.lower())
                cur = edges_by_key.setdefault(k, {"from": f, "to": t, "label": lbl, "weight": 0})
                cur["weight"] += 1

        nodes = sorted(nodes_by_id.values(), key=lambda n: -n["weight"])
        edges = sorted(edges_by_key.values(), key=lambda e: -e["weight"])
        return {
            "slug": slug,
            "title": book["title"],
            "paragraphs_seen": paragraphs_seen,
            "paragraphs_total": None,  # filled in by frontend if needed
            "nodes": nodes,
            "edges": edges,
        }

    # ── Cross-app routing ───────────────────────────────────────
    # Each method takes the paragraph context and forwards it to another app's
    # public surface. Failures are returned as {error: ...} — the rail UI
    # surfaces the toast; nothing in reader breaks if the destination app is
    # missing or down.

    def _paragraph_text(self, slug: str, paragraph_index: int) -> tuple[str | None, str | None]:
        book = self._resolve_slug(slug)
        if not book:
            return None, None
        _meta, body = self._read_book_body(book)
        paras = _split_paragraphs(body)
        if 0 <= paragraph_index < len(paras):
            return paras[paragraph_index].lstrip("# ").strip(), book["title"]
        return None, book["title"]

    async def quote_line_for(self, slug: str, paragraph_index: int) -> dict:
        """Return a formatted quote line. UI POSTs it to /quotes/api/add itself."""
        text, title = self._paragraph_text(slug, paragraph_index)
        if not text:
            return {"error": "paragraph not found"}
        line = f"“{text[:400]}{'…' if len(text) > 400 else ''}” — {title}"
        return {"ok": True, "line": line}

    async def send_to_kb_note(self, slug: str, paragraph_index: int, note: str = "") -> dict:
        text, title = self._paragraph_text(slug, paragraph_index)
        if not text:
            return {"error": "paragraph not found"}
        from datetime import date
        slug_safe = re.sub(r"[^\w-]", "-", slug)[:40]
        fname = f"{slug_safe}-p{paragraph_index}-{date.today().isoformat()}.md"
        base = self.vault_config("notes_dir", DEFAULT_NOTES_DIR)
        rel = f"{base}/{fname}"
        body = (
            f"## Quote\n\n> {text}\n\n"
            f"## My note\n\n{note or '_(write your thought here)_'}\n\n"
            f"## Source\n\n- Book: {title}\n- Paragraph: {paragraph_index}\n"
        )
        self.vault_create_note(
            rel,
            frontmatter={
                "title": f"{title} — paragraph {paragraph_index}",
                "tags": ["reading-note", "reader"],
                "book": title,
                "slug": slug,
                "paragraph": paragraph_index,
            },
            body=body,
        )
        await self.emit(
            "reader:note_created",
            {
                "slug": slug,
                "title": title,
                "paragraph": paragraph_index,
                "path": rel,
            },
        )
        return {"ok": True, "path": rel}

    async def ask_assistant_about(self, slug: str, paragraph_index: int, question: str = "") -> dict:
        text, title = self._paragraph_text(slug, paragraph_index)
        if not text:
            return {"error": "paragraph not found"}
        q = question.strip() or "Unpack this passage."
        user_msg = (
            f"Book: {title}\n"
            f"Paragraph {paragraph_index}:\n"
            f"> {text}\n\n"
            f"Question: {q}"
        )
        try:
            answer = await self.think(
                user_msg,
                system=READER_PASSAGE_QA_SYSTEM,
                domain="text",
                temperature=0.4,
            )
            return {"ok": True, "answer": answer, "context": {"book": title, "paragraph": paragraph_index}}
        except Exception as e:
            return {"error": str(e)}

    async def search_passage_in_vault(self, slug: str, paragraph_index: int) -> dict:
        text, _title = self._paragraph_text(slug, paragraph_index)
        if not text:
            return {"error": "paragraph not found"}
        # Pick the single most distinctive proper-noun-ish token, fall back to first word > 5 chars
        tokens = re.findall(r"\b[A-Z][a-z]{3,}\b", text)
        query = tokens[0] if tokens else next((w for w in re.findall(r"\b[a-z]{6,}\b", text.lower())), "")
        if not query:
            return {"error": "no good search term in this paragraph"}
        try:
            results = await self.search(query)
            return {"ok": True, "query": query, "count": len(results) if results else 0, "results": (results or [])[:8]}
        except Exception as e:
            return {"error": str(e)}

    # ── Multi-voice TTS ──────────────────────────────────────────
    # Splits a paragraph into (speaker, text) segments based on quote marks +
    # nearby attribution ("said X", "X replied"). Each segment is synthesized
    # with that speaker's voice (looked up in voice_maps[slug]). Returned as a
    # list the frontend plays sequentially. Audio is cached by sha1(voice+text)
    # so revisits are free.

    # Kokoro voices — local high-quality TTS via voice-api (:8602). Default.
    DEFAULT_NARRATOR_VOICE = "af_heart"
    DEFAULT_DIALOGUE_VOICE = "am_adam"

    # Curly + straight quote pairs we recognize as dialogue boundaries
    _QUOTE_PATTERN = re.compile(
        r"([“”‘’\"'][^“”‘’\"'\n]{2,}?[“”‘’\"'])"
    )

    def _audio_dir(self) -> Path:
        d = Path(self.kernel.config.data_dir) / "apps" / "reader" / "audio"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _audio_cache_path(self, text: str, voice: str) -> Path:
        import hashlib
        h = hashlib.sha1(f"{voice}::{text}".encode("utf-8")).hexdigest()[:16]
        return self._audio_dir() / f"{h}.mp3"

    # Local-first preference order used when the user hasn't pinned a provider.
    # Cloud TTS is intentionally omitted — reader never silently sends paragraph
    # text to the cloud (Rule 19). To use cloud TTS, the user must explicitly
    # set reader.tts_provider = "openai-tts".
    _LOCAL_TTS_FALLBACK = ("kokoro", "xtts", "edge-tts")

    def _preferred_tts_provider(self):
        """Resolve which speak provider reader wants. Returns provider obj or None.

        Default = 'kokoro' (local). Other options: 'xtts', 'edge-tts',
        'openai-tts' (cloud — explicit opt-in only). 'auto' tries local
        providers in order; cloud TTS is NEVER included in the auto fallback
        because reader paragraphs are vault content.
        """
        wanted = (self.setting("reader.tts_provider", "kokoro") or "kokoro").strip()
        try:
            speak_cap = self.kernel.capabilities.get("speak")
        except Exception:
            return None
        if not speak_cap:
            return None
        providers = getattr(speak_cap, "providers", []) or []

        if wanted == "auto":
            # Local-first walk: pick the first AVAILABLE local provider.
            # We can't await here, so just return the first matching one and
            # let _speak_cached's availability check handle fallback.
            for name in self._LOCAL_TTS_FALLBACK:
                for p in providers:
                    if getattr(p, "name", "") == name:
                        return p
            return None
        for p in providers:
            if getattr(p, "name", "") == wanted:
                return p
        return None

    async def _speak_cached(self, text: str, voice: str = "") -> str | None:
        """Synthesize once per (text, voice, provider). Returns public URL or None."""
        text = (text or "").strip()
        if not text:
            return None
        provider = self._preferred_tts_provider()
        provider_tag = getattr(provider, "name", None) or "auto"
        cache_path = self._audio_cache_path(text, f"{provider_tag}::{voice or '_default'}")
        if not cache_path.exists():
            kwargs: dict = {}
            if voice:
                kwargs["voice"] = voice
            # Pinned-provider path: call provider.execute directly. Never fall
            # through to the system chain (which could pick a cloud provider
            # the user never asked for) — instead walk the local list.
            audio = None
            try_order = []
            if provider is not None:
                try_order.append(provider)
            # Append local fallbacks (skip the one we already added)
            try:
                speak_cap = self.kernel.capabilities.get("speak")
                for name in self._LOCAL_TTS_FALLBACK:
                    for p in getattr(speak_cap, "providers", []) or []:
                        if getattr(p, "name", "") == name and p is not provider:
                            try_order.append(p)
            except Exception:
                pass
            for p in try_order:
                try:
                    if not await p.available():
                        continue
                    audio = await p.execute(text=text[:2000], **kwargs)
                    if audio:
                        break
                except Exception as e:
                    log.warning("tts provider %s failed: %s", getattr(p, "name", "?"), e)
                    continue
            if not audio:
                log.warning("no local TTS provider succeeded for reader (cloud TTS deliberately not tried)")
                return None
            if not audio:
                return None
            src = Path(str(audio))
            if not src.exists():
                return None
            try:
                cache_path.write_bytes(src.read_bytes())
            except Exception as e:
                log.warning("audio cache write failed: %s", e)
                return f"/reader/api/audio/{src.name}"
        return f"/reader/api/audio-cache/{cache_path.name}"

    def _attribute_speaker(
        self, surrounding: str, characters: list[str], last_speaker: str | None
    ) -> str | None:
        """Heuristic speaker attribution from narration text near a quote.

        Looks for patterns like 'X said' / 'said X' / 'X replied' / 'X cried'
        in the narration immediately before/after the quote. Falls back to the
        last identified speaker (alternating-dialogue assumption), then None.
        """
        if not surrounding or not characters:
            return last_speaker
        speech_verbs = r"said|replied|asked|cried|whispered|murmured|shouted|answered|called|exclaimed|continued|went on|added|remarked"
        s_lower = surrounding.lower()
        for char in characters:
            name = char.lower()
            if re.search(rf"\b{re.escape(name)}\s+(?:{speech_verbs})\b", s_lower):
                return char
            if re.search(rf"\b(?:{speech_verbs})\s+{re.escape(name)}\b", s_lower):
                return char
        # alternation fallback
        if last_speaker and len(characters) >= 2:
            for c in characters:
                if c != last_speaker:
                    return c
        return last_speaker

    def _split_paragraph_segments(
        self, paragraph: str, slug: str
    ) -> list[dict]:
        """Split a paragraph into [{speaker, voice, text}, ...] segments.

        Quoted strings → dialogue (attributed to a character if possible).
        Everything else → narrator.
        """
        card = self.state_data.get("world_cards", {}).get(slug, {}) or {}
        char_names = [c.get("name") for c in (card.get("characters") or []) if c.get("name")]
        voice_map = self.state_data.get("voice_maps", {}).get(slug, {}) or {}
        narrator_voice = voice_map.get("__narrator__", self.DEFAULT_NARRATOR_VOICE)
        dialogue_voice = voice_map.get("__dialogue__", self.DEFAULT_DIALOGUE_VOICE)

        # Tokenize: alternating non-quote / quote pieces
        pieces = []
        last_end = 0
        for m in self._QUOTE_PATTERN.finditer(paragraph):
            if m.start() > last_end:
                pieces.append(("narrator", paragraph[last_end:m.start()]))
            pieces.append(("quote", m.group(1)))
            last_end = m.end()
        if last_end < len(paragraph):
            pieces.append(("narrator", paragraph[last_end:]))

        if not pieces:
            return [{"speaker": "narrator", "voice": narrator_voice, "text": paragraph}]

        segments: list[dict] = []
        last_speaker: str | None = None
        for i, (kind, text) in enumerate(pieces):
            text = text.strip()
            if not text:
                continue
            if kind == "narrator":
                segments.append({"speaker": "narrator", "voice": narrator_voice, "text": text})
            else:
                # Strip the surrounding quote marks for cleaner TTS, keep the words
                inner = re.sub(r"^[“”‘’\"']", "", text)
                inner = re.sub(r"[“”‘’\"']$", "", inner).strip()
                if not inner:
                    continue
                # Attribution: look at narrator text immediately before AND after
                before_ctx = pieces[i - 1][1] if i > 0 and pieces[i - 1][0] == "narrator" else ""
                after_ctx = pieces[i + 1][1] if i + 1 < len(pieces) and pieces[i + 1][0] == "narrator" else ""
                speaker = self._attribute_speaker(before_ctx + " " + after_ctx, char_names, last_speaker)
                voice = voice_map.get(speaker) if speaker else None
                voice = voice or dialogue_voice
                segments.append({"speaker": speaker or "speaker", "voice": voice, "text": inner})
                last_speaker = speaker or last_speaker
        return segments

    async def synthesize_paragraph(self, slug: str, paragraph_index: int) -> dict:
        book = self._resolve_slug(slug)
        if not book:
            return {"error": "book not found"}
        _meta, body = self._read_book_body(book)
        paras = _split_paragraphs(body)
        if not (0 <= paragraph_index < len(paras)):
            return {"error": "paragraph index out of range"}
        text = paras[paragraph_index]
        # Skip headings
        if text.startswith("##") and len(text) < 80:
            return {"segments": []}
        raw_segments = self._split_paragraph_segments(text, slug)
        out_segments = []
        for seg in raw_segments:
            url = await self._speak_cached(seg["text"], seg["voice"])
            if url:
                out_segments.append({**seg, "url": url})
        return {"segments": out_segments, "paragraph_index": paragraph_index}

    async def get_voice_map(self, slug: str) -> dict:
        return self.state_data.get("voice_maps", {}).get(slug, {}) or {}

    async def set_voice_map(self, slug: str, voices: dict) -> dict:
        vm = self.state_data.setdefault("voice_maps", {})
        vm[slug] = {k: v for k, v in voices.items() if isinstance(v, str)}
        self.save_state(self.state_data)
        return {"ok": True, "voices": vm[slug]}

    async def lookup_word(self, word: str) -> dict:
        """Look up a word via the dictionary app, preferring its vault cache.

        Tries the cached/saved copy first (free, instant), falls back to a fresh
        LLM lookup. Mirrors what dictionary's own ``/api/lookup`` route does so
        users get the same hit-rate from the reader as from the dictionary UI.
        """
        try:
            instances = getattr(getattr(self.kernel, "apps", None), "instances", {}) or {}
            dict_app = instances.get("dictionary")
            if dict_app and hasattr(dict_app, "_read_vault_word") and hasattr(dict_app, "_vault_as_lookup"):
                existing = await dict_app._read_vault_word(word)
                if existing:
                    cached = dict_app._vault_as_lookup(existing)
                    if cached:
                        cached["provenance"] = {"mode": "local", "provider": "vault"}
                        cached["cached"] = True
                        return cached
            result = await self.call_app("dictionary", "lookup", word=word)
            result.setdefault("cached", False)
            return result
        except Exception as e:
            return {"error": f"dictionary not available: {e}", "word": word}

    # ── CLI ──────────────────────────────────────────────────────

    @cli_command("list", help="List books in the vault")
    async def cli_list(self):
        for b in self._list_books():
            self.print_rich(f"[bold]{b['title']}[/bold]  ({b['slug']})")

    @cli_command("open", help="Open a book by slug")
    async def cli_open(self, slug: str):
        result = await self.open_book(slug)
        if "error" in result:
            self.print_rich(f"[red]{result['error']}[/red]")
            return
        self.print_rich(f"[bold]{result['title']}[/bold] — {len(result['paragraphs'])} paragraphs")

    # ── Web API ──────────────────────────────────────────────────

    @web_route("GET", "/api/books")
    async def api_books(self, request):
        return {"books": self._list_books(), "books_dir": self._books_dir_rel()}

    @web_route("GET", "/api/book/{slug}")
    async def api_book(self, request):
        slug = request.path_params["slug"]
        chapter = request.query_params.get("chapter")
        return await self.open_book(slug, chapter=chapter)

    @web_route("POST", "/api/progress")
    async def api_progress(self, request):
        data = await request.json()
        return await self.set_progress(
            data.get("slug", ""),
            int(data.get("paragraph", 0)),
            chapter=data.get("chapter"),
        )

    @web_route("POST", "/api/scene")
    async def api_scene(self, request):
        data = await request.json()
        return await self.generate_scene(
            data.get("slug", ""),
            int(data.get("paragraph_index", 0)),
            data.get("text", ""),
            force=bool(data.get("force", False)),
        )

    @web_route("GET", "/api/production/{slug}")
    async def api_production_get(self, request):
        slug = request.path_params["slug"]
        doc = await self._ensure_production_doc(slug)
        return doc

    @web_route("POST", "/api/production/{slug}/section")
    async def api_production_update(self, request):
        slug = request.path_params["slug"]
        data = await request.json()
        section = data.get("section", "")
        content = data.get("content", "")
        if section not in self.PRODUCTION_SECTIONS:
            return {"error": f"unknown section: {section}"}
        return await self.update_production_section(slug, section, content)

    @web_route("POST", "/api/production/{slug}/regenerate")
    async def api_production_regen(self, request):
        slug = request.path_params["slug"]
        data = await request.json()
        section = data.get("section", "")
        if section not in self.PRODUCTION_SECTIONS:
            return {"error": f"unknown section: {section}"}
        return await self.regenerate_production_section(slug, section)

    @web_route("POST", "/api/presence-map/{slug}")
    async def api_build_presence(self, request):
        slug = request.path_params["slug"]
        # If regen requested, wipe before rebuilding
        if request.query_params.get("regen") == "1":
            self.state_data.get("presence_maps", {}).pop(slug, None)
            self.save_state(self.state_data)
        return await self.build_presence_map(slug)

    @web_route("GET", "/api/presence-map/{slug}")
    async def api_get_presence(self, request):
        slug = request.path_params["slug"]
        pmap = self.state_data.get("presence_maps", {}).get(slug)
        if not pmap:
            return {"slug": slug, "built": False}
        # Compact summary
        char_counts: dict[str, int] = {}
        for names in pmap.values():
            for n in names:
                char_counts[n] = char_counts.get(n, 0) + 1
        return {
            "slug": slug,
            "built": True,
            "paragraphs_with_subjects": len(pmap),
            "character_appearances": char_counts,
        }

    @web_route("GET", "/api/canon/{slug}")
    async def api_canon_list(self, request):
        slug = request.path_params["slug"]
        d = self._canon_dir(slug)
        safe = re.sub(r"[^\w-]", "_", slug)[:60]
        return {
            "slug": slug,
            "portraits": [
                {"name": p.stem, "url": f"/reader/canon/{safe}/{p.name}"}
                for p in sorted(d.glob("*.png"))
            ],
        }

    @web_route("POST", "/api/canon/{slug}/regen")
    async def api_canon_regen(self, request):
        """Force-regenerate every canonical portrait for the book."""
        slug = request.path_params["slug"]
        # Wipe existing portraits
        d = self._canon_dir(slug)
        for p in d.glob("*.png"):
            try:
                p.unlink()
            except Exception:
                pass
        card = await self._ensure_world_card(slug)
        results = []
        for c in (card.get("characters") or [])[:6]:
            portrait = await self._ensure_character_portrait(slug, c)
            results.append({"name": c.get("name"), "ok": bool(portrait)})
        return {"slug": slug, "regenerated": results}

    @web_route("GET", "/canon/{book}/{filename}")
    async def serve_canon(self, request):
        from starlette.responses import FileResponse, JSONResponse
        book = request.path_params["book"]
        fname = request.path_params["filename"]
        for piece in (book, fname):
            if "/" in piece or "\\" in piece or ".." in piece:
                return JSONResponse({"error": "invalid path"}, status_code=400)
        path = Path(self.kernel.config.data_dir) / "apps" / "reader" / "canon" / book / fname
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(path), media_type="image/png")

    @web_route("GET", "/api/scene-prompt/{slug}/{idx}")
    async def api_scene_prompt(self, request):
        from starlette.responses import JSONResponse, PlainTextResponse
        slug = request.path_params["slug"]
        try:
            idx = int(request.path_params["idx"])
        except ValueError:
            return JSONResponse({"error": "bad index"}, status_code=400)
        safe_slug = re.sub(r"[^\w-]", "_", slug)[:60]
        path = self._scenes_dir() / f"{safe_slug}-p{idx}.prompt.txt"
        if not path.exists():
            return JSONResponse({"error": "no prompt log for this scene"}, status_code=404)
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    @web_route("GET", "/api/world-card/{slug}")
    async def api_world_card(self, request):
        slug = request.path_params["slug"]
        if request.query_params.get("regen") == "1":
            self.state_data.get("world_cards", {}).pop(slug, None)
            self.save_state(self.state_data)
        card = await self._ensure_world_card(slug)
        return {"slug": slug, "card": card}

    @web_route("GET", "/api/scene-cached")
    async def api_scene_cached(self, request):
        slug = request.query_params.get("slug", "")
        try:
            i = int(request.query_params.get("i", 0))
        except ValueError:
            i = 0
        url = self.get_cached_scene(slug, i)
        return {"url": url} if url else {"url": None}

    @web_route("POST", "/api/concepts")
    async def api_concepts(self, request):
        data = await request.json()
        return await self.generate_concepts(
            data.get("slug", ""),
            int(data.get("paragraph_index", 0)),
            data.get("text", ""),
        )

    @web_route("GET", "/scene/{filename}")
    async def serve_scene(self, request):
        from starlette.responses import FileResponse, JSONResponse
        fname = request.path_params["filename"]
        if "/" in fname or "\\" in fname or ".." in fname:
            return JSONResponse({"error": "invalid path"}, status_code=400)
        path = self._scenes_dir() / fname
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(path), media_type="image/png")

    @web_route("GET", "/api/book-map/{slug}")
    async def api_book_map(self, request):
        return await self.book_map(request.path_params["slug"])

    @web_route("POST", "/api/send/quote")
    async def api_quote_line(self, request):
        data = await request.json()
        return await self.quote_line_for(
            data.get("slug", ""), int(data.get("paragraph_index", 0))
        )

    @web_route("POST", "/api/send/kb")
    async def api_send_kb(self, request):
        data = await request.json()
        return await self.send_to_kb_note(
            data.get("slug", ""),
            int(data.get("paragraph_index", 0)),
            note=data.get("note", ""),
        )

    @web_route("POST", "/api/send/ask")
    async def api_send_ask(self, request):
        data = await request.json()
        return await self.ask_assistant_about(
            data.get("slug", ""),
            int(data.get("paragraph_index", 0)),
            question=data.get("question", ""),
        )

    @web_route("POST", "/api/send/search")
    async def api_send_search(self, request):
        data = await request.json()
        return await self.search_passage_in_vault(
            data.get("slug", ""), int(data.get("paragraph_index", 0))
        )

    @web_route("POST", "/api/highlight")
    async def api_highlight(self, request):
        data = await request.json()
        return await self.highlight(
            data.get("slug", ""),
            int(data.get("paragraph_index", 0)),
            data.get("text", ""),
            note=data.get("note", ""),
        )

    @web_route("POST", "/api/speak")
    async def api_speak(self, request):
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return {"error": "no text"}
        try:
            audio = await self.speak(text[:2000])
            if not audio:
                return {"error": "no TTS provider available"}
            filename = Path(str(audio)).name
            return {"audio": f"/reader/api/audio/{filename}", "status": "ok"}
        except Exception as e:
            return {"error": str(e)}

    @web_route("GET", "/api/audio/{filename}")
    async def api_audio(self, request):
        """Serve TTS-generated audio from the shared voice-api temp dir."""
        from starlette.responses import FileResponse, JSONResponse

        from emptyos.capabilities.audio import AUDIO_DIR, AUDIO_MIME

        filename = request.path_params["filename"]
        if "/" in filename or "\\" in filename or ".." in filename:
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        path = (AUDIO_DIR / filename).resolve()
        if not str(path).startswith(str(AUDIO_DIR.resolve())):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        mime = AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=mime)

    @web_route("GET", "/api/audio-cache/{filename}")
    async def api_audio_cache(self, request):
        """Serve reader's permanent audio cache (one file per (text, voice) hash)."""
        from starlette.responses import FileResponse, JSONResponse
        filename = request.path_params["filename"]
        if "/" in filename or "\\" in filename or ".." in filename:
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        path = (self._audio_dir() / filename).resolve()
        if not str(path).startswith(str(self._audio_dir().resolve())):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(path), media_type="audio/mpeg")

    @web_route("POST", "/api/speak-paragraph")
    async def api_speak_paragraph(self, request):
        """Multi-voice paragraph synthesis. Returns ordered segment list."""
        data = await request.json()
        return await self.synthesize_paragraph(
            data.get("slug", ""), int(data.get("paragraph_index", 0))
        )

    @web_route("GET", "/api/voices/{slug}")
    async def api_get_voices(self, request):
        slug = request.path_params["slug"]
        voices = await self.get_voice_map(slug)
        provider = (self.setting("reader.tts_provider", "kokoro") or "kokoro").strip()
        # Per-provider voice catalogs. The dropdown swaps based on the active
        # provider so users always see voices that actually work for the engine
        # they've pinned.
        catalogs = {
            "openai-tts": [
                {"id": "alloy",   "label": "Alloy — neutral, narrator-like"},
                {"id": "echo",    "label": "Echo — warm male"},
                {"id": "fable",   "label": "Fable — UK-accented male, storyteller"},
                {"id": "onyx",    "label": "Onyx — deep male"},
                {"id": "nova",    "label": "Nova — bright female"},
                {"id": "shimmer", "label": "Shimmer — soft female"},
                {"id": "ballad",  "label": "Ballad — expressive (gpt-4o-mini-tts)"},
                {"id": "sage",    "label": "Sage — calm wise (gpt-4o-mini-tts)"},
                {"id": "ash",     "label": "Ash — neutral (gpt-4o-mini-tts)"},
                {"id": "coral",   "label": "Coral — friendly female (gpt-4o-mini-tts)"},
            ],
            "kokoro": [
                {"id": "af_heart",    "label": "Heart — US female, warm (default)"},
                {"id": "af_bella",    "label": "Bella — US female"},
                {"id": "af_nicole",   "label": "Nicole — US female"},
                {"id": "af_sarah",    "label": "Sarah — US female"},
                {"id": "am_adam",     "label": "Adam — US male"},
                {"id": "am_michael",  "label": "Michael — US male"},
                {"id": "am_eric",     "label": "Eric — US male"},
                {"id": "bf_emma",     "label": "Emma — UK female"},
                {"id": "bf_isabella", "label": "Isabella — UK female"},
                {"id": "bm_george",   "label": "George — UK male"},
                {"id": "bm_lewis",    "label": "Lewis — UK male"},
                {"id": "zf_xiaoxiao", "label": "Xiaoxiao — Mandarin female"},
                {"id": "zf_xiaoyi",   "label": "Xiaoyi — Mandarin female"},
                {"id": "zm_yunxi",    "label": "Yunxi — Mandarin male"},
                {"id": "zm_yunjian",  "label": "Yunjian — Mandarin male"},
                {"id": "jf_alpha",    "label": "Alpha — Japanese female"},
                {"id": "jm_kumo",     "label": "Kumo — Japanese male"},
            ],
            "edge-tts": [
                {"id": "en-US-AriaNeural",    "label": "Aria — US female, neutral"},
                {"id": "en-US-GuyNeural",     "label": "Guy — US male"},
                {"id": "en-US-JennyNeural",   "label": "Jenny — US female, warm"},
                {"id": "en-GB-SoniaNeural",   "label": "Sonia — UK female, mature"},
                {"id": "en-GB-RyanNeural",    "label": "Ryan — UK male"},
                {"id": "en-GB-LibbyNeural",   "label": "Libby — UK female, young"},
                {"id": "en-AU-NatashaNeural", "label": "Natasha — AU female"},
                {"id": "en-AU-WilliamNeural", "label": "William — AU male"},
                {"id": "en-IE-EmilyNeural",   "label": "Emily — IE female"},
            ],
        }
        # Defaults pre-selected for whichever provider is active
        default_pairs = {
            "openai-tts": {"narrator": "fable",      "dialogue": "onyx"},
            "kokoro":     {"narrator": "af_heart",   "dialogue": "am_adam"},
            "edge-tts":   {"narrator": "en-US-AriaNeural", "dialogue": "en-US-GuyNeural"},
            "xtts":       {"narrator": "", "dialogue": ""},
            "auto":       {"narrator": "af_heart",   "dialogue": "am_adam"},
        }
        return {
            "slug": slug,
            "voices": voices,
            "active_provider": provider,
            "defaults": default_pairs.get(provider, default_pairs["auto"]),
            "available": catalogs.get(provider, catalogs["edge-tts"] + catalogs["openai-tts"]),
        }

    @web_route("POST", "/api/voices/{slug}")
    async def api_set_voices(self, request):
        slug = request.path_params["slug"]
        data = await request.json()
        return await self.set_voice_map(slug, data.get("voices", {}))

    @web_route("POST", "/api/lookup")
    async def api_lookup(self, request):
        data = await request.json()
        word = (data.get("word") or "").strip()
        if not word:
            return {"error": "no word"}
        return await self.lookup_word(word)

    # ── Hub panel ────────────────────────────────────────────────

    async def panel_currently_reading(self):
        progress = self.state_data.get("progress", {})
        if not progress:
            return None
        items = []
        for slug, p in sorted(
            progress.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True
        )[:3]:
            book = self._resolve_slug(slug)
            title = book["title"] if book else slug
            para = p.get("paragraph", 0)
            items.append({"label": title, "detail": f"¶ {para}", "url": f"/reader/#{slug}"})
        return items or None

    # ── Voice intents ────────────────────────────────────────────

    async def voice_open(self, query: str) -> dict:
        q = (query or "").strip().lower()
        if not q:
            return {"say": "Which book?"}
        for b in self._list_books():
            if q in b["title"].lower() or q == b["slug"]:
                await self.open_book(b["slug"])
                return {"say": f"Opening {b['title']}."}
        return {"say": f"I couldn't find a book matching '{query}'."}

    async def voice_read_aloud(self) -> dict:
        cur = self.state_data.get("current")
        if not cur:
            return {"say": "No book is open."}
        return {"say": "Use the Read paragraph button on the page."}
