"""dictionary — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (lookup/pronounce/vocab) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.

**Import tier.** `vocab_schema` is tier 0 (imports nothing local); this module is
tier 1 and may import from it; every other helper is tier 2 and imports from
both. So `vocab_schema` must never import from here — that is the one edge that
would close a cycle, and it is not obvious from either file on its own.
"""

from __future__ import annotations

import re

from .vocab_schema import parse_senses


DEFAULT_DEFAULT_DICT_FOLDER = "30_Resources/Learning/Dictionary"

DICTIONARY_LOOKUP_SYSTEM = (
    "You are a precise dictionary assistant. Given a single English word, "
    "return a structured definition as JSON only.\n\n"
    "Required schema (all keys present, strings empty if unknown):\n"
    '{"word": str, "phonetic": str (IPA), "part_of_speech": str, '
    '"definition": str (one sentence), "example": str (one natural sentence), '
    '"synonyms": [str], "antonyms": [str], "chinese": str (simplified, '
    'one or two characters when possible), "etymology": str (one short '
    'sentence; empty when unknown), "usage_notes": str (one short sentence)}\n\n'
    "Do NOT:\n"
    "- Wrap the JSON in markdown fences or any prose.\n"
    "- Invent etymologies you don't have evidence for — leave empty instead.\n"
    "- Translate into languages other than Chinese unless asked.\n"
    "- Pad usage_notes when the word is unremarkable; empty is fine.\n"
    "- List antonyms a word does not have. Only a true opposite in meaning "
    "counts; most function words (and, the, a, but, because, that, he) have "
    "none, so give []. A contrasting or alternative word (and/or, a/the, "
    "this/that) is not an antonym.\n"
    "- Return more than one sense; pick the most common.\n"
    "- Tag a surname, given name, place or brand as \"noun\"; its "
    "part_of_speech is \"proper noun\".\n"
    "- Invent a meaning for a string you do not recognise as an English "
    "word. Say so in definition and leave part_of_speech empty."
)

# The user message and temperature for a structured lookup. The definition-pack
# builder (scripts/build_definition_pack.py) reads these, so pack entries are
# generated from the same static request. A live answer can still differ: it
# goes through the daemon's provider chain and any language localization.
DICTIONARY_LOOKUP_USER = 'Define the English word "{word}".'
DICTIONARY_LOOKUP_TEMPERATURE = 0.3

DICTIONARY_BASIC_SYSTEM = (
    "You are a precise dictionary assistant. Define the given English word "
    "in one clear, plain sentence. Output only the sentence — no quotes, no "
    "labels, no preamble, no JSON."
)

DICTIONARY_WOTD_SYSTEM = (
    "You are a curator of vocabulary for a daily learner. Pick one "
    "interesting, uncommon-but-useful English word and return it as JSON.\n\n"
    "Required schema:\n"
    '{"word": str, "phonetic": str (IPA), "definition": str (one sentence), '
    '"example": str (one natural sentence using the word), '
    '"chinese": str, "fun_fact": str (one short fact about origin or usage)}\n\n'
    "Do NOT:\n"
    "- Pick the same word twice in a row — vary across sessions.\n"
    "- Pick obscure technical jargon a general learner would never use.\n"
    "- Pick taboo, slur, or political-loaded words.\n"
    "- Wrap the JSON in markdown fences or any prose."
)

DICTIONARY_CONTEXT_SYSTEM = (
    "You are a precise dictionary assistant. Explain how a word is used "
    "in a given context, returning JSON only.\n\n"
    "Required schema:\n"
    '{"word": str, "meaning_in_context": str (one sentence — the sense '
    'actually used in the supplied context), "general_definition": str '
    '(one sentence — the broader dictionary sense), "chinese": str, '
    '"usage_note": str (one short sentence; empty when unremarkable)}\n\n'
    "Do NOT:\n"
    "- Wrap the JSON in markdown fences or any prose.\n"
    "- Repeat the context back to the user.\n"
    "- Return multiple senses — pick the one matching the context.\n"
    "- Speculate when the context is ambiguous; say so in usage_note instead."
)

# The reader's CEFR band, when nothing better is known. Both the reading bar and the
# harvest bar used to hardcode a band — "a B2-C1 learner", "an advanced (C1) reader" —
# which asserted a level for a reader nobody had asked. They read the reader's own
# setting now, and this is only the fallback.
DEFAULT_CEFR_BAND = "B2-C1"
# The most words one screen may ever carry, however hard it is. An interruption
# budget, not a target: see `_cap_for` in reading.py, which scales it down on a short
# screen so a 24-word chunk cannot end up a fifth highlighted.
DEFAULT_ITEM_CAP = 6

