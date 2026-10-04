"""dictionary — the vocabulary note schema: sense-based, multilingual, accumulating.

Pure functions only — no `self`, no kernel access, no I/O. Imported by vocab.py
(the write path) and reading.py (the read path) so there is exactly one definition
of "what a saved word looks like" (P4 Atomic, CLAUDE.md rule 4).

## Why this is not a flat word→definition record

Checked against ISO 24613 (LMF), W3C OntoLex-Lemon, TEI Lex-0, and Kaikki /
wiktextract. They disagree about syntax and agree about structure:

    LexicalEntry ──has many──> Form   (lemma + inflections)
                 └─has many──> Sense  ──> definition, translation, example, level

**Definition, translation, example and CEFR belong to the SENSE, not the word.**
Cambridge's English Vocabulary Profile levels *per meaning*: `degree` is A2 for
temperature, B1 for a qualification, B2 for an amount, C2 in `a degree of`. So
"what level is `degree`" is not a question with an answer.

A flat `word: definition + gloss` schema therefore asserts a false dependency, and
the damage is concrete for reading-driven capture:

- you meet "she has a **degree** in physics" and the note says "degree = 度";
  the note is wrong *for the sentence that created it*
- meeting a second sense either **overwrites** the first or makes a duplicate file
- "do I know `bank`?" has no answer — riverbank and the financial institution are
  two memories with two forgetting curves, and one card tests only whichever you
  happened to save
- a translation without a sense is meaningless ("the German for `light`"), which is
  why wiktextract puts a `sense` key on every translation row

So: **one note per word, senses as a repeating list inside it.** That is the shape
every standard converges on. One-file-per-sense is NOT the fix (no standard does
it — it would fragment pronunciation/forms/etymology, which really are word-level).

## Where each layer lives, and why

Vault frontmatter is **flat-only** — the VaultIndex parser silently drops nested
maps and lists-of-dicts (CLAUDE.md § Development Gotchas). A `senses:` list of
objects would therefore vanish. So:

    frontmatter  = word-level facts + flat scalars/lists  (queryable)
    body         = the senses, as `## Sense:` sections     (human-readable, rich)

    ---
    tags:
      - vocabulary
    word: degree
    lemma: degree              # canonical form (OntoLex canonicalForm — exactly one)
    lang: en                   # TARGET language: the language the word IS
    part_of_speech: noun       # a spelling with two POS is TWO notes (as Kaikki does)
    entry_id: en/degree/noun   # stable anchor for SRS + dedupe
    ipa: /dɪˈɡriː/
    forms:                     # inflections, so a page hit on "degrees" resolves here
      - degrees
    sense_keys:                # flat list — the senses' slugs, so senses stay queryable
      - qualification
      - temperature
    gloss_zh: 学位             # DENORMALISED: the PRIMARY sense's gloss, for fast
    chinese: 学位              #   queries + legacy readers. Truth is per-sense, below.
    status: learning           # new | learning | known | ignored
    times_met: 3
    first_met: 2026-07-14
    last_met: 2026-07-20
    difficulty: 4              # the READER's 1-5 "how hard is this for me"; absent = unrated
    favorite: false
    created: 2026-07-14
    updated: 2026-07-20
    ---

    ## Sense: qualification
    - **level**: B1
    - **tags**: countable
    - **definition**: an academic award conferred by a university.
    - **zh**: 学位
    - **example**: She has a degree in physics.
    - **met**: 2026-07-14 — [source](https://…)

    ## Sense: temperature
    - **level**: A2
    - **definition**: a unit of measurement for temperature or angles.
    - **zh**: 度

    ## Notes
    (the reader's own — AI never rewrites this)

`gloss_zh` at word level is a deliberate **denormalisation**, not a relapse: it is
the primary sense's gloss, kept flat so `vault_query(gloss_es=…)` and the legacy
`chinese:` readers keep working. The per-sense translations are authoritative.

**SRS scheduling state is NOT here.** Interval/ease/due are high-frequency machine
bookkeeping → `data/` (CLAUDE.md § Storage: two domains). And when it is keyed, it
must be keyed by SENSE, never by filename — keying by file would re-introduce the
one-sense-per-word bug through the back door.

`difficulty` looks like schedule state and is not: it is a judgement the reader
typed, survives a reset, and is theirs to read in the note — so it is vault, by the
same test that puts `status` and `favorite` here. It is deliberately word-level
rather than per-sense: "this word will not stick" is how a reader actually
experiences it, and asking them to rate each meaning separately would go unanswered.
"""

from __future__ import annotations

import re

