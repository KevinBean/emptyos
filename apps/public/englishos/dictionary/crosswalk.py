"""dictionary — the vowel↔spelling crosswalk: which letters spell which vowel.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the 16 General American vowel phonemes plus 5 r-coloured
sequences, the 151 hand-authored sound↔spelling correspondences between them
and 71 graphemes, the pure graph builder that joins those against the learner's
own per-phone error counts, the two /api routes (crosswalk, crosswalk/kb), and
the hub panel. Source of truth for *how English writes a vowel* — the
orthographic complement to pronounce.py, which owns *how the learner says one*.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self._load_weak_phones (pronounce.py) for the error
join; self.vault_create_note for the KB export; self.emit for
dictionary:crosswalk_published.
Do not import from ``.app`` (it imports us, which would cycle).

The join key is ARPABET, because that is what weak-phones.json stores. One
honest wrinkle rides along: ARPABET has no separate symbol for schwa, so both
/ə/ COMMA and /ʌ/ STRUT map to ``AH`` and the error data cannot tell them
apart. Both carry ``arpa_shared`` so the surface can say so rather than
silently double-count a single number as two findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import DictionaryApp  # noqa: F401 — for type hints only


# ─── Bind to DictionaryApp class as ────────────────────────────────
#   api_crosswalk           = _crosswalk.api_crosswalk
#   api_crosswalk_kb        = _crosswalk.api_crosswalk_kb
#   panel_vowel_crosswalk   = _crosswalk.panel_vowel_crosswalk
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


# ── The sound side ────────────────────────────────────────────────
# x = backness (0 front → 1 back), y = height (0 close → 1 open): the two
# axes of the IPA vowel quadrilateral, so a renderer can lay the phonemes out
# as the chart they actually belong on. Diphthongs sit at their nucleus.
# ``group`` drives hue on every surface; it is information (backness), not
# decoration. ``arpa`` is None for the r-coloured entries — they are vowel+/r/
# sequences, not single ARPABET phones, so they never join the error store.

VOWELS: list[dict] = [
    {"id": "i",   "ipa": "i",  "key": "fleece",  "group": "front",   "arpa": "IY", "x": 0.05, "y": 0.04,
     "note": "The long, tense 'ee'. Tongue as high and far forward as it goes."},
    {"id": "ih",  "ipa": "ɪ",  "key": "kit",     "group": "front",   "arpa": "IH", "x": 0.22, "y": 0.22,
     "note": "Short and lax — not a shortened /i/. The tongue sits lower and more relaxed."},
    {"id": "ey",  "ipa": "eɪ", "key": "face",    "group": "front",   "arpa": "EY", "x": 0.13, "y": 0.40, "di": True,
     "note": "A glide: starts near /ɛ/ and closes toward /ɪ/."},
    {"id": "eh",  "ipa": "ɛ",  "key": "dress",   "group": "front",   "arpa": "EH", "x": 0.15, "y": 0.58,
     "note": "Mid-front. Jaw more open than /ɪ/, tongue still forward."},
    {"id": "ae",  "ipa": "æ",  "key": "trap",    "group": "front",   "arpa": "AE", "x": 0.11, "y": 0.86,
     "note": "Low and front. Only three spellings — the most regular vowel in English."},
    {"id": "er",  "ipa": "ɝ",  "key": "nurse",   "group": "central", "arpa": "ER", "x": 0.44, "y": 0.34,
     "note": "The r-coloured vowel. Unstressed it becomes /ɚ/ — the ending of teacher, dollar, doctor."},
    {"id": "sch", "ipa": "ə",  "key": "comma",   "group": "central", "arpa": "AH", "x": 0.53, "y": 0.49,
     "arpa_shared": True,
     "note": "Schwa. The most common vowel in English, and it only appears in unstressed syllables — which is why every vowel letter can spell it."},
    {"id": "ah",  "ipa": "ʌ",  "key": "strut",   "group": "central", "arpa": "AH", "x": 0.60, "y": 0.66,
     "arpa_shared": True,
     "note": "Schwa's stressed cousin. Same neighbourhood, but it takes the beat."},
    {"id": "ay",  "ipa": "aɪ", "key": "price",   "group": "central", "arpa": "AY", "x": 0.36, "y": 0.93, "di": True,
     "note": "Opens low and central, then closes toward /ɪ/."},
    {"id": "aw",  "ipa": "aʊ", "key": "mouth",   "group": "central", "arpa": "AW", "x": 0.50, "y": 0.90, "di": True,
     "note": "Same low start as /aɪ/, but glides back and rounds toward /ʊ/."},
    {"id": "uw",  "ipa": "u",  "key": "goose",   "group": "back",    "arpa": "UW", "x": 0.94, "y": 0.04,
     "note": "High, back and rounded — and the single worst-spelled sound in English."},
    {"id": "uh",  "ipa": "ʊ",  "key": "foot",    "group": "back",    "arpa": "UH", "x": 0.79, "y": 0.23,
     "note": "Short, lax and rounded. Only about a dozen common words use it."},
    {"id": "ow",  "ipa": "oʊ", "key": "goat",    "group": "back",    "arpa": "OW", "x": 0.87, "y": 0.41, "di": True,
     "note": "Starts mid-back and rounds up toward /ʊ/. British English starts it more centrally: /əʊ/."},
    {"id": "ao",  "ipa": "ɔ",  "key": "thought", "group": "back",    "arpa": "AO", "x": 0.93, "y": 0.63,
     "note": "Open-mid and rounded. Roughly 40% of American speakers have merged this with /ɑ/ — for them cot and caught sound identical."},
    {"id": "oy",  "ipa": "ɔɪ", "key": "choice",  "group": "back",    "arpa": "OY", "x": 0.78, "y": 0.75, "di": True,
     "note": "Starts at /ɔ/ and glides to /ɪ/. The most reliably spelled vowel in the language."},
    {"id": "aa",  "ipa": "ɑ",  "key": "lot",     "group": "back",    "arpa": "AA", "x": 0.94, "y": 0.94,
     "note": "Low, back, unrounded. In General American this covers both LOT and PALM — hot and father share a vowel."},
    # R-coloured — vowel + /r/, so no single ARPABET phone and no error join.
    {"id": "ir",  "ipa": "ɪr", "key": "near",    "group": "front", "arpa": None, "rhotic": True,
     "note": "Vowel plus /r/. Non-rhotic accents keep a centring glide here instead: /ɪə/."},
    {"id": "air", "ipa": "ɛr", "key": "square",  "group": "front", "arpa": None, "rhotic": True,
     "note": "Vowel plus /r/. The ⟨air⟩ ⟨are⟩ ⟨ear⟩ ⟨ere⟩ split is almost pure history."},
    {"id": "ar",  "ipa": "ɑr", "key": "start",   "group": "back",  "arpa": None, "rhotic": True,
     "note": "Vowel plus /r/. Nearly always ⟨ar⟩ — one of the more trustworthy patterns."},
    {"id": "or",  "ipa": "ɔr", "key": "north",   "group": "back",  "arpa": None, "rhotic": True,
     "note": "Vowel plus /r/. Absorbs both the NORTH and FORCE sets, which merged for most speakers."},
    {"id": "ur",  "ipa": "ʊr", "key": "cure",    "group": "back",  "arpa": None, "rhotic": True,
     "note": "Vowel plus /r/, and the least stable of the five — many speakers shift poor and sure toward /ɔr/."},
]


# ── The correspondences ───────────────────────────────────────────
# (vowel_id, grapheme, example words, strength[, caveat])
# strength: core = the main pattern · common · rare = a handful of words.
# Every row carries real example words; nothing here is generated.

_E: list[tuple] = [
    # /i/ FLEECE
    ("i", "ee", "see, tree, green", "core"),
    ("i", "ea", "eat, team, leaf", "core"),
    ("i", "e", "be, me, evil", "core"),
    ("i", "ie", "field, chief, believe", "core"),
    ("i", "y", "happy, city, funny", "core", "word-final, unstressed"),
    ("i", "e_e", "these, complete, theme", "common"),
    ("i", "ei", "receive, ceiling, seize", "common"),
    ("i", "i", "machine, ski, police", "common", "mostly loanwords"),
    ("i", "ey", "key, monkey, valley", "common"),
    ("i", "eo", "people", "rare"),
    ("i", "ae", "algae, Caesar", "rare"),
    ("i", "oe", "amoeba, phoenix", "rare"),
    ("i", "i_e", "marine, sardine", "rare"),
    # /ɪ/ KIT
    ("ih", "i", "sit, big, wish", "core"),
    ("ih", "y", "gym, myth, system", "core"),
    ("ih", "ui", "build, guilt, guitar", "common"),
    ("ih", "e", "pretty, English, before", "common"),
    ("ih", "a", "village, damage, orange", "common", "unstressed -age"),
    ("ih", "u", "busy, business", "rare"),
    ("ih", "o", "women", "rare"),
    ("ih", "ie", "sieve", "rare"),
    ("ih", "ee", "been", "rare", "varies by speaker"),
    # /ɛ/ DRESS
    ("eh", "e", "bed, ten, help", "core"),
    ("eh", "ea", "head, bread, weather", "core"),
    ("eh", "ue", "guest, guess", "common"),
    ("eh", "a", "any, many", "rare"),
    ("eh", "ai", "said, again", "rare"),
    ("eh", "ie", "friend", "rare"),
    ("eh", "eo", "leopard, jeopardy", "rare"),
    ("eh", "u", "bury", "rare"),
    ("eh", "ae", "aesthetic", "rare", "US spelling"),
    # /æ/ TRAP
    ("ae", "a", "cat, hand, map", "core"),
    ("ae", "ai", "plaid", "rare"),
    ("ae", "au", "laugh, aunt", "rare", "aunt varies by region"),
    # /ɑ/ LOT–PALM
    ("aa", "o", "hot, stop, box", "core"),
    ("aa", "a", "father, spa, drama", "core"),
    ("aa", "al", "calm, palm, half", "common", "the l is silent"),
    ("aa", "ow", "knowledge", "rare"),
    ("aa", "ach", "yacht", "rare"),
    # /ɔ/ THOUGHT
    ("ao", "aw", "saw, law, dawn", "core"),
    ("ao", "au", "cause, autumn, fault", "core"),
    ("ao", "a", "all, ball, walk, salt", "core"),
    ("ao", "ough", "bought, thought, cough", "common"),
    ("ao", "augh", "caught, daughter, taught", "common"),
    ("ao", "o", "off, dog, long, cross", "common", "merges with /ɑ/ for many speakers"),
    ("ao", "oa", "broad", "rare"),
    # /ʊ/ FOOT
    ("uh", "oo", "book, good, foot", "core"),
    ("uh", "u", "put, full, push", "core"),
    ("uh", "ou", "could, would, should", "common"),
    ("uh", "o", "wolf, woman, bosom", "rare"),
    # /u/ GOOSE
    ("uw", "oo", "food, moon, soon", "core"),
    ("uw", "u", "truth, ruby, super", "core"),
    ("uw", "u_e", "rule, June, flute", "core"),
    ("uw", "ew", "grew, flew, chew", "core"),
    ("uw", "ue", "blue, true, glue", "core"),
    ("uw", "ou", "soup, group, you", "common"),
    ("uw", "ui", "fruit, juice, suit", "common"),
    ("uw", "o", "do, to, who", "common"),
    ("uw", "o_e", "move, prove, lose", "common"),
    ("uw", "oe", "shoe, canoe", "rare"),
    ("uw", "ough", "through", "rare"),
    ("uw", "eu", "maneuver, sleuth", "rare"),
    ("uw", "wo", "two", "rare"),
    # /ʌ/ STRUT
    ("ah", "u", "cup, sun, but", "core"),
    ("ah", "o", "son, love, come, money", "core"),
    ("ah", "ou", "touch, country, young", "common"),
    ("ah", "oo", "blood, flood", "rare"),
    ("ah", "ough", "tough, rough, enough", "rare"),
    ("ah", "oe", "does", "rare"),
    # /ə/ SCHWA
    ("sch", "a", "about, sofa, banana", "core"),
    ("sch", "e", "taken, problem, the", "core"),
    ("sch", "i", "pencil, family, president", "core"),
    ("sch", "o", "lemon, second, common", "core"),
    ("sch", "u", "supply, minus, circus", "core"),
    ("sch", "ou", "famous, nervous", "common"),
    ("sch", "ai", "mountain, certain, bargain", "common"),
    ("sch", "io", "nation, mission", "common"),
    ("sch", "eo", "dungeon, surgeon", "rare"),
    ("sch", "ough", "thorough, borough", "rare"),
    ("sch", "ia", "parliament", "rare"),
    # /ɝ/ NURSE (+ unstressed /ɚ/)
    ("er", "er", "her, term, serve", "core"),
    ("er", "ir", "bird, girl, first", "core"),
    ("er", "ur", "turn, burn, nurse", "core"),
    ("er", "ear", "earth, learn, heard", "common"),
    ("er", "or", "word, work, world", "common"),
    ("er", "ar", "dollar, sugar, collar", "common", "unstressed /ɚ/"),
    ("er", "ure", "measure, picture", "common", "unstressed /ɚ/"),
    ("er", "our", "journey, courage", "rare"),
    ("er", "yr", "myrtle, myrrh", "rare"),
    # /eɪ/ FACE
    ("ey", "a_e", "make, name, late", "core"),
    ("ey", "ai", "rain, wait, train", "core"),
    ("ey", "ay", "day, play, say", "core"),
    ("ey", "a", "baby, table, apron", "core", "open syllable"),
    ("ey", "eigh", "weigh, neighbor, sleigh", "common"),
    ("ey", "ei", "vein, reign, beige", "common"),
    ("ey", "ey", "they, grey, obey", "common"),
    ("ey", "ea", "great, break, steak", "rare"),
    ("ey", "et", "ballet, buffet, gourmet", "rare", "French loans"),
    ("ey", "é", "café, résumé", "rare"),
    ("ey", "aigh", "straight", "rare"),
    # /aɪ/ PRICE
    ("ay", "i_e", "time, nine, like", "core"),
    ("ay", "igh", "high, night, light", "core"),
    ("ay", "y", "my, sky, cry", "core"),
    ("ay", "i", "find, mind, child", "core"),
    ("ay", "ie", "pie, tie, die", "common"),
    ("ay", "ei", "height, either", "rare", "either varies"),
    ("ay", "uy", "buy, guy", "rare"),
    ("ay", "ye", "dye, bye", "rare"),
    ("ay", "eye", "eye", "rare"),
    ("ay", "ai", "aisle", "rare"),
    # /ɔɪ/ CHOICE
    ("oy", "oi", "coin, point, voice", "core"),
    ("oy", "oy", "boy, toy, enjoy", "core"),
    ("oy", "uoy", "buoy", "rare", "chiefly British; US often /ˈbuːi/"),
    # /oʊ/ GOAT
    ("ow", "o", "go, no, open, most", "core"),
    ("ow", "o_e", "bone, home, note", "core"),
    ("ow", "oa", "boat, road, coat", "core"),
    ("ow", "ow", "snow, know, grow", "core"),
    ("ow", "oe", "toe, goes, hoe", "common"),
    ("ow", "ough", "though, dough, although", "rare"),
    ("ow", "ou", "shoulder, soul, boulder", "rare"),
    ("ow", "eau", "beau, bureau, plateau", "rare"),
    ("ow", "olk", "folk, yolk", "rare"),
    ("ow", "ew", "sew", "rare"),
    # /aʊ/ MOUTH
    ("aw", "ou", "out, house, sound", "core"),
    ("aw", "ow", "now, cow, down", "core"),
    ("aw", "ough", "bough, plough, drought", "rare"),
    # /ɑr/ START
    ("ar", "ar", "car, star, hard", "core"),
    ("ar", "ear", "heart, hearth", "rare"),
    ("ar", "er", "sergeant", "rare"),
    # /ɔr/ NORTH–FORCE
    ("or", "or", "for, north, storm", "core"),
    ("or", "ore", "more, store, before", "core"),
    ("or", "oar", "board, roar, soar", "common"),
    ("or", "our", "four, pour, course", "common"),
    ("or", "oor", "door, floor", "common"),
    ("or", "ar", "war, warm, quart", "common", "after w"),
    ("or", "aur", "dinosaur, centaur", "rare"),
    # /ɪr/ NEAR
    ("ir", "ear", "ear, hear, near", "core"),
    ("ir", "eer", "deer, beer, cheer", "core"),
    ("ir", "ere", "here, mere, sphere", "common"),
    ("ir", "ier", "pier, fierce", "rare"),
    ("ir", "eir", "weird", "rare"),
    # /ɛr/ SQUARE
    ("air", "air", "hair, chair, fair", "core"),
    ("air", "are", "care, share, bare", "core"),
    ("air", "ear", "bear, pear, wear", "common"),
    ("air", "ere", "there, where", "common"),
    ("air", "eir", "their, heir", "rare"),
    ("air", "ayer", "prayer", "rare"),
    # /ʊr/ CURE
    ("ur", "ure", "pure, cure, sure", "core"),
    ("ur", "ur", "during, rural", "common"),
    ("ur", "our", "tour, gourd", "rare"),
    ("ur", "oor", "poor, moor", "rare", "often shifts to /ɔr/"),
]

EDGES: list[dict] = [
    {"vowel": e[0], "grapheme": e[1], "examples": e[2], "strength": e[3],
     "caveat": e[4] if len(e) > 4 else None}
    for e in _E
]

_STRENGTH_ORDER = {"core": 0, "common": 1, "rare": 2}


def grapheme_label(gid: str) -> str:
    """``a_e`` is the split digraph (make, name) — render it as ``a…e``."""
    return gid.replace("_", "…") if "_" in gid else gid


def crosswalk_graph(weak: dict | None = None) -> dict:
    """Build the full graph, optionally joined against per-phone error counts.

    Pure: no ``self``, no I/O, so the invariants are unit-testable without a
    daemon. ``weak`` is the weak-phones.json store (ARPABET-keyed); when given,
    each vowel gains ``errors`` (its occurrence count) and each grapheme gains
    ``error_weight`` — the summed error count of the sounds it can spell, which
    is what makes a spelling *personally* worth studying rather than merely
    ambiguous.
    """
    weak = weak or {}

    by_vowel: dict[str, list[dict]] = {}
    by_grapheme: dict[str, list[dict]] = {}
    for e in EDGES:
        by_vowel.setdefault(e["vowel"], []).append(e)
        by_grapheme.setdefault(e["grapheme"], []).append(e)

    vowels = []
    for v in VOWELS:
        arpa = v.get("arpa")
        entry = dict(v)
        entry["spellings"] = len(by_vowel.get(v["id"], []))
        entry["errors"] = int((weak.get(arpa) or {}).get("occurrences", 0)) if arpa else 0
        vowels.append(entry)

    graphemes = []
    for gid, rows in by_grapheme.items():
        sounds = [r["vowel"] for r in rows]
        graphemes.append({
            "id": gid,
            "label": grapheme_label(gid),
            "fan": len(rows),
            "sounds": sounds,
            "error_weight": sum(
                int((weak.get(v.get("arpa")) or {}).get("occurrences", 0))
                for v in VOWELS if v["id"] in sounds and v.get("arpa")
            ),
        })
    graphemes.sort(key=lambda g: (-g["fan"], g["id"]))

    edges = sorted(
        EDGES,
        key=lambda e: (_STRENGTH_ORDER.get(e["strength"], 3), e["vowel"], e["grapheme"]),
    )

    return {
        "vowels": vowels,
        "graphemes": graphemes,
        "edges": edges,
        "totals": {
            "vowels": len(VOWELS),
            "graphemes": len(graphemes),
            "edges": len(EDGES),
            "unambiguous_spellings": sum(1 for g in graphemes if g["fan"] == 1),
        },
        "joined": bool(weak),
    }


@web_route("GET", "/api/crosswalk")
async def api_crosswalk(self, request):
    """The graph, joined against this learner's own per-phone error counts."""
    try:
        weak = self._load_weak_phones()
    except Exception:
        weak = {}
    return crosswalk_graph(weak)