VOCAB_HARVEST_SYSTEM = (
    "You harvest vocabulary worth learning from a passage, for a non-native English "
    "reader at <LEVEL>. Return a JSON array of objects only.\n\n"
    "Each object: "
    '{"word": str (lemma / base form, lowercase unless a proper adjective), '
    '"part_of_speech": str, "definition": str (one plain sentence), '
    '"chinese": str (simplified, short), '
    '"sentence": str (the clause from the passage where it appeared, lightly '
    "trimmed)}\n\n"
    "Pick ONLY words that are genuinely above a ~6k-word reader: mid-to-low "
    "frequency, useful general or academic register.\n\n"
    "Do NOT include:\n"
    "- Common words a B2 learner already knows (system, however, develop, important).\n"
    "- Proper nouns, brand/product names, people, places.\n"
    "- Narrow technical jargon, code identifiers, file names, acronyms.\n"
    "- More than 8 words; fewer is fine. Return [] if nothing qualifies.\n"
    "- Any prose or markdown fences around the JSON."
)

VOCAB_FEED_SYSTEM = (
    "You curate a small daily vocabulary feed for an advanced (C1) non-native "
    "English speaker who wants to grow recognition breadth toward C2 — the "
    "academic, literary, and idiomatic register he meets in essays and good "
    "prose, NOT technical or software vocabulary. Return a JSON array of "
    "objects only.\n\n"
    "Each object: "
    '{"word": str, "phonetic": str (IPA), "part_of_speech": str, '
    '"definition": str (one sentence), "example": str (one natural sentence), '
    '"chinese": str (simplified, short), '
    '"usage_notes": str (one short sentence — a collocation, a confusable, or '
    "empty when unremarkable)}\n\n"
    "Do NOT:\n"
    "- Repeat any word in the supplied exclusion list.\n"
    "- Pick the tired 'impressive word' clichés (ephemeral, perspicacious, "
    "ubiquitous, serendipity, plethora, myriad, eloquent).\n"
    "- Pick technical / software jargon — he already commands that register.\n"
    "- Pick words so rare they never recur; favour words that earn their keep.\n"
    "- Wrap the JSON in markdown fences or any prose."
)

VOCAB_PRODUCTION_SYSTEM = (
    "You are a sharp but encouraging writing coach checking whether a learner "
    "actively and correctly used a set of target words in a short piece of "
    "writing. Return JSON only.\n\n"
    "Required schema:\n"
    '{"items": [{"word": str, "used": bool, "correct": bool, '
    '"note": str (one sentence — if wrong/awkward, the precise fix with a '
    "better collocation or register; if good, a brief confirmation)}], "
    '"overall": str (one or two sentences on register and naturalness)}\n\n'
    "Judge real usage, not mere presence — a word shoehorned in unnaturally is "
    "used=true, correct=false. Be specific about collocation and register.\n\n"
    "Do NOT:\n"
    "- Rewrite the whole piece for them; point at the fix.\n"
    "- Be vague ('good job') — name what works or what to change.\n"
    "- Wrap the JSON in markdown fences or any prose."
)

DICTIONARY_EXAMPLES_SYSTEM = (
    "You are a precise dictionary assistant. Generate example sentences for "
    "a word, varying difficulty from easy to advanced. Return a JSON array "
    "of strings only — no object, no keys, just the array.\n\n"
    "Do NOT:\n"
    "- Wrap the JSON in markdown fences or prose.\n"
    "- Number the examples or prefix them with 'Example:'.\n"
    "- Use the word inside quotes — write natural prose.\n"
    "- Repeat the same syntactic structure across examples."
)

# A short, real gloss of "ratify" per native language, for the prompt example in
# `prompts.py`. `<GLOSS>` keeps that example a real word in the reader's own
# language — a placeholder there would teach the model to echo the placeholder.
NATIVE_GLOSS_SAMPLES = {
    "chinese": "批准",
    "japanese": "批准する",
    "korean": "비준하다",
    "spanish": "ratificar",
    "french": "ratifier",
    "german": "ratifizieren",
    "portuguese": "ratificar",
    "italian": "ratificare",
    "russian": "ратифицировать",
    "arabic": "يصادق",
    "hindi": "अनुमोदन करना",
    "vietnamese": "phê chuẩn",
}
DEFAULT_NATIVE_LANGUAGE = "Chinese"
DEFAULT_TARGET_LANGUAGE = "English"