# Language name → ISO 639-1. Accepts the endonym and the English name, because the
# setting is free text and a reader may well type "中文".
LANGUAGE_CODES: dict[str, str] = {
    "chinese": "zh", "mandarin": "zh", "中文": "zh", "简体中文": "zh", "汉语": "zh",
    "english": "en", "英语": "en",
    "japanese": "ja", "日本語": "ja", "日语": "ja",
    "korean": "ko", "한국어": "ko",
    "spanish": "es", "español": "es",
    "french": "fr", "français": "fr",
    "german": "de", "deutsch": "de",
    "portuguese": "pt", "português": "pt",
    "italian": "it", "italiano": "it",
    "russian": "ru", "русский": "ru",
    "arabic": "ar", "العربية": "ar",
    "hindi": "hi", "हिन्दी": "hi",
    "vietnamese": "vi", "tiếng việt": "vi",
    "indonesian": "id", "thai": "th", "turkish": "tr", "dutch": "nl", "polish": "pl",
}

DEFAULT_TARGET_CODE = "en"
_CODE_RE = re.compile(r"^[a-z]{2,3}$")

STATUSES = ("new", "learning", "known", "ignored")
CEFR_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")

# `difficulty` is the READER's own 1-5 rating — "how hard is this word for ME" —
# and it is not the same axis as `level` or as the SRS ladder. CEFR is a claim
# about the language (`degree` is B1 for most people); the SRS level is derived
# from how a schedule has gone. Neither can express "I have looked this up four
# times and it still will not stick", which is the one thing the reader knows and
# the machine does not. 0 means unrated — absence, not "easy".
DIFFICULTY_MAX = 5
REGISTERS = ("neutral", "formal", "informal", "slang", "technical", "literary")

# Frontmatter fields that are flat lists of scalars (never lists of dicts — the
# parser cannot hold those; that is precisely why senses live in the body).
LIST_FIELDS = ("forms", "sense_keys", "synonyms", "antonyms", "word_family", "topics")

SENSE_HEADING = "## Sense: "


# ── languages ─────────────────────────────────────────────────────────────


def lang_code(name: str) -> str:
    """Language name → ISO 639-1 code. Already-a-code passes through.

    Unknown names fall back to a slug rather than to a fixed default —
    mislabelling a gloss as another language is worse than an odd-but-stable key,
    because translations are keyed by this and would silently collide.
    """
    raw = str(name or "").strip().lower()
    if not raw:
        return ""
    if _CODE_RE.match(raw) and raw in set(LANGUAGE_CODES.values()):
        return raw
    if raw in LANGUAGE_CODES:
        return LANGUAGE_CODES[raw]
    slug = re.sub(r"[^a-z]", "", raw)[:3]
    return slug or ""


def gloss_key(native: str) -> str:
    """`"Chinese"` → `"gloss_zh"` (the denormalised word-level convenience key)."""
    code = lang_code(native)
    return f"gloss_{code}" if code else ""


# ── senses ────────────────────────────────────────────────────────────────


def sense_key(label: str, definition: str = "") -> str:
    """A stable slug identifying one sense within a word.

    The model supplies a 1-2 word `sense_label` ("qualification", "temperature").
    Falling back to the definition's first words is deliberately crude — a wrong
    merge is worse than a duplicate sense, so when we cannot tell, we do not merge.
    """
    raw = str(label or "").strip().lower()
    if not raw:
        raw = " ".join(str(definition or "").strip().lower().split()[:3])
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug[:40]


def entry_id(word: str, part_of_speech: str, target: str) -> str:
    """`en/degree/noun` — the stable anchor SRS and dedupe hang off."""
    code = lang_code(target) or DEFAULT_TARGET_CODE
    pos = re.sub(r"[^a-z]+", "", str(part_of_speech or "").lower()) or "x"
    return f"{code}/{str(word or '').strip().lower()}/{pos}"


def render_sense(sense: dict) -> list[str]:
    """One `## Sense:` section. Translations are a line per language."""
    lines = [SENSE_HEADING + (sense.get("key") or "sense"), ""]
    if sense.get("level"):
        lines.append(f"- **level**: {sense['level']}")
    if sense.get("register"):
        lines.append(f"- **register**: {sense['register']}")
    tags = coerce_list(sense.get("tags"))
    if tags:
        lines.append(f"- **tags**: {', '.join(tags)}")
    if sense.get("definition"):
        lines.append(f"- **definition**: {sense['definition']}")
    for code, text in sorted((sense.get("translations") or {}).items()):
        if text:
            lines.append(f"- **{code}**: {text}")
    for example in sense.get("examples") or []:
        if example:
            lines.append(f"- **example**: {example}")
    for met in sense.get("met") or []:
        if met:
            lines.append(f"- **met**: {met}")
    lines.append("")
    return lines