def _kb_note_body(vowel: dict, rows: list[dict]) -> str:
    """Render one vowel's spellings as KB-note markdown. Pure, so the wording
    is testable without a daemon or a kb app."""
    lines = [
        f"The vowel /{vowel['ipa']}/ (lexical set {vowel['key'].upper()}) is written "
        f"{len(rows)} different ways in English.",
        "",
        vowel["note"],
        "",
        "## Spellings",
        "",
    ]
    for r in rows:
        cav = f" — {r['caveat']}" if r["caveat"] else ""
        lines.append(
            f"- `{grapheme_label(r['grapheme'])}` ({r['strength']}) — {r['examples']}{cav}"
        )
    return "\n".join(lines)


@web_route("POST", "/api/crosswalk/kb")
async def api_crosswalk_kb(self, request):
    """Materialise one KB note per vowel so kb_explain() and think() can read them.

    Goes through the kb app's own ``upsert_note`` rather than writing markdown
    into ``kb/sources/`` directly — kb owns that folder, its slug rules and its
    path layout, and upsert is idempotent, so re-running refreshes a note
    instead of forking a second one or resetting its ``created`` date. Its
    docstring names this exact case ("generators that refresh durable KB
    material"), and ``**extra_fields`` carries our phone/arpabet frontmatter
    without KB-specific glue.

    Slug is ``vowel-spellings-{id}`` — deliberately NOT the ``phone-{arpa}``
    scheme that shadowing/kb.py owns, so the two writers never collide.
    """
    by_vowel: dict[str, list[dict]] = {}
    for e in EDGES:
        by_vowel.setdefault(e["vowel"], []).append(e)

    written, failed = [], []
    for v in VOWELS:
        rows = sorted(
            by_vowel.get(v["id"], []),
            key=lambda e: _STRENGTH_ORDER.get(e["strength"], 3),
        )
        slug = f"vowel-spellings-{v['id']}"
        try:
            res = await self.call_app(
                "kb",
                "upsert_note",
                kind="concept",
                title=f"/{v['ipa']}/ {v['key'].upper()} — how English spells it",
                body=_kb_note_body(v, rows),
                domain="pronunciation",
                topic="vowel-spelling",
                slug=slug,
                phone=v["ipa"],
                lexical_set=v["key"],
                arpabet=v.get("arpa") or "",
                spellings=len(rows),
            )
        except Exception as exc:  # one bad note must not lose the rest
            failed.append({"slug": slug, "error": str(exc)})
            continue
        # kb is an optional app — absence returns None rather than raising, and
        # that must surface as a failure rather than a silent no-op.
        if not res or res.get("error"):
            failed.append({
                "slug": slug,
                "error": (res or {}).get("error") if res else "kb app unavailable",
            })
        else:
            written.append(slug)

    if written:
        await self.emit("dictionary:crosswalk_published", {"notes": len(written)})
    return {"written": len(written), "slugs": written, "failed": failed}


async def panel_vowel_crosswalk(self) -> list[dict] | None:
    """Hub panel — the vowels you actually get wrong, and how many ways each is spelled.

    Returns None (invisible) until the pronounce pipeline has logged something,
    so a fresh install never shows an empty box.
    """
    try:
        weak = self._load_weak_phones()
    except Exception:
        return None
    if not weak:
        return None

    graph = crosswalk_graph(weak)
    hits = [v for v in graph["vowels"] if v["errors"] > 0]
    if not hits:
        return None
    hits.sort(key=lambda v: (-v["errors"], -v["spellings"]))

    rows = []
    for v in hits[:5]:
        shared = " · /ə/+/ʌ/ share one ARPABET symbol" if v.get("arpa_shared") else ""
        rows.append({
            "title": f"/{v['ipa']}/ {v['key'].upper()}",
            "subtitle": f"{v['errors']} slip{'s' if v['errors'] != 1 else ''} · "
                        f"{v['spellings']} ways to spell it{shared}",
            "href": "/dictionary/pages/crosswalk.html#" + v["id"],
            "icon": "🔤",
        })
    return rows