def reading_prompt(
    template: str, native: str, target: str, *, level: str = "", cap: int = 0
) -> str:
    """Bind a reading prompt to the reader's languages, LEVEL and per-screen CAP.

    `level` is the CEFR band the reader's bar sits at, and `cap` is the most words
    this screen may carry. Both used to be string literals in the prompt — "a B2-C1
    learner", "3 to 6 words" — which is to say the layer asserted a level for a
    reader it had never asked, and a word count for a screen it had not seen.
    Templates without the placeholders are unaffected.
    """
    native = (native or DEFAULT_NATIVE_LANGUAGE).strip()
    target = (target or DEFAULT_TARGET_LANGUAGE).strip()
    gloss = NATIVE_GLOSS_SAMPLES.get(native.lower(), "")
    if not gloss:
        # Unknown language: show the gloss slot holding the target-language word
        # itself. Still a VALID example (the property that matters), and the rule
        # line below tells the model to write it in the reader's language.
        gloss = "ratify"
    return (
        template.replace("<TARGET>", target)
        .replace("<NATIVE>", native)
        .replace("<GLOSS>", gloss)
        .replace("<LEVEL>", level or DEFAULT_CEFR_BAND)
        .replace("<CAP>", str(cap or DEFAULT_ITEM_CAP))
    )


def definition_of(data: dict | None) -> str:
    """Best-effort definition for a word note: frontmatter, sense, else first prose line.

    Consumers: vocab.api_quiz (quiz stems), srs.srs_due (unified-review card backs).

    The sense lookup is not an optimisation. `save_word` writes definitions as
    `- **definition**: ...` bullets inside a `## Sense:` block, and the prose
    fallback does not skip list items — so without it this returned the literal
    string `- **definition**: to give formal consent`, bullet and asterisks
    included, into every quiz stem and review card.
    """
    if not data:
        return ""
    definition = (data.get("meta") or {}).get("definition", "") or ""
    if definition:
        return definition
    body = data.get("body") or ""
    for sense in parse_senses(body):
        if sense.get("definition"):
            return str(sense["definition"])
    for line in body.split("\n"):
        line = line.strip()
        if line and not line.startswith(("#", "**", ">", "-")):
            return line
    return body[:100]


# ── Video moments ─────────────────────────────────────────────────────────
#
# video-digest writes a `<stem>.transcript.json` sidecar next to every YouTube
# digest: `{"video_id": ..., "lines": [{"text", "start", "duration"}]}`. The
# vocabulary harvest reads the digest PROSE, so until these helpers existed the
# moment a word was actually said was known at harvest time and thrown away.
#
# The moment rides in the source URL (`...&t=93s`) rather than in a new
# frontmatter field: `save_word` already persists `source_url` to `source:` and
# into the sense's `met:` encounter line, so a timestamped URL flows through the
# existing schema untouched and stays clickable everywhere a source link renders.

_WORD_RE = re.compile(r"[a-z][a-z'\-]*")


def moment_index(lines) -> dict[str, dict]:
    """Map each word in a timestamped transcript to the FIRST moment it is said.

    `lines` is the native youtube-transcript-api shape — `[{"text", "start",
    "duration"}]`. Returns `{word_lower: {"t": int, "text": str}}`.

    First occurrence, not last: the learner is looking for where they met the
    word, and re-encounters later in the same video are the same meeting.
    """
    index: dict[str, dict] = {}
    if not isinstance(lines, list):
        return index
    for line in lines:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0, int(float(line.get("start") or 0)))
        except (TypeError, ValueError):
            continue
        for token in _WORD_RE.findall(text.lower()):
            token = token.strip("'-")
            if len(token) < 2 or token in index:
                continue
            index[token] = {"t": start, "text": text}
    return index