def parse_senses(body: str) -> list[dict]:
    """Read the `## Sense:` sections back out of a note body.

    Fails soft — an unparseable bullet is skipped, never raised. A hand-edited note
    must stay readable to the app, and the reader's prose is not ours to police.
    """
    senses: list[dict] = []
    current: dict | None = None
    for line in str(body or "").split("\n"):
        if line.startswith(SENSE_HEADING):
            if current:
                senses.append(current)
            current = {
                "key": line[len(SENSE_HEADING):].strip(),
                "translations": {}, "examples": [], "met": [], "tags": [],
            }
            continue
        if current is None:
            continue
        if line.startswith("## "):        # a non-sense section ends the run
            senses.append(current)
            current = None
            continue
        match = re.match(r"\s*-\s+\*\*([^*]+)\*\*:\s*(.+?)\s*$", line)
        if not match:
            continue
        field, value = match.group(1).strip().lower(), match.group(2).strip()
        if field in ("level", "register", "definition"):
            current[field] = value
        elif field == "tags":
            current["tags"] = coerce_list(value)
        elif field == "example":
            current["examples"].append(value)
        elif field == "met":
            current["met"].append(value)
        elif _CODE_RE.match(field):       # a language code → a translation
            current["translations"][field] = value
    if current:
        senses.append(current)
    return senses


def merge_sense(senses: list[dict], incoming: dict) -> tuple[list[dict], bool]:
    """Fold one encountered sense into a word's sense list.

    Matching is by `key` only. A sense we cannot confidently match becomes a NEW
    sense rather than being merged into an existing one — inventing a merge would
    silently corrupt the meaning the reader actually met, which is the exact bug
    this whole module exists to prevent.

    Returns (senses, is_new).
    """
    key = incoming.get("key")
    if not key:
        return senses, False
    for sense in senses:
        if sense.get("key") != key:
            continue
        # Same sense met again: enrich, never replace. Their earlier example and
        # their other languages' translations survive.
        for field in ("level", "register", "definition"):
            if incoming.get(field) and not sense.get(field):
                sense[field] = incoming[field]
        sense["tags"] = coerce_list(coerce_list(sense.get("tags")) + coerce_list(incoming.get("tags")))
        sense.setdefault("translations", {}).update(
            {k: v for k, v in (incoming.get("translations") or {}).items() if v}
        )
        for field in ("examples", "met"):
            merged = list(sense.get(field) or [])
            for value in incoming.get(field) or []:
                if value and value not in merged:
                    merged.append(value)
            sense[field] = merged[:8]      # bounded: a word met 200 times is still readable
        return senses, False
    senses.append(incoming)
    return senses, True


def replace_senses(body: str, senses: list[dict]) -> str:
    """Swap the `## Sense:` sections for a fresh set, leaving everything else alone.

    Every other section — `## Notes`, `## Etymology`, whatever the reader added —
    is copied through verbatim. AI owns the sense blocks; the prose above and below
    is theirs (`.claude/rules/authorship-boundary.md`).
    """
    rendered: list[str] = []
    for sense in senses:
        rendered.extend(render_sense(sense))

    lines = str(body or "").split("\n")
    out: list[str] = []
    i = 0
    placed = False
    while i < len(lines):
        if lines[i].startswith(SENSE_HEADING):
            if not placed:
                out.extend(rendered)
                placed = True
            i += 1
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if not placed:
        if out and out[-1].strip():
            out.append("")
        out.extend(rendered)
    return "\n".join(out).rstrip() + "\n"


# ── frontmatter ───────────────────────────────────────────────────────────


_FALSEY = {"false", "no", "0", "off", "none", "null", ""}


def coerce_bool(value: object) -> bool:
    """A YAML bool that has been round-tripped through frontmatter is a STRING.

    `bool("false")` is True, so a plain `bool()` silently flips every `false:` field
    to true the first time a note is re-saved (CLAUDE.md § vault props are strings).
    """
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in _FALSEY


def coerce_difficulty(value: object) -> int:
    """A reader's 1-5 difficulty star rating. 0 (unrated) for anything unreadable.

    Clamps rather than raises: the value arrives from frontmatter (always a string
    once round-tripped), from an HTTP body, and from the browser extension, and a
    hand-edited `difficulty: 9` should read as "the hardest they can say" instead
    of poisoning every read of that note.
    """
    try:
        rating = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, min(DIFFICULTY_MAX, rating))


