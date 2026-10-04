"""dictionary — word lookup + enrichment routes — define / explain / examples / WOTD / suggest / pronounce-audio.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The 'get a definition' surface: the core LLM lookup() (structured + basic fallback), the /api/lookup route (vault-cached, fresh-override), explain-in-context, example generation, word-of-the-day, local autocomplete + did-you-mean (suggest.py), and the TTS pronounce-audio + audio-serve routes. Wraps the LLM personas from shared..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._read_vault_word/_vault_as_lookup/_track_lookup (spine data layer); self.think/self.speak/self.last_provenance (capabilities); the DICTIONARY_*_SYSTEM personas.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote
from emptyos.capabilities.audio import AUDIO_DIR as VOICE_AUDIO_DIR
from emptyos.capabilities.audio import AUDIO_MIME
from emptyos.sdk import SpendCapReached, parse_llm_json, web_route
from .shared import DICTIONARY_BASIC_SYSTEM, DICTIONARY_CONTEXT_SYSTEM, DICTIONARY_EXAMPLES_SYSTEM, DICTIONARY_LOOKUP_SYSTEM, DICTIONARY_LOOKUP_TEMPERATURE, DICTIONARY_LOOKUP_USER, DICTIONARY_WOTD_SYSTEM
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only

# Shown when the daemon's monthly spend cap stops a paid lookup.
LOOKUP_LIMIT_MESSAGE = (
    "This month's AI limit is reached, so this word can't be looked up now. "
    "Saved words and common words still work, and new lookups resume next month."
)


# ─── Bind to DictionaryApp class as ────────────────────────────────
#   _definition_pack = _lookup._definition_pack
#   _known_word = _lookup._known_word
#   lookup           = _lookup.lookup
#   voice_lookup     = _lookup.voice_lookup
#   api_lookup       = _lookup.api_lookup
#   api_suggest      = _lookup.api_suggest
#   api_word_of_day  = _lookup.api_word_of_day
#   api_explain      = _lookup.api_explain
#   api_didyoumean   = _lookup.api_didyoumean
#   api_pronounce    = _lookup.api_pronounce
#   api_audio        = _lookup.api_audio
#   api_examples     = _lookup.api_examples
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


def _definition_pack(self):
    """The configured definition pack, opened once; None when unset or unusable.

    ``[apps.dictionary] definition_pack_path`` — absolute, or relative to the
    directory holding emptyos.toml. Unset keeps the old behaviour exactly.
    """
    if hasattr(self, "_definition_pack_cache"):
        return self._definition_pack_cache
    pack = None
    raw = str(self.app_config("definition_pack_path", "") or "").strip()
    if raw:
        from emptyos.basepath import resolve_under_base

        from .definition_pack import DefinitionPack

        pack = DefinitionPack(resolve_under_base(raw, self.kernel.config.path.parent))
        if not pack.available:
            self.kernel.syslog.warn("dictionary", f"definition pack unused — {pack.error}")
            pack = None
    self._definition_pack_cache = pack
    return pack


async def lookup(self, word: str, fresh: bool = False) -> dict:
    """Look up a word: the definition pack first, then the model.

    A pack hit costs nothing and is the same for every learner; ``fresh``
    skips the pack to force a new model answer.
    """
    pack = None if fresh else self._definition_pack()
    if pack is not None:
        entry = pack.get(word)
        if entry:
            entry["provenance"] = {
                "mode": "local",
                "provider": "definition-pack",
                "model": pack.meta.get("model"),
                # When the pack was generated — a pack answer is a snapshot.
                "as_of": pack.meta.get("generated_at"),
            }
            return entry
    try:
        response = await self.think(
            DICTIONARY_LOOKUP_USER.format(word=word),
            system=DICTIONARY_LOOKUP_SYSTEM,
            domain="text",
            temperature=DICTIONARY_LOOKUP_TEMPERATURE,
        )
        data = parse_llm_json(response)
        data.setdefault("word", word)
        # Provenance is set by the caller from the real call, never taken from
        # model output — a reply could otherwise claim to be a pack answer.
        data.pop("provenance", None)
        return data
    except SpendCapReached:
        raise  # a second call would be refused for the same reason
    except Exception:
        basic = await self.think(
            f'Define "{word}".',
            system=DICTIONARY_BASIC_SYSTEM,
            domain="text",
            temperature=0.3,
        )
        return {
            "word": word,
            "definition": basic,
            "error": "structured lookup failed",
        }


async def voice_lookup(self, word: str = "") -> dict:
    """Voice verb — define one word, spoken, plus an entity card.

    The phone-shaped wrapper `dictionary.lookup` had no voice surface, so the
    bridge offered nothing for "what does X mean" and the model reached for a
    vault search instead (telegram baseline w03, 2026-09-30). Same source
    order as `api_lookup`: the saved vault copy first, then `lookup()`.
    """
    word = (word or "").strip()
    if not word:
        return {"say": "Which word should I look up?"}
    self._track_lookup(word)
    entry = None
    existing = await self._read_vault_word(word)
    if existing:
        entry = self._vault_as_lookup(existing)
    if not entry:
        try:
            entry = await self.lookup(word)
        except SpendCapReached:
            return {"say": LOOKUP_LIMIT_MESSAGE}
    definition = str((entry or {}).get("definition") or "").strip()
    if not definition:
        return {"say": f"I couldn't find a definition for {word}."}
    pos = str(entry.get("part_of_speech") or "").strip()
    example = str(entry.get("example") or "").strip()
    chinese = str(entry.get("chinese") or "").strip()
    say = f"{word} ({pos}): {definition}" if pos else f"{word}: {definition}"
    if chinese:
        say += f" 中文：{chinese}。"
    if example:
        say += f" For example: {example}"
    fields = [{"label": k, "value": v} for k, v in (
        ("Part of speech", pos), ("中文", chinese), ("Example", example)) if v]
    return {
        "say": say,
        "card": {"renderer": "entity-card", "data": {
            "title": word, "subtitle": definition, "fields": fields}},
        "link": {"text": "Open in dictionary", "href": f"/dictionary/#lookup/{quote(word)}"},
    }


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

    fresh = request.query_params.get("fresh") == "1"
    try:
        result = await self.lookup(word, fresh=fresh)
    except SpendCapReached:
        # `error` too: the shared dictionary popup and other consumers only
        # check that key, and would otherwise draw an empty definition.
        return {"word": word, "limit_reached": True, "message": LOOKUP_LIMIT_MESSAGE,
                "error": LOOKUP_LIMIT_MESSAGE}
    result.setdefault("provenance", self.last_provenance())
    return result


async def _word_index(self):
    """The suggestion vocabulary, built once per app instance, off the loop.

    Local by design (editions M10): what a learner types never leaves the
    machine. Vocabulary: the definition pack's headwords when a pack is
    configured, else wordfreq's list — see ``suggest.load_vocabulary``.
    """
    index = getattr(self, "_word_index_cache", None)
    if index is None:
        import asyncio

        from .suggest import WordIndex, load_vocabulary

        pack = self._definition_pack()

        def build():
            # All of it off the loop, the pack's ~50k-row read included.
            headwords = pack.headwords() if pack is not None else None
            return WordIndex(load_vocabulary(headwords), spellings_trusted=bool(headwords))

        index = await asyncio.to_thread(build)
        self._word_index_cache = index
    return index


@web_route("GET", "/api/suggest")
async def api_suggest(self, request):
    """Autocomplete: known words starting with `q`, most frequent first."""
    q = request.query_params.get("q", "").strip()
    if len(q) < 2:
        return {"suggestions": []}
    index = await _word_index(self)
    return {"suggestions": index.complete(q, limit=8)}


@web_route("GET", "/api/word-of-day")
async def api_word_of_day(self, request):
    """Generate an interesting word of the day via LLM."""
    today_str = date.today().isoformat()
    # Check cache
    cache = self.load_state({})
    if cache.get("wotd_date") == today_str and cache.get("wotd"):
        return cache["wotd"]

    try:
        resp = await self.think(
            "Pick one interesting, uncommon-but-useful English word for today.",
            system=DICTIONARY_WOTD_SYSTEM,
            domain="text",
            temperature=0.8,
        )
        data = parse_llm_json(resp)
        data["date"] = today_str
        # Cache it
        cache["wotd_date"] = today_str
        cache["wotd"] = data
        self.save_state(cache)
        return data
    except Exception as e:
        return {"error": str(e)}


@web_route("GET", "/api/explain")
async def api_explain(self, request):
    """Explain a word in context via LLM."""
    word = request.query_params.get("word", "").strip()
    context = request.query_params.get("context", "").strip()
    if not word:
        return {"error": "word parameter required"}

    self._track_lookup(word)

    if context:
        user_msg = (
            f'Word: "{word}"\n'
            f'Context: "{context}"'
        )
    else:
        user_msg = f'Word: "{word}"\nContext: (none — give the broader sense)'

    result = parse_llm_json(
        await self.think(
            user_msg,
            system=DICTIONARY_CONTEXT_SYSTEM,
            domain="text",
            temperature=0.3,
        ),
        fallback={"word": word, "meaning_in_context": "", "error": "LLM parse failed"},
    )
    return result


@web_route("POST", "/api/didyoumean")
async def api_didyoumean(self, request):
    """Spelling correction: known words close to `q` in spelling or sound."""
    body = await self.safe_json(request)
    q = str(body.get("q", "") if isinstance(body, dict) else "").strip()
    if not q:
        return {"suggestions": []}
    import asyncio

    index = await _word_index(self)
    return {"suggestions": await asyncio.to_thread(index.did_you_mean, q, 5)}


def _known_word(self, word: str) -> bool:
    """A headword in the shipped definition pack, or a picture name."""
    w = str(word or "").strip()
    pack = self._definition_pack()
    if pack is not None and pack.get(w.lower()):
        return True
    return any(str(item.get("name", "")).lower() == w.lower()
               for item in (getattr(self, "items", None) or {}).values())


@web_route("GET", "/api/pronounce/{word}")
async def api_pronounce(self, request):
    """Generate pronunciation audio via TTS. Returns a browser-playable URL."""
    word = request.path_params["word"]
    try:
        # "word" is claimed only on evidence the caller cannot supply: a
        # headword in the shipped definition pack or a picture name. Any other
        # text a learner puts in this URL is free text, which a build that
        # limits cloud speech to words keeps on the device.
        audio = await self.speak(word, source="word" if self._known_word(word) else None)
        if not audio:
            return {
                "word": word,
                "status": "tts_unavailable",
                "error": "no speak provider available",
            }
        filename = Path(str(audio)).name
        return {
            "word": word,
            "audio_url": f"/dictionary/api/audio/{filename}",
            "status": "ok",
        }
    except Exception as e:
        if getattr(e, "keep_local", False):
            # speech_guard kept it off the online voice (not a known word;
            # personal-looking text; switched off) and no on-device voice exists.
            from emptyos.capabilities.speech_guard import learner_message

            return {"word": word, "status": "tts_local_only",
                    "error": learner_message(getattr(e, "code", ""))}
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
            f'Word: "{word}". Give {count} example sentences, varying '
            f"difficulty from easy to advanced.",
            system=DICTIONARY_EXAMPLES_SYSTEM,
            domain="text",
            temperature=0.7,
        )
        examples = parse_llm_json(
            response,
            fallback=[
                f"The {word} was remarkable.",
                f"She described it as {word}.",
                f"This is a {word} example.",
            ],
        )
        return {"word": word, "examples": examples}
    except Exception:
        return {
            "word": word,
            "examples": [
                f"The {word} was remarkable.",
                f"She described it as {word}.",
                f"This is a {word} example.",
            ],
        }
