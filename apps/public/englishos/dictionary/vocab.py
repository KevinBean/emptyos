"""dictionary — vault vocab management routes — save / list / favorite / delete / quiz / export / frequency / addons.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: The vault-vocabulary CRUD + management surface: saving a word as markdown (with SRS init), listing the deck with SRS status, single-word detail, favorite toggle, delete (cascading to SRS + frequency), quiz generation, Anki-compatible export, lookup-frequency reporting, and the user-configured word-addons..

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._vault_words/_read_vault_word/_vault_as_lookup/_vault_dir (spine data layer); self._load_srs/_save_srs (srs); self._load_freq/_save_freq (frequency); self.write/self.emit/self.app_config.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import random
from datetime import date
from urllib.parse import quote
from emptyos.frontmatter import set_frontmatter_field
from emptyos.sdk import web_route
from emptyos.sdk.utils import csv_to_rows, read_upload_text, rows_to_csv, sniff_columns
from typing import TYPE_CHECKING

from .shared import definition_of, display_level, group_threads
from .vocab_schema import (
    DEFAULT_TARGET_CODE,
    coerce_difficulty,
    coerce_list,
    entry_id,
    gloss_key,
    lang_code,
    merge_frontmatter,
    merge_sense,
    normalise_fields,
    note_filename,
    parse_senses,
    render_sense,
    replace_senses,
    sense_key,
)


def _compose_note(meta: dict, body: str) -> str:
    """Frontmatter + body → the file on disk. One writer, so the two paths agree."""
    lines = ["---"]
    for key, value in meta.items():
        if key == "tags":
            continue  # written last, always block-style
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {v}" for v in value]
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            lines.append(f"{key}: {value}")
    lines += ["tags:", "  - vocabulary", "---", ""]
    return "\n".join(lines) + body.lstrip("\n")


def _encounter_line(day: str, sentence: str, source_url: str) -> str:
    """Where and when a SENSE was met. Rendered as the value of a `met:` bullet,
    so it carries no bullet of its own — `render_sense` supplies that."""
    parts = [day]
    if source_url:
        parts.append(f"[source]({source_url})")
    if sentence:
        parts.append(f'"{sentence.strip()}"')
    return " — ".join(parts)


async def _extend_word_note(
    self, *, path: str, word: str, existing: dict, native: str, gloss: str,
    incoming: dict, sense: dict, extra: list, today: str, enroll_srs: bool,
) -> dict:
    """Meeting a known word again: merge frontmatter, fold in the sense.

    Either the sense matches one already recorded (enrich it — keep their earlier
    example and their other languages) or it is a genuinely new meaning (append it).
    Neither path overwrites the word. Sections the reader wrote are copied through
    untouched — AI owns the sense blocks and nothing else
    (`.claude/rules/authorship-boundary.md`).

    The read-modify-write is serialised on the note itself (`note_lock`), not the
    app instance: the reading layer, the SRS, and the vocab loop all write here, and
    a lost update would silently drop a sense (CLAUDE.md § vault RMW races).
    """
    async with self.note_lock(path):
        senses, is_new = merge_sense(parse_senses(existing.get("body") or ""), sense)
        for other_sense in extra or []:
            senses, _ = merge_sense(senses, other_sense)
        merged = merge_frontmatter(
            existing.get("meta") or {}, incoming, native=native, gloss=gloss, met_on=today
        )
        merged.setdefault("word", word)
        merged["sense_keys"] = [s.get("key") for s in senses if s.get("key")]
        body = replace_senses(existing.get("body") or "", senses)
        await self.write(path, _compose_note(merged, body))
    if enroll_srs:
        srs = self._load_srs()
        if word not in srs:
            # No `level`/`streak`: FSRS seeds `s`/`d` on the first graded
            # review (sdk/srs.py — an entry with no `s` is a first exposure).
            srs[word] = {"review_count": 0, "next_review": today,
                         "last_reviewed": None}
            self._save_srs(srs)
    # Emits stay OUTSIDE the note lock so a handler can call back through the app
    # without deadlocking (CLAUDE.md § vault read-modify-write races).
    await self.emit("dictionary:word_saved", {"word": word, "again": True, "new_sense": is_new})
    return {"ok": True, "word": word, "path": path, "extended": True, "new_sense": is_new}

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ────────────────────────────────
#   save_word              = _vocab.save_word
#   api_save               = _vocab.api_save
#   api_vault              = _vocab.api_vault
#   api_threads            = _vocab.api_threads
#   api_vault_word         = _vocab.api_vault_word
#   api_favorite           = _vocab.api_favorite
#   set_word_difficulty    = _vocab.set_word_difficulty
#   api_difficulty         = _vocab.api_difficulty
#   difficulty_map         = _vocab.difficulty_map
#   api_frequency          = _vocab.api_frequency
#   api_delete_vault_word  = _vocab.api_delete_vault_word
#   api_quiz               = _vocab.api_quiz
#   api_export             = _vocab.api_export
#   api_vault_import_preview = _vocab.api_vault_import_preview
#   api_vault_import_confirm = _vocab.api_vault_import_confirm
#   api_word_addons        = _vocab.api_word_addons
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


async def save_word(
    self,
    *,
    word: str,
    definition: str = "",
    phonetic: str = "",
    part_of_speech: str = "",
    example: str = "",
    synonyms=None,
    antonyms=None,
    chinese: str = "",
    native: str = "",
    gloss: str = "",
    target: str = "",
    etymology: str = "",
    usage_notes: str = "",
    source_url: str = "",
    sentence: str = "",
    meaning_in_context: str = "",
    lemma: str = "",
    forms=None,
    collocations=None,
    word_family=None,
    topics=None,
    sense_label: str = "",
    sense_tags=None,
    extra_senses=None,
    level: str = "",
    register: str = "",
    status: str = "",
    difficulty: int = 0,
    favorite: bool = False,
    enroll_srs: bool = True,
) -> dict:
    """Persist a word as a vault markdown note and (optionally) enroll it in SRS.

    Shared persist path: the ``/api/save`` route, and the vocab-loop feed /
    harvest approvals reach it via ``self.save_word(...)`` (rule 4 forbids
    helper-to-helper imports, so the loop routes through the bound method).

    **A word has SENSES, and this writes one of them.** `degree` met as "a degree
    in physics" and `degree` met as "20 degrees" are two meanings with two
    definitions, two translations, and two forgetting curves — every lexical
    standard models them as a repeating substructure, and Cambridge's EVP even
    levels CEFR per meaning. So a save either **adds a new sense** to the note or
    **enriches the matching one**; it never overwrites the word.

    **Re-saving is additive, not destructive.** The old writer rebuilt the note
    from scratch, discarding the reader's own edits, their other languages, and
    every earlier encounter. Now: frontmatter merges, senses merge, and any section
    the reader wrote is copied through untouched. See `vocab_schema` for the shape
    and its invariants.

    `native` + `gloss` are the multilingual pair (`native="Spanish"`,
    `gloss="ratificar"`). `chinese=` is the legacy spelling and still works.
    """
    word = (word or "").strip()
    if not word:
        return {"error": "word required"}
    synonyms = coerce_list(synonyms)
    antonyms = coerce_list(antonyms)
    forms = coerce_list(forms)
    word_family = coerce_list(word_family)
    topics = coerce_list(topics)

    # Legacy callers pass chinese=; new ones pass native=/gloss=.
    if chinese and not gloss:
        native, gloss = native or "Chinese", chinese
    target_code = lang_code(target) or DEFAULT_TARGET_CODE
    native_code = lang_code(native)

    today = date.today().isoformat()
    path = f"{self._vault_dir()}/{note_filename(word, target_code)}"

    # The sense actually met. `meaning_in_context` is the example — it is the
    # sentence from the page, which is the strongest thing a flashcard can carry.
    sense = {
        "key": sense_key(sense_label, definition),
        "definition": definition or meaning_in_context,
        "level": level,
        "register": register,
        "tags": coerce_list(sense_tags),
        "translations": {native_code: gloss} if (native_code and gloss) else {},
        # The sentence from the page is the strongest example a flashcard can carry.
        # The model's own example is the fallback, not the other way round — and the
        # two are often the same string, so dedupe rather than list it twice.
        "examples": coerce_list([sentence, example])[:2],
        # Encounters hang off the SENSE — "I met this meaning here" — not off the
        # word. A word-level log could not say WHICH meaning you read.
        "met": [_encounter_line(today, sentence, source_url)],
    }

    # The word's OTHER meanings, from enrichment. They are recorded but carry no
    # `met` entry: the reader is learning the sense they actually READ, and a note
    # that silently promotes four meanings to equal standing misrepresents that.
    other = []
    for extra in extra_senses or []:
        extra_definition = str(extra.get("definition") or "").strip()
        if not extra_definition:
            continue
        extra_gloss = str(extra.get("native") or "").strip()
        other.append({
            "key": sense_key(extra.get("sense_label", ""), extra_definition),
            "definition": extra_definition,
            "level": str(extra.get("level") or ""),
            "register": str(extra.get("register") or ""),
            "tags": coerce_list(extra.get("tags")),
            "translations": {native_code: extra_gloss} if (native_code and extra_gloss) else {},
            "examples": [e for e in [extra.get("example")] if e],
            "met": [],
        })

    # Word-level facts only. Blanks are dropped by the merge, so a thin caller (the
    # reading card knows word/pos/gloss and nothing else) can never wipe a rich note
    # the dictionary's own enrichment built up.
    fields = {
        "lemma": lemma or word,
        "phonetic": phonetic,
        "part_of_speech": part_of_speech,
        "entry_id": entry_id(word, part_of_speech, target_code),
        "forms": forms,
        "synonyms": synonyms,
        "antonyms": antonyms,
        "word_family": word_family,
        "topics": topics,
        "status": status,
        "difficulty": difficulty,
        "source": source_url,
        "updated": today,
    }

    existing = await self._read_vault_word(word)
    if existing:
        return await _extend_word_note(
            self, path=path, word=word, existing=existing, native=native, gloss=gloss,
            incoming=fields, sense=sense, extra=other, today=today,
            enroll_srs=enroll_srs,
        )

    meta = normalise_fields({
        **{k: v for k, v in fields.items() if v not in ("", 0, [], None)},
        "word": word,
        "lang": target_code,
        "sense_keys": [sense["key"]] + [o["key"] for o in other],
        "favorite": favorite,
        "status": status or "new",
        "times_met": 1,
        "first_met": today,
        "last_met": today,
        "created": today,
    })
    if gloss and gloss_key(native):
        # Denormalised: the PRIMARY sense's gloss, kept flat so frontmatter queries
        # and the legacy `chinese:` readers keep working. Per-sense is the truth.
        meta[gloss_key(native)] = gloss
        if gloss_key(native) == "gloss_zh":
            meta["chinese"] = gloss

    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {v}" for v in value]
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            lines.append(f"{key}: {value}")
    # Block-style tags — inline arrays are a smell (CLAUDE.md § Development Gotchas).
    lines += ["tags:", "  - vocabulary", "---", "", f"# {word}", ""]

    if phonetic:
        lines += [f"**Pronunciation**: {phonetic}", ""]

    lines += render_sense(sense)
    for extra in other:
        lines += render_sense(extra)

    if collocations:
        lines += ["## Collocations", ""] + [f"- {c}" for c in coerce_list(collocations)] + [""]
    if etymology:
        lines += ["## Etymology", "", etymology, ""]
    if usage_notes:
        lines += ["## Usage Notes", "", usage_notes, ""]
    if synonyms:
        lines += ["## Synonyms", "", ", ".join(synonyms), ""]
    if antonyms:
        lines += ["## Antonyms", "", ", ".join(antonyms), ""]

    # "抓词现场·一键回跳" — the sentence + page live on the SENSE (its `met:` line),
    # not in a word-level log. A log at word level could not say which MEANING was
    # met, which is the whole point of recording the encounter.
    content = "\n".join(lines)
    await self.write(path, content)

    if enroll_srs:
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


@web_route("POST", "/api/save")
async def api_save(self, request):
    """Save a word to vault as markdown (+ SRS enroll)."""
    body = await request.json()
    word = (body.get("word") or "").strip()
    if not word:
        return {"error": "word required"}
    return await self.save_word(
        word=word,
        definition=body.get("definition", ""),
        phonetic=body.get("phonetic", ""),
        part_of_speech=body.get("part_of_speech", ""),
        example=body.get("example", ""),
        synonyms=body.get("synonyms", []),
        antonyms=body.get("antonyms", []),
        chinese=body.get("chinese", ""),
        native=body.get("native", ""),
        gloss=body.get("gloss", ""),
        target=body.get("target", ""),
        sense_label=body.get("sense_label", ""),
        sense_tags=body.get("sense_tags", []),
        level=body.get("level", ""),
        register=body.get("register", ""),
        meaning_in_context=body.get("meaning_in_context", ""),
        lemma=body.get("lemma", ""),
        forms=body.get("forms", []),
        collocations=body.get("collocations", []),
        word_family=body.get("word_family", []),
        topics=body.get("topics", []),
        etymology=body.get("etymology", ""),
        usage_notes=body.get("usage_notes", ""),
        source_url=body.get("source_url", ""),
        sentence=body.get("sentence", ""),
        favorite=body.get("favorite", False),
    )


@web_route("GET", "/api/vault")
async def api_vault(self, request):
    """List all saved vault words with SRS status and the reader's difficulty rating.

    `?min_difficulty=N` narrows to words rated N stars or harder — the "just show me
    the ones I keep failing" cut. `?sort=difficulty` orders hardest first (ties broken
    alphabetically, so the list is stable between loads rather than reshuffling).
    """
    words = await self._vault_words()
    srs = self._load_srs()
    today = date.today().isoformat()
    result = []
    for w in words:
        entry = srs.get(w, {})
        result.append(
            {
                "word": w,
                "level": display_level(entry),
                "streak": entry.get("streak", 0),
                "next_review": entry.get("next_review", today),
                "favorite": False,     # both enriched from the note below
                "difficulty": 0,
            }
        )
    # One read per word, which this route already paid for `favorite`. Not routed
    # through `difficulty_map` on purpose: that reads the VaultIndex, which only
    # holds notes carrying `tags: [vocabulary]`, and this list's source of truth is
    # the folder — a legacy note missing the tag must still appear in the deck.
    for item in result:
        meta = (await self._read_vault_word(item["word"]) or {}).get("meta") or {}
        if meta.get("favorite") == "true":
            item["favorite"] = True
        item["difficulty"] = coerce_difficulty(meta.get("difficulty"))

    floor = coerce_difficulty(request.query_params.get("min_difficulty", 0))
    if floor:
        result = [item for item in result if item["difficulty"] >= floor]
    if request.query_params.get("sort") == "difficulty":
        result.sort(key=lambda item: (-item["difficulty"], item["word"].lower()))
    return {"words": result, "total": len(result)}


@web_route("GET", "/api/threads")
async def api_threads(self, request):
    """Group the deck into threads by shared `topics` / `word_family`.

    Pure read over frontmatter the lookup has been capturing and `save_word` has
    been persisting all along — a word already knows its topics and its family,
    and nothing ever showed them. Served from the in-memory VaultIndex, so this
    reads no files.
    """
    if not self._threads_on():
        return {"enabled": False, "threads": []}
    entries = [
        {"word": name, "meta": props}
        for row in (self.vault_query(tags=["vocabulary"]) or [])
        for props in [row.get("properties") or {}]
        for name in [str(props.get("word") or row.get("name") or "").strip()]
        if name
    ]
    threads = group_threads(entries)
    return {
        "enabled": True,
        "threads": threads,
        "total": len(threads),
        "threaded_words": len({w for t in threads for w in t["words"]}),
        "deck_size": len(entries),
    }


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
        "srs": {**entry, "level": display_level(entry)},
        "difficulty": coerce_difficulty(data["meta"].get("difficulty")),
        "definition": parsed.get("definition", ""),
        "example": parsed.get("example", ""),
        "chinese": parsed.get("chinese", ""),
        "phonetic": parsed.get("phonetic", ""),
        "part_of_speech": parsed.get("part_of_speech", ""),
        "synonyms": parsed.get("synonyms", []),
        "antonyms": parsed.get("antonyms", []),
        "etymology": parsed.get("etymology", ""),
        "usage_notes": parsed.get("usage_notes", ""),
        "source": data["meta"].get("source", ""),
    }


@web_route("POST", "/api/favorite")
async def api_favorite(self, request):
    """Toggle favorite status for a word."""
    body = await request.json()
    word = (body.get("word") or "").strip()
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


# ── Difficulty (the reader's own 1-5 star rating) ─────────────────────────
#
# The moment this exists for: you look a word up, read the definition, and still
# do not know it. Nothing in the system could record that. The SRS ladder only
# knows how a schedule has gone, and CEFR `level` is a claim about the language,
# not about you — so "I have met this four times and it still will not stick" had
# nowhere to live. It lives in the note, because it is a judgement the reader made
# and will want to read back (`.claude/rules/authorship-boundary.md` — theirs, not
# ours: nothing here ever rates a word on their behalf).


async def set_word_difficulty(self, word: str, difficulty=0, *, bump: bool = False) -> dict:
    """Write one word's difficulty rating. The single write path for the field.

    `bump=True` adds `difficulty` to the current rating instead of replacing it,
    for a caller that means "one harder than it was" rather than naming a number.
    Both clamp to 0..5, so a word cannot escape the scale. Reachable only through
    `POST /api/difficulty` with `bump: true` — **no UI uses it**, deliberately: the
    reading layer's "Still hard" verdict used to, and rating a word on the reader's
    behalf from a button that sits beside the star row is what this field's whole
    contract (the section comment above) rules out. Don't wire it back into a verdict.

    Serialised on the note (`note_lock`), not the app instance: the reading layer,
    the review card, and the vocabulary page all write here, and this is a
    read-modify-write over a file every one of them also writes senses into
    (CLAUDE.md § vault read-modify-write races).
    """
    word = (word or "").strip()
    if not word:
        return {"error": "word required"}
    path = f"{self._vault_dir()}/{word}.md"
    async with self.note_lock(path):
        data = await self._read_vault_word(word)
        if not data:
            return {"error": f"Word '{word}' not found in vault"}
        current = coerce_difficulty((data["meta"] or {}).get("difficulty"))
        rating = coerce_difficulty(current + coerce_difficulty(difficulty)) if bump \
            else coerce_difficulty(difficulty)
        if rating != current:
            # Rewrite the frontmatter field in place rather than recomposing the
            # note: everything else in the file — their prose, their senses, their
            # other languages — is none of this route's business.
            await self.write(path, set_frontmatter_field(data["raw"], "difficulty", str(rating)))
    if rating != current:
        await self.emit("dictionary:difficulty_set", {"word": word, "difficulty": rating})
    return {"ok": True, "word": word, "difficulty": rating, "was": current}


@web_route("POST", "/api/difficulty")
async def api_difficulty(self, request):
    """Rate how hard a saved word is for this reader. 0 clears the rating."""
    body = await request.json()
    return await self.set_word_difficulty(
        (body.get("word") or "").strip(),
        body.get("difficulty", 0),
        bump=bool(body.get("bump")),
    )


def difficulty_map(self) -> dict[str, int]:
    """`{word: 1-5}` for every rated word, from the in-memory VaultIndex.

    Read through `vault_query` rather than by opening notes: the callers are
    ordering paths (the review deck, the stats roll-up) that run over the whole
    deck, and a file read per word to learn one integer is what makes a feature
    like this quietly expensive. Unrated words are absent, so callers `.get(w, 0)`.

    **The index only holds notes carrying `tags: [vocabulary]`**, which every note
    `_compose_note` writes does — but `api_vault` lists the FOLDER, so a note that
    somehow lacks the tag appears in the deck and is invisible here. It then sorts
    as unrated and goes uncounted in the stats. That is the acceptable direction:
    the rating is a tie-break in an order and a number on a card, so degrading to
    "unrated" costs a position, never a word. Do NOT make a correctness decision
    depend on this map.
    """
    out: dict[str, int] = {}
    for row in self.vault_query(tags=["vocabulary"]) or []:
        props = row.get("properties") or {}
        name = str(props.get("word") or row.get("name") or "").strip()
        rating = coerce_difficulty(props.get("difficulty"))
        if name and rating:
            out[name] = rating
    return out


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
        definition = definition_of(data)

        # Generate wrong options
        others = [x for x in vault_words if x != w]
        wrong = random.sample(others, min(3, len(others)))
        options = [w] + wrong
        random.shuffle(options)

        questions.append(
            {
                "definition": definition,
                "chinese": data["meta"].get("chinese", ""),
                "options": options,
                "answer": w,
            }
        )

    return {"questions": questions, "total": len(questions)}


@web_route("GET", "/api/export")
async def api_export(self, request):
    """Export vocabulary — JSON (labelled "anki-compatible"), or with
    ``?format=csv`` a genuinely importable Anki CSV (front/back/tags).

    Flagged in gap analysis (dictionary-apkg-export): the JSON export was
    labelled "anki-compatible" but Anki imports ``.apkg`` or CSV, not
    arbitrary JSON — nothing could actually round-trip into Anki.

    Fixed along the way: the "back" field read raw frontmatter
    (``meta["definition"]``), which no current-format note carries — sense
    data lives in the body via ``## Sense:`` blocks, and `save_word`'s
    `fields` dict has no `definition` key. Every export's "back" field was
    silently falling through to ``data["body"][:200]`` — raw markdown
    headers and ``- **definition**:`` bullets, not a definition. Same root
    cause as dictionary-empty-reveal / dictionary-definition-markdown-leak
    (20d8dd9c2), in a route that fix never touched — switched to the
    shared `definition_of(data)` helper both of those already use.
    """
    vault_words = await self._vault_words()
    cards = []
    for w in vault_words:
        data = await self._read_vault_word(w)
        if data:
            cards.append(
                {
                    "front": w,
                    "back": definition_of(data),
                    "chinese": data["meta"].get("chinese", ""),
                    "phonetic": data["meta"].get("phonetic", ""),
                    "tags": data["meta"].get("tags", ""),
                }
            )
    if (request.query_params.get("format") or "").strip().lower() == "csv":
        from starlette.responses import Response

        csv_rows = [
            {"front": c["front"], "back": c["back"],
             "tags": " ".join(coerce_list(c.get("tags")))}
            for c in cards
        ]
        return Response(
            rows_to_csv(csv_rows, ["front", "back", "tags"]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="vocab.csv"'},
        )
    return {"cards": cards, "total": len(cards), "format": "anki-compatible"}


# ── CSV import (dictionary-word-list-import) ────────────────────────
MAX_VOCAB_IMPORT_BYTES = 2 * 1024 * 1024  # a word-list CSV is never 2 MB
MAX_VOCAB_IMPORT_ROWS = 500  # a sane ceiling on one import

# Deliberately overlaps this app's own Anki CSV export (front/back/tags),
# so a previously-exported deck round-trips back in.
_VOCAB_IMPORT_ALIASES: dict[str, list[str]] = {
    "word": ["word", "front", "term", "vocab", "vocabulary"],
    "definition": ["definition", "back", "meaning", "translation"],
    "chinese": ["chinese", "gloss", "native"],
    "phonetic": ["phonetic", "ipa", "pronunciation"],
    "part_of_speech": ["part of speech", "pos", "part_of_speech"],
    "example": ["example", "sentence", "usage"],
}


def _parse_vocab_import_csv(csv_text: str) -> dict:
    """Parse a word-list CSV -> {rows, columns, mapping, warnings, skipped}.
    A row with no word is dropped (counted in `skipped`) — there's nothing
    to import without one."""
    raw_rows = csv_to_rows(csv_text)
    if not raw_rows:
        return {"rows": [], "columns": [], "mapping": {},
                "warnings": ["no rows found in file"], "skipped": 0}

    columns = list(raw_rows[0].keys())
    mapping = sniff_columns(columns, _VOCAB_IMPORT_ALIASES)
    warnings = []
    if "word" not in mapping:
        warnings.append("could not find a word column — every row will be skipped")

    out_rows = []
    skipped = 0
    for raw in raw_rows:
        word = str(raw.get(mapping.get("word", ""), "") or "").strip()
        if not word:
            skipped += 1
            continue
        out_rows.append({
            "word": word,
            "definition": str(raw.get(mapping.get("definition", ""), "") or "").strip(),
            "chinese": str(raw.get(mapping.get("chinese", ""), "") or "").strip(),
            "phonetic": str(raw.get(mapping.get("phonetic", ""), "") or "").strip(),
            "part_of_speech": str(raw.get(mapping.get("part_of_speech", ""), "") or "").strip(),
            "example": str(raw.get(mapping.get("example", ""), "") or "").strip(),
        })
    if len(out_rows) > MAX_VOCAB_IMPORT_ROWS:
        warnings.append(f"file has {len(out_rows)} rows — only the first {MAX_VOCAB_IMPORT_ROWS} will be imported")
        out_rows = out_rows[:MAX_VOCAB_IMPORT_ROWS]
    return {"rows": out_rows, "columns": columns, "mapping": mapping,
            "warnings": warnings, "skipped": skipped}


@web_route("POST", "/api/vault/import/preview")
async def api_vault_import_preview(self, request):
    """Parse a word-list CSV and flag words already in the deck. Writes
    nothing.

    Flagged in gap analysis (dictionary-word-list-import): the deck was
    one-way — words only entered by lookup or harvest. Follows the same
    propose->preview->confirm shape as expense/requirements import
    (`.claude/rules/proposed-action.md`, impact-shaped). Column vocabulary
    deliberately overlaps this app's own Anki CSV export (front/back/tags)
    so a previously-exported deck round-trips back in.
    """
    csv_text, err = await read_upload_text(request, max_bytes=MAX_VOCAB_IMPORT_BYTES)
    if err:
        return {"error": err}
    parsed = _parse_vocab_import_csv(csv_text)
    rows = parsed["rows"]
    if not rows:
        return {"error": "no importable words found", "warnings": parsed["warnings"],
                "columns": parsed["columns"], "mapping": parsed["mapping"]}

    existing = set(await self._vault_words())
    out_rows = []
    new_count = 0
    for r in rows:
        already = r["word"] in existing
        if not already:
            new_count += 1
        out_rows.append({**r, "already_in_deck": already})

    return {
        "rows": out_rows,
        "columns": parsed["columns"],
        "mapping": parsed["mapping"],
        "warnings": parsed["warnings"],
        "summary": {
            "total": len(out_rows),
            "new": new_count,
            "already_in_deck": len(out_rows) - new_count,
            "skipped": parsed["skipped"],
        },
    }


@web_route("POST", "/api/vault/import/confirm")
async def api_vault_import_confirm(self, request):
    """Write the reviewed rows through the existing `save_word()` verb —
    the same SRS-init + sense-merge path a manual save uses, so an
    imported word behaves exactly like a looked-up one. Body:
    {rows: [{word, definition, chinese, phonetic, part_of_speech, example}]}.

    Re-saving an already-present word is safe (`save_word` enriches
    rather than overwrites), so there's no separate duplicate-skip logic
    here — unlike a one-shot record import, a word deck is meant to
    accumulate.
    """
    body = await request.json()
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return {"error": "rows required"}
    if len(rows) > MAX_VOCAB_IMPORT_ROWS:
        return {"error": "too many rows"}

    imported = 0
    skipped = 0
    for r in rows:
        if not isinstance(r, dict):
            skipped += 1
            continue
        word = str(r.get("word", "")).strip()
        if not word:
            skipped += 1
            continue
        res = await self.save_word(
            word=word,
            definition=str(r.get("definition", "")).strip(),
            chinese=str(r.get("chinese", "")).strip(),
            phonetic=str(r.get("phonetic", "")).strip(),
            part_of_speech=str(r.get("part_of_speech", "")).strip(),
            example=str(r.get("example", "")).strip(),
            enroll_srs=True,
        )
        if res.get("ok"):
            imported += 1
        else:
            skipped += 1

    return {"ok": True, "imported": imported, "skipped": skipped}


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
        addons.append(
            {
                "id": item.get("id") or item.get("label") or "addon",
                "label": item.get("label") or item.get("id") or "Open",
                "icon": item.get("icon") or "",
                "url": tmpl.replace("{word}", quote(word, safe="")),
            }
        )
    return {"addons": addons}