def coerce_list(value: object) -> list[str]:
    """Normalise a loose list field at the write boundary.

    Callers hand us a list, a comma string, or None for the same field (CLAUDE.md
    § Development Gotchas — coerce once at the write, not at every read site).
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def read_glosses(meta: dict) -> dict[str, str]:
    """Word-level glosses, keyed by language code. Includes the legacy `chinese:`."""
    out: dict[str, str] = {}
    for key, value in (meta or {}).items():
        if str(key).startswith("gloss_"):
            code = str(key)[len("gloss_"):].strip().lower()
            text = str(value or "").strip()
            if code and text:
                out[code] = text
    legacy = str((meta or {}).get("chinese") or "").strip()
    if legacy and "zh" not in out:
        out["zh"] = legacy
    return out


def pick_gloss(meta: dict, native: str, senses: list[dict] | None = None) -> tuple[str, str]:
    """The gloss to show this reader → (text, code).

    Prefers a sense-level translation in their language, then the word-level
    denormalised gloss, then ANY gloss the note has — a Spanish reader opening a
    word they saved while learning in Chinese should still see something, marked
    with the language it is actually in, rather than a blank.
    """
    want = lang_code(native)
    for sense in senses or []:
        text = (sense.get("translations") or {}).get(want)
        if text:
            return text, want
    glosses = read_glosses(meta)
    if want and glosses.get(want):
        return glosses[want], want
    if glosses:
        code = next(iter(glosses))
        return glosses[code], code
    return "", ""


def target_of(meta: dict, default: str = DEFAULT_TARGET_CODE) -> str:
    return lang_code(str((meta or {}).get("lang") or "")) or default


def note_filename(word: str, target: str, default_target: str = DEFAULT_TARGET_CODE) -> str:
    """`ratify` (en) → `ratify.md`; `pain` (fr) → `pain.fr.md`.

    The default target keeps the flat legacy name so existing notes resolve
    unchanged. Other targets are suffixed, because a word can be spelled the same
    in two languages ("pain" in English and French) and one file cannot hold both.
    """
    code = lang_code(target) or default_target
    return f"{str(word or '').strip()}.md" if code == default_target else \
        f"{str(word or '').strip()}.{code}.md"


def normalise_fields(meta: dict) -> dict:
    """Coerce a note's frontmatter into the schema. Fails soft, never raises."""
    out = dict(meta or {})
    for field in LIST_FIELDS:
        if field in out:
            items = coerce_list(out[field])
            if items:
                out[field] = items
            else:
                out.pop(field, None)

    status = str(out.get("status") or "").strip().lower()
    out["status"] = status if status in STATUSES else "new"

    for field, allowed, caser in (("level", CEFR_LEVELS, str.upper),
                                  ("register", REGISTERS, str.lower)):
        value = caser(str(out.get(field) or "").strip())
        if value in allowed:
            out[field] = value
        else:
            out.pop(field, None)

    if "times_met" in out:
        try:
            out["times_met"] = max(0, int(out["times_met"]))
        except (TypeError, ValueError):
            out.pop("times_met", None)

    # Unrated is stored as absence, not as `difficulty: 0` — so clearing a rating
    # leaves a note indistinguishable from one that was never rated, which is what
    # the reader means by clearing it.
    if "difficulty" in out:
        rating = coerce_difficulty(out["difficulty"])
        if rating:
            out["difficulty"] = rating
        else:
            out.pop("difficulty", None)

    out["favorite"] = coerce_bool(out.get("favorite"))
    return out


def merge_frontmatter(
    existing: dict, incoming: dict, *, native: str = "", gloss: str = "", met_on: str = "",
) -> dict:
    """Union the old note's frontmatter with a new encounter's word-level fields.

    Accumulating, never destructive:
    - list fields are unioned, not replaced
    - `times_met` increments, `last_met` advances — the encounter count is the
      honest answer to "is this word actually frequent *for me*"
    - `created` / `first_met` survive from the original note
    - blanks never overwrite something real
    - `status` is the READER's: a word they marked `known` or `ignored` does not
      silently revert because they scrolled past it again
    """
    out = dict(existing or {})
    for key, value in (incoming or {}).items():
        if key in LIST_FIELDS:
            items = coerce_list(coerce_list(out.get(key)) + coerce_list(value))
            if items:
                out[key] = items
            continue
        text = value if isinstance(value, bool) else str(value or "").strip()
        if text in ("", None):
            continue
        out[key] = value

    key = gloss_key(native)
    text = str(gloss or "").strip()
    if key and text and not out.get(key):
        # Word-level gloss is the PRIMARY sense's — the first one recorded. A later
        # sense's gloss does not overwrite it; per-sense translations hold the truth.
        out[key] = text
        if key == "gloss_zh":
            out["chinese"] = text

    if met_on:
        try:
            out["times_met"] = max(1, int(out.get("times_met") or 1)) + 1
        except (TypeError, ValueError):
            out["times_met"] = 2
        out["last_met"] = met_on
        out.setdefault("first_met", (existing or {}).get("first_met") or met_on)

    for keep in ("created", "first_met"):
        if (existing or {}).get(keep):
            out[keep] = existing[keep]
    status = str((existing or {}).get("status") or "").strip().lower()
    if status in ("known", "ignored"):
        out["status"] = status
    return normalise_fields(out)