def moment_url(url: str, seconds) -> str:
    """Append a `t=<n>s` deep link to a video URL.

    Returns `url` unchanged when there is nothing to add or a timestamp is
    already present — re-stamping a URL that already carries one would produce
    two `t=` params and let the wrong one win.
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        secs = int(float(seconds))
    except (TypeError, ValueError):
        return url
    if secs <= 0 or re.search(r"[?&]t=", url):
        return url
    return f"{url}{'&' if '?' in url else '?'}t={secs}s"


# ── Word threads ──────────────────────────────────────────────────────────
#
# The lookup already asks for `topics` and `word_family` and `vocab.py` persists
# both to frontmatter — but nothing rendered them, and SRS shuffled new words at
# random, so a word family was guaranteed to be split across sessions. These
# helpers group and sequence over that existing data. No new capture.

_THREAD_KINDS = (("topics", "topic"), ("word_family", "family"))


def _thread_keys(meta: dict | None) -> list[tuple[str, str]]:
    """`(kind, key)` pairs a word note belongs to, from existing frontmatter."""
    keys: list[tuple[str, str]] = []
    if not isinstance(meta, dict):
        return keys
    for field, kind in _THREAD_KINDS:
        value = meta.get(field)
        if isinstance(value, str):
            value = [value]
        for item in value or []:
            item = str(item).strip().lower()
            if item:
                keys.append((kind, item))
    return keys


def group_threads(entries, *, min_size: int = 2) -> list[dict]:
    """Group word entries into threads by shared `topics` / `word_family`.

    `entries` is `[{"word": str, "meta": {...}}]`. Returns
    `[{"thread", "kind", "words": [...]}]`, largest first then alphabetical.

    A word may appear in several threads — that is the point of a network, and
    forcing each word into one bucket would be a taxonomy, not a thread. Threads
    below `min_size` are dropped: a "thread" of one is just a word.
    """
    buckets: dict[tuple[str, str], list[str]] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word") or "").strip()
        if not word:
            continue
        for key in _thread_keys(entry.get("meta")):
            buckets.setdefault(key, [])
            if word not in buckets[key]:
                buckets[key].append(word)
    threads = [
        {"thread": key, "kind": kind, "words": sorted(words)}
        for (kind, key), words in buckets.items()
        if len(words) >= min_size
    ]
    threads.sort(key=lambda t: (-len(t["words"]), t["thread"]))
    return threads


def thread_sequence(entries) -> list[str]:
    """Order words so thread-mates are adjacent, largest thread first.

    Replaces `random.shuffle` for new-word introduction. Words in no thread keep
    a stable alphabetical tail rather than being dropped — every word must still
    be reachable, threads only change the ORDER in which they arrive.
    """
    ordered: list[str] = []
    placed: set[str] = set()
    for thread in group_threads(entries):
        for word in thread["words"]:
            if word not in placed:
                placed.add(word)
                ordered.append(word)
    loose = sorted(
        str(e.get("word") or "").strip()
        for e in entries or []
        if isinstance(e, dict) and str(e.get("word") or "").strip() not in placed
    )
    return ordered + [w for w in loose if w]


def best_example(word: str, senses) -> str:
    """The strongest example sentence for a word, across its senses.

    Prefers one that actually contains the word — a card whose sentence does not
    show the word in use teaches nothing, and enrichment examples for OTHER
    senses are exactly the ones that miss it. Falls back to the first example
    available rather than to nothing.

    `senses` is the `vocab_schema.parse_senses` shape: `[{"examples": [...]}]`.
    """
    needle = (word or "").strip().lower()
    fallback = ""
    for sense in senses or []:
        if not isinstance(sense, dict):
            continue
        for example in sense.get("examples") or []:
            example = str(example or "").strip()
            if not example:
                continue
            if needle and needle in example.lower():
                return example
            fallback = fallback or example
    return fallback


def due_card_key(card: dict) -> tuple:
    """Sort key for cards already due — oldest-overdue first, then hardest.

    One definition, used by both `api_srs_deck` and the `srs_due` cross-app
    contract so the deck the UI paints and the deck `learn` pulls cannot disagree
    about what comes first.

    Three parts, and the order between them is the whole design:

    1. ``next_review`` — FSRS decides WHEN a card returns, and the reader's rating
       must never move that. A five-star word due today does not jump ahead of an
       unrated one overdue since April; letting it would be second-guessing the
       algorithm with a number nobody ever re-examines.
    2. ``-difficulty`` — among the cards already due today, meet the ones the
       reader has said are hard first, so a session cut short spends its attention
       where they asked for it.
    3. ``word`` — ties break alphabetically. Without it the deck reshuffles between
       loads, which reads as data loss to someone working through a list.
    """
    return (card.get("next_review") or "", -int(card.get("difficulty") or 0), card.get("word") or "")


def display_level(entry: dict) -> int:
    """A 0-7 progress figure for the UI's level dots, derived from FSRS
    stability rather than stored.

    The dots predate FSRS and were driven by ``level``, an index into a fixed
    interval ladder. That field is gone; this reproduces the same 0-7 feel from
    what FSRS actually knows, and is a *display* value only — nothing schedules
    off it, and it is never written back to srs.json.
    """
    try:
        st = entry.get("s")
        st = None if st is None else float(st)
    except (TypeError, ValueError):
        st = None
    if st is None:
        return 0
    for i, edge in enumerate((1.0, 3.0, 7.0, 14.0, 30.0, 60.0, 120.0), start=1):
        if st < edge:
            return i - 1
    return 7
