"""Dictionary — word lookup, vault storage, SRS flashcards, quizzes.

Provides definitions via LLM, saves words to vault as markdown,
spaced repetition review, and quiz generation from saved vocabulary.
External API: Datamuse for autocomplete suggestions.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from emptyos.sdk import BaseApp, cli_command, load_json, parse_frontmatter, parse_llm_json, save_json, strip_frontmatter, web_route
from emptyos.capabilities.audio import AUDIO_DIR as VOICE_AUDIO_DIR, AUDIO_MIME

from . import srs as _srs

DEFAULT_DEFAULT_DICT_FOLDER = "30_Resources/Learning/Dictionary"


class DictionaryApp(BaseApp):

    # ── Helpers ──────────────────────────────────────────────

    _srs_path = _srs._srs_path
    _load_srs = _srs._load_srs
    _save_srs = _srs._save_srs

    def _vault_dir(self) -> str:
        return self.vault_config("words_dir", DEFAULT_DEFAULT_DICT_FOLDER)

    async def get_summary(self) -> dict:
        """Summary for staff observers — word count, due reviews, recent words."""
        words = await self._vault_words()
        srs = self._load_srs()
        today = date.today().isoformat()
        due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
        recent = words[-10:] if words else []
        return {
            "dictionary_words": len(words),
            "recent_words": recent,
            "in_srs": len(srs),
            "due_for_review": due,
        }

    async def _vault_words(self) -> list[str]:
        """List saved words from vault dictionary folder."""
        folder = self.vault_config_path("words_dir", DEFAULT_DEFAULT_DICT_FOLDER)
        if folder and folder.exists():
            return sorted(
                f.stem for f in folder.glob("*.md") if not f.name.startswith("_")
            )
        return []

    async def _read_vault_word(self, word: str) -> dict | None:
        """Read a saved word's markdown and parse frontmatter + content."""
        path = f"{self._vault_dir()}/{word}.md"
        try:
            content = await self.read(path)
        except Exception:
            return None
        meta = parse_frontmatter(content)
        body = strip_frontmatter(content).strip()
        return {"word": word, "meta": meta, "body": body, "raw": content}

    @staticmethod
    def _parse_sections(body: str) -> dict[str, str]:
        """Split markdown body into `## Section` -> text map."""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in body.split("\n"):
            if line.startswith("## "):
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        return sections

    @staticmethod
    def _parse_meanings_legacy(body: str) -> dict | None:
        """Parse a legacy `## Meanings` / `### POS` dictionary layout.

        Picks the first prose line after the first `### POS` as the definition,
        and the first `> "..."` line as the example. Skips synonym lists and
        alternate senses separated by `---` — the quick-lookup card only needs
        the primary meaning.
        """
        pos = ""
        definition = ""
        example = ""
        seen_pos = False
        for raw in body.split("\n"):
            s = raw.strip()
            if s.startswith("### "):
                if definition:
                    break
                if not pos:
                    pos = s[4:].strip()
                seen_pos = True
                continue
            if not seen_pos:
                continue
            if s == "---":
                if definition:
                    break
                continue
            if not s:
                continue
            if s.startswith("> "):
                if not example:
                    example = s[2:].strip().strip('"')
                continue
            if not definition:
                definition = s
        if not definition:
            return None
        return {"definition": definition, "part_of_speech": pos, "example": example}

    def _vault_as_lookup(self, entry: dict) -> dict | None:
        """Reshape a saved vault entry into the same shape as `lookup()`.

        Returns None if the saved note lacks a definition (fall back to LLM).
        Handles both the EmptyOS `## Definition` layout and the legacy
        `## Meanings` / `### POS` layout produced by some vault dictionary tools.
        """
        meta = entry.get("meta", {}) or {}
        sections = self._parse_sections(entry.get("body", ""))
        definition = sections.get("Definition", "").strip()
        part_of_speech = meta.get("part_of_speech", "")
        example = sections.get("Example", "").strip()
        if example.startswith("> "):
            example = example[2:].strip()

        if not definition:
            legacy = self._parse_meanings_legacy(sections.get("Meanings", ""))
            if legacy:
                definition = legacy["definition"]
                if not part_of_speech:
                    part_of_speech = legacy["part_of_speech"]
                if not example:
                    example = legacy["example"]

        if not definition:
            return None
        synonyms = [s.strip() for s in sections.get("Synonyms", "").split(",") if s.strip()]
        antonyms = [s.strip() for s in sections.get("Antonyms", "").split(",") if s.strip()]
        return {
            "word": entry.get("word", ""),
            "phonetic": meta.get("phonetic", ""),
            "part_of_speech": part_of_speech,
            "definition": definition,
            "example": example,
            "synonyms": synonyms,
            "antonyms": antonyms,
            "chinese": meta.get("chinese", "") or sections.get("Chinese", "").strip(),
            "etymology": sections.get("Etymology", "").strip(),
            "usage_notes": sections.get("Usage Notes", "").strip(),
            "from_vault": True,
        }

    # ── Core: LLM Lookup ─────────────────────────────────────

    async def lookup(self, word: str) -> dict:
        """Look up a word using LLM."""
        prompt = (
            f'Define the English word "{word}". Return a JSON object:\n'
            f'{{"word": str, "phonetic": str, "part_of_speech": str, '
            f'"definition": str, "example": str, "synonyms": [str], '
            f'"antonyms": [str], "chinese": str, "etymology": str, '
            f'"usage_notes": str}}\n'
            f'Return ONLY valid JSON, no markdown fences.'
        )
        try:
            response = await self.think(prompt, domain="text", temperature=0.3)
            data = parse_llm_json(response)
            data.setdefault("word", word)
            return data
        except Exception:
            basic = await self.think(
                f'Define "{word}" in one clear sentence.', domain="text", temperature=0.3
            )
            return {
                "word": word,
                "definition": basic,
                "error": "structured lookup failed",
            }

    # ── CLI ───────────────────────────────────────────────────

    @cli_command("dict", help="Look up a word")
    async def cmd_dict(self, word: str = ""):
        if not word:
            print("  Usage: eos dict <word>")
            return
        result = await self.lookup(word)
        print(f"\n  {result.get('word', word)}", end="")
        if result.get("phonetic"):
            print(f"  {result['phonetic']}", end="")
        print()
        if result.get("part_of_speech"):
            print(f"  [{result['part_of_speech']}]")
        print(f"  {result.get('definition', 'No definition')}")
        if result.get("example"):
            print(f"  Example: {result['example']}")
        if result.get("chinese"):
            print(f"  Chinese: {result['chinese']}")
        if result.get("synonyms"):
            print(f"  Synonyms: {', '.join(result['synonyms'][:5])}")
        print()

    # ── API: Lookup ──────────────────────────────────────────

    @web_route("GET", "/api/lookup")
    async def api_lookup(self, request):
        word = request.query_params.get("word", "").strip()
        if not word:
            return {"error": "word parameter required"}
        self._track_lookup(word)

        # Prefer the saved vault copy — faster, free, and matches what the user has.
        # Skip when `?fresh=1` so the user can force-refresh an LLM lookup.
        if request.query_params.get("fresh") != "1":
            existing = await self._read_vault_word(word)
            if existing:
                cached = self._vault_as_lookup(existing)
                if cached:
                    cached["provenance"] = {"mode": "local", "provider": "vault", "model": None}
                    return cached

        result = await self.lookup(word)
        result["provenance"] = self.last_provenance()
        return result

    # ── API: Save to vault ───────────────────────────────────

    @web_route("POST", "/api/save")
    async def api_save(self, request):
        """Save a word to vault as markdown."""
        body = await request.json()
        word = body.get("word", "").strip()
        if not word:
            return {"error": "word required"}

        definition = body.get("definition", "")
        phonetic = body.get("phonetic", "")
        part_of_speech = body.get("part_of_speech", "")
        example = body.get("example", "")
        synonyms = body.get("synonyms", [])
        antonyms = body.get("antonyms", [])
        chinese = body.get("chinese", "")
        etymology = body.get("etymology", "")
        usage_notes = body.get("usage_notes", "")
        favorite = body.get("favorite", False)

        # Build markdown
        today = date.today().isoformat()
        lines = [
            "---",
            f"word: {word}",
            f"phonetic: {phonetic}" if phonetic else None,
            f"part_of_speech: {part_of_speech}" if part_of_speech else None,
            f"chinese: {chinese}" if chinese else None,
            f"favorite: {str(favorite).lower()}",
            f"created: {today}",
            "tags:",
            "  - vocabulary",
            "---",
            "",
            f"# {word}",
            "",
        ]
        lines = [l for l in lines if l is not None]

        if phonetic:
            lines.append(f"**Pronunciation**: {phonetic}")
            lines.append("")
        if part_of_speech:
            lines.append(f"**Part of Speech**: {part_of_speech}")
            lines.append("")

        lines.append("## Definition")
        lines.append("")
        lines.append(definition)
        lines.append("")

        if example:
            lines.append("## Example")
            lines.append("")
            lines.append(f"> {example}")
            lines.append("")

        if etymology:
            lines.append("## Etymology")
            lines.append("")
            lines.append(etymology)
            lines.append("")

        if usage_notes:
            lines.append("## Usage Notes")
            lines.append("")
            lines.append(usage_notes)
            lines.append("")

        if synonyms:
            lines.append("## Synonyms")
            lines.append("")
            lines.append(", ".join(synonyms))
            lines.append("")

        if antonyms:
            lines.append("## Antonyms")
            lines.append("")
            lines.append(", ".join(antonyms))
            lines.append("")

        if chinese:
            lines.append("## Chinese")
            lines.append("")
            lines.append(chinese)
            lines.append("")

        content = "\n".join(lines)
        path = f"{self._vault_dir()}/{word}.md"

        await self.write(path, content)

        # Initialize SRS entry
        srs = self._load_srs()
        if word not in srs:
            srs[word] = {
                "level": 0,
                "streak": 0,
                "reviews": 0,
                "next_review": today,
                "last_reviewed": None,
            }
            self._save_srs(srs)

        await self.emit("dictionary:word_saved", {"word": word})
        return {"ok": True, "word": word, "path": path}

    # ── API: Vault listing ───────────────────────────────────

    @web_route("GET", "/api/vault")
    async def api_vault(self, request):
        """List all saved vault words with SRS status."""
        words = await self._vault_words()
        srs = self._load_srs()
        today = date.today().isoformat()
        result = []
        for w in words:
            entry = srs.get(w, {})
            result.append({
                "word": w,
                "level": entry.get("level", 0),
                "streak": entry.get("streak", 0),
                "next_review": entry.get("next_review", today),
                "favorite": False,  # will be enriched below
            })
        # Read favorite status from vault if available
        for item in result:
            data = await self._read_vault_word(item["word"])
            if data and data.get("meta", {}).get("favorite") == "true":
                item["favorite"] = True
        return {"words": result, "total": len(result)}

    @web_route("GET", "/api/vault/{word}")
    async def api_vault_word(self, request):
        """Get a single saved word's full content."""
        word = request.path_params.get("word", "")
        data = await self._read_vault_word(word)
        if not data:
            return {"error": f"Word '{word}' not found in vault"}
        srs = self._load_srs()
        entry = srs.get(word, {})
        parsed = self._vault_as_lookup(data) or {}
        return {
            "word": word,
            "meta": data["meta"],
            "body": data["body"],
            "srs": entry,
            "definition": parsed.get("definition", ""),
            "example": parsed.get("example", ""),
            "chinese": parsed.get("chinese", ""),
            "phonetic": parsed.get("phonetic", ""),
            "part_of_speech": parsed.get("part_of_speech", ""),
            "synonyms": parsed.get("synonyms", []),
            "antonyms": parsed.get("antonyms", []),
            "etymology": parsed.get("etymology", ""),
            "usage_notes": parsed.get("usage_notes", ""),
        }

    # ── API: Favorite toggle ─────────────────────────────────

    @web_route("POST", "/api/favorite")
    async def api_favorite(self, request):
        """Toggle favorite status for a word."""
        body = await request.json()
        word = body.get("word", "").strip()
        if not word:
            return {"error": "word required"}

        data = await self._read_vault_word(word)
        if not data:
            return {"error": f"Word '{word}' not found"}

        raw = data["raw"]
        current = data["meta"].get("favorite", "false") == "true"
        new_val = not current

        # Update frontmatter
        if "favorite:" in raw:
            raw = raw.replace(
                f"favorite: {str(current).lower()}",
                f"favorite: {str(new_val).lower()}",
            )
        else:
            # Insert favorite field after word field
            raw = raw.replace("---\n", f"---\nfavorite: {str(new_val).lower()}\n", 1)

        path = f"{self._vault_dir()}/{word}.md"
        await self.write(path, raw)
        return {"ok": True, "word": word, "favorite": new_val}

    # ── API: Autocomplete suggestions (Datamuse) ─────────────

    @web_route("GET", "/api/suggest")
    async def api_suggest(self, request):
        """Autocomplete suggestions via Datamuse API."""
        q = request.query_params.get("q", "").strip()
        if not q or len(q) < 2:
            return {"suggestions": []}
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"https://api.datamuse.com/sug?s={q}&max=8"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"suggestions": [d["word"] for d in data]}
            return {"suggestions": []}
        except Exception:
            return {"suggestions": []}

    # ── API: Word of the Day ─────────────────────────────────

    @web_route("GET", "/api/word-of-day")
    async def api_word_of_day(self, request):
        """Generate an interesting word of the day via LLM."""
        today_str = date.today().isoformat()
        # Check cache
        cache = self.load_state({})
        if cache.get("wotd_date") == today_str and cache.get("wotd"):
            return cache["wotd"]

        prompt = (
            "Pick one interesting, uncommon but useful English vocabulary word. "
            "Return JSON: {\"word\": str, \"phonetic\": str, \"definition\": str, "
            "\"example\": str, \"chinese\": str, \"fun_fact\": str}\n"
            "Pick a different word each time. Return ONLY JSON."
        )
        try:
            resp = await self.think(prompt, domain="text", temperature=0.8)
            data = parse_llm_json(resp)
            data["date"] = today_str
            # Cache it
            cache["wotd_date"] = today_str
            cache["wotd"] = data
            self.save_state(cache)
            return data
        except Exception as e:
            return {"error": str(e)}

    # ── API: SRS (bound from srs.py) ─────────────────────────

    api_srs_deck = _srs.api_srs_deck
    api_srs_review = _srs.api_srs_review
    api_srs_stats = _srs.api_srs_stats

    # ── API: Frequency tracking ────────────────────────────────

    def _freq_path(self) -> Path:
        return self.data_dir / "frequency.json"

    def _load_freq(self) -> dict:
        return load_json(self._freq_path(), {})

    def _save_freq(self, data: dict):
        save_json(self._freq_path(), data)

    def _track_lookup(self, word: str):
        """Increment lookup frequency for a word."""
        freq = self._load_freq()
        today = date.today().isoformat()
        if word not in freq:
            freq[word] = {"count": 0, "first": today, "last": today}
        freq[word]["count"] += 1
        freq[word]["last"] = today
        self._save_freq(freq)

    @web_route("GET", "/api/frequency")
    async def api_frequency(self, request):
        """Word lookup frequency tracking {word: {count, first, last}}."""
        freq = self._load_freq()
        word = request.query_params.get("word", "").strip()
        if word:
            return freq.get(word, {"count": 0, "first": None, "last": None})
        # Return all, sorted by count descending
        ranked = sorted(freq.items(), key=lambda x: x[1]["count"], reverse=True)
        limit = int(request.query_params.get("limit", "50"))
        return {"frequencies": dict(ranked[:limit]), "total_words": len(freq)}

    # ── API: Explain in context ──────────────────────────────

    @web_route("GET", "/api/explain")
    async def api_explain(self, request):
        """Explain a word in context via LLM."""
        word = request.query_params.get("word", "").strip()
        context = request.query_params.get("context", "").strip()
        if not word:
            return {"error": "word parameter required"}

        self._track_lookup(word)

        if context:
            prompt = (
                f'Explain the word "{word}" as used in this context:\n'
                f'"{context}"\n\n'
                f'Return JSON: {{"word": str, "meaning_in_context": str, '
                f'"general_definition": str, "chinese": str, '
                f'"usage_note": str}}\nReturn ONLY valid JSON.'
            )
        else:
            prompt = (
                f'Explain the word "{word}" briefly.\n'
                f'Return JSON: {{"word": str, "meaning_in_context": str, '
                f'"general_definition": str, "chinese": str, '
                f'"usage_note": str}}\nReturn ONLY valid JSON.'
            )

        result = parse_llm_json(
            await self.think(prompt, domain="text", temperature=0.3),
            fallback={"word": word, "meaning_in_context": "", "error": "LLM parse failed"},
        )
        return result

    # ── API: Spelling correction ─────────────────────────────

    @web_route("POST", "/api/didyoumean")
    async def api_didyoumean(self, request):
        """Spelling correction using Datamuse API."""
        body = await request.json()
        q = body.get("q", "").strip()
        if not q:
            return {"suggestions": []}
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"https://api.datamuse.com/words?sl={q}&max=5"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"suggestions": [d["word"] for d in data]}
            return {"suggestions": []}
        except Exception:
            return {"suggestions": []}

    # ── API: Delete vault word ───────────────────────────────

    @web_route("DELETE", "/api/vault/{word}")
    async def api_delete_vault_word(self, request):
        """Delete a saved word from vault."""
        word = request.path_params.get("word", "")
        if not word:
            return {"error": "word required"}

        path = f"{self._vault_dir()}/{word}.md"
        vault = self.kernel.config.notes_path
        if vault:
            full_path = vault / self._vault_dir() / f"{word}.md"
            if full_path.exists():
                full_path.unlink()
            else:
                return {"error": f"Word '{word}' not found in vault"}
        else:
            try:
                await self.write(path, "")  # fallback: overwrite with empty
            except Exception:
                return {"error": f"Could not delete '{word}'"}

        # Also remove from SRS
        srs = self._load_srs()
        if word in srs:
            del srs[word]
            self._save_srs(srs)

        # Remove from frequency
        freq = self._load_freq()
        if word in freq:
            del freq[word]
            self._save_freq(freq)

        await self.emit("dictionary:word_deleted", {"word": word})
        return {"ok": True, "word": word, "deleted": True}

    # ── API: Quiz ────────────────────────────────────────────

    @web_route("GET", "/api/quiz")
    async def api_quiz(self, request):
        """Generate a 5-question multiple choice quiz from saved words."""
        count = int(request.query_params.get("count", "5"))
        vault_words = await self._vault_words()

        if len(vault_words) < 4:
            return {"error": "Need at least 4 saved words for a quiz"}

        # Pick quiz words
        quiz_words = random.sample(vault_words, min(count, len(vault_words)))
        questions = []

        for w in quiz_words:
            data = await self._read_vault_word(w)
            if not data:
                continue
            # Extract definition preview from body
            definition = data["meta"].get("definition", "")
            if not definition:
                # Get first meaningful line from body
                for line in data["body"].split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("**") and not line.startswith(">"):
                        definition = line
                        break

            if not definition:
                definition = data["body"][:100]

            # Generate wrong options
            others = [x for x in vault_words if x != w]
            wrong = random.sample(others, min(3, len(others)))
            options = [w] + wrong
            random.shuffle(options)

            questions.append({
                "definition": definition,
                "chinese": data["meta"].get("chinese", ""),
                "options": options,
                "answer": w,
            })

        return {"questions": questions, "total": len(questions)}

    # ── API: Pronunciation + Examples ──────────────────────────

    @web_route("GET", "/api/pronounce/{word}")
    async def api_pronounce(self, request):
        """Generate pronunciation audio via TTS. Returns a browser-playable URL."""
        word = request.path_params["word"]
        try:
            audio = await self.speak(word)
            if not audio:
                return {"word": word, "status": "tts_unavailable", "error": "no speak provider available"}
            filename = Path(str(audio)).name
            return {
                "word": word,
                "audio_url": f"/dictionary/api/audio/{filename}",
                "status": "ok",
            }
        except Exception as e:
            return {"word": word, "error": str(e), "status": "tts_unavailable"}

    @web_route("GET", "/api/audio/{filename}")
    async def api_audio(self, request):
        """Serve TTS audio from the voice-api temp directory."""
        from starlette.responses import FileResponse, JSONResponse

        filename = request.path_params["filename"]
        if "/" in filename or "\\" in filename or ".." in filename:
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        path = (VOICE_AUDIO_DIR / filename).resolve()
        if not str(path).startswith(str(VOICE_AUDIO_DIR.resolve())):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        mime = AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=mime)

    @web_route("GET", "/api/examples/{word}")
    async def api_examples(self, request):
        """Generate example sentences for a word via LLM."""
        word = request.path_params["word"]
        count = int(request.query_params.get("count", "3"))
        try:
            response = await self.think(
                f"Give {count} example sentences using the word '{word}'. "
                f"Vary difficulty (easy → advanced). "
                f"Return as JSON array of strings, nothing else.",
                domain="text", temperature=0.7,
            )
            examples = parse_llm_json(response, fallback=[
                f"The {word} was remarkable.",
                f"She described it as {word}.",
                f"This is a {word} example.",
            ])
            return {"word": word, "examples": examples}
        except Exception:
            return {"word": word, "examples": [f"The {word} was remarkable.", f"She described it as {word}.", f"This is a {word} example."]}

    @web_route("GET", "/api/export")
    async def api_export(self, request):
        """Export vocabulary as JSON (Anki-compatible format)."""
        vault_words = await self._vault_words()
        cards = []
        for w in vault_words:
            data = await self._read_vault_word(w)
            if data:
                cards.append({
                    "front": w,
                    "back": data["meta"].get("definition", data["body"][:200]),
                    "chinese": data["meta"].get("chinese", ""),
                    "phonetic": data["meta"].get("phonetic", ""),
                    "tags": data["meta"].get("tags", ""),
                })
        return {"cards": cards, "total": len(cards), "format": "anki-compatible"}

    @web_route("GET", "/api/word-addons/{word}")
    async def api_word_addons(self, request):
        """Return user-configured word addons (external sites) with {word} substituted.

        Addons live in emptyos.toml under [apps.dictionary] word_addons = [...].
        Each item: {id, label, icon, url_template}. No built-in list — everything is user-configured.
        """
        word = (request.path_params.get("word") or "").strip()
        if not word:
            return {"addons": []}
        raw = self.app_config("word_addons", []) or []
        addons = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tmpl = item.get("url_template") or ""
            if not tmpl:
                continue
            addons.append({
                "id": item.get("id") or item.get("label") or "addon",
                "label": item.get("label") or item.get("id") or "Open",
                "icon": item.get("icon") or "",
                "url": tmpl.replace("{word}", quote(word, safe="")),
            })
        return {"addons": addons}
