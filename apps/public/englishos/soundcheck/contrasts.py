"""soundcheck — the contrast inventory: which sound pairs are worth drilling.

Pure, stdlib-only, and imported by BOTH the runtime app and the offline bank
generator (``scripts/build_soundcheck_bank.py``). Owns: the vowel geometry
needed for adjacency, the ARPABET phone inventory, the ~30 TARGET_CONTRASTS the
bank is generated from, and the schwa/STRUT split that ARPABET alone cannot
express. Source of truth for *what counts as a confusable pair*.

No ``self``, no I/O, no app imports — the offline generator imports it directly
(``sys.path`` + ``from soundcheck import contrasts``, the same route
``tests/test_unit_dictionary_crosswalk.py`` uses for the crosswalk), so it must
stay importable without booting the app.

Two things here are deliberate and easy to get wrong if edited casually:

**The vowel geometry is a copy, and the copy is gated.** ``VOWEL_GEOMETRY``
mirrors the x/y quadrilateral coordinates in ``dictionary/crosswalk.py``. It is
duplicated rather than imported because this module must stay import-free for
the generator, and ``crosswalk`` reaches the app package. The duplication is a
drift risk, so ``tests/test_unit_soundcheck_bank.py`` asserts the two agree
field by field. Change one, the test tells you about the other.

**AH is two vowels wearing one label.** ARPABET has no separate symbol for
schwa, so /ə/ COMMA and /ʌ/ STRUT both map to ``AH`` — which the crosswalk flags
as ``arpa_shared`` and treats as unsolvable. cmudict's stress digits solve it:
``AH0`` is schwa, ``AH1``/``AH2`` are STRUT. This module carries that split, and
``to_telemetry_phone()`` is the one-way door back to bare ``AH`` for anything
written to ``weak-phones.json``, which is stress-blind.
"""

from __future__ import annotations

import math

# ── Vowel geometry ────────────────────────────────────────────────
# x = backness (0 front → 1 back), y = height (0 close → 1 open) — the two axes
# of the IPA vowel quadrilateral. Adjacency in this space IS confusability: the
# vowels a learner mixes up are the ones whose tongue positions are close.
# Mirrors dictionary/crosswalk.py VOWELS; the test asserts they agree.
# ``di`` marks a diphthong (positioned at its nucleus).

VOWEL_GEOMETRY: dict[str, dict] = {
    "IY": {"id": "i",   "ipa": "i",  "key": "fleece",  "x": 0.05, "y": 0.04},
    "IH": {"id": "ih",  "ipa": "ɪ",  "key": "kit",     "x": 0.22, "y": 0.22},
    "EY": {"id": "ey",  "ipa": "eɪ", "key": "face",    "x": 0.13, "y": 0.40, "di": True},
    "EH": {"id": "eh",  "ipa": "ɛ",  "key": "dress",   "x": 0.15, "y": 0.58},
    "AE": {"id": "ae",  "ipa": "æ",  "key": "trap",    "x": 0.11, "y": 0.86},
    "ER": {"id": "er",  "ipa": "ɝ",  "key": "nurse",   "x": 0.44, "y": 0.34},
    "AH": {"id": "ah",  "ipa": "ʌ",  "key": "strut",   "x": 0.60, "y": 0.66},
    "AY": {"id": "ay",  "ipa": "aɪ", "key": "price",   "x": 0.36, "y": 0.93, "di": True},
    "AW": {"id": "aw",  "ipa": "aʊ", "key": "mouth",   "x": 0.50, "y": 0.90, "di": True},
    "UW": {"id": "uw",  "ipa": "u",  "key": "goose",   "x": 0.94, "y": 0.04},
    "UH": {"id": "uh",  "ipa": "ʊ",  "key": "foot",    "x": 0.79, "y": 0.23},
    "OW": {"id": "ow",  "ipa": "oʊ", "key": "goat",    "x": 0.87, "y": 0.41, "di": True},
    "AO": {"id": "ao",  "ipa": "ɔ",  "key": "thought", "x": 0.93, "y": 0.63},
    "OY": {"id": "oy",  "ipa": "ɔɪ", "key": "choice",  "x": 0.78, "y": 0.75, "di": True},
    "AA": {"id": "aa",  "ipa": "ɑ",  "key": "lot",     "x": 0.94, "y": 0.94},
}

# Schwa is not in VOWEL_GEOMETRY under its own ARPABET label because it has
# none. It rides the AH0 stress digit instead. Its quadrilateral position is
# the crosswalk's ``sch`` entry.
SCHWA = {"id": "sch", "ipa": "ə", "key": "comma", "x": 0.53, "y": 0.49}

VOWELS: frozenset[str] = frozenset(VOWEL_GEOMETRY)

CONSONANTS: frozenset[str] = frozenset({
    "B", "CH", "D", "DH", "F", "G", "HH", "JH", "K", "L", "M", "N", "NG",
    "P", "R", "S", "SH", "T", "TH", "V", "W", "Y", "Z", "ZH",
})

PHONES: frozenset[str] = VOWELS | CONSONANTS

# ── The schwa/STRUT split ─────────────────────────────────────────
# cmudict writes stress on every vowel: 0 unstressed, 1 primary, 2 secondary.
# For AH that digit is not decoration — it is the whole distinction between two
# different vowels. Everywhere else the digit is stripped.

SCHWA_PHONE = "AH0"   # /ə/ COMMA — unstressed only, by definition
STRUT_PHONE = "AH1"   # /ʌ/ STRUT — schwa's stressed cousin


def split_ah(phone_with_stress: str) -> str:
    """``AH0`` → schwa, ``AH1``/``AH2`` → STRUT, everything else → stress-stripped.

    The only place in soundcheck where a stress digit survives into a phone
    label. Feed it a raw cmudict phone; get back the label the bank uses.
    """
    bare = strip_stress(phone_with_stress)
    if bare != "AH":
        return bare
    return SCHWA_PHONE if phone_with_stress.endswith("0") else STRUT_PHONE


def to_telemetry_phone(phone: str) -> str:
    """Map a bank phone label back to what ``weak-phones.json`` can store.

    One-way and lossy: ``AH0`` and ``AH1`` both collapse to ``AH``, because the
    telemetry store is keyed on stress-blind ARPABET and cannot represent the
    distinction. Called at the write boundary and nowhere else — the lossiness
    is a property of the sink, not of our data, and confining it here is what
    keeps that true.
    """
    return "AH" if phone in (SCHWA_PHONE, STRUT_PHONE) else phone


def strip_stress(phone: str) -> str:
    """cmudict phones carry trailing stress digits (AH0, AH1, AH2)."""
    return "".join(c for c in phone if not c.isdigit())


# ── Adjacency ─────────────────────────────────────────────────────

def vowel_distance(a: str, b: str) -> float:
    """Euclidean distance in the quadrilateral, or ``inf`` if not both vowels.

    Used to rank distractors: the second-nearest vowel is the honest third
    bucket in a 3-way sort, because it is the one the learner would plausibly
    reach for.
    """
    ga, gb = _geometry(a), _geometry(b)
    if ga is None or gb is None:
        return math.inf
    return math.hypot(ga["x"] - gb["x"], ga["y"] - gb["y"])


def _geometry(phone: str) -> dict | None:
    if phone == SCHWA_PHONE:
        return SCHWA
    if phone == STRUT_PHONE:
        return VOWEL_GEOMETRY["AH"]
    return VOWEL_GEOMETRY.get(strip_stress(phone))


def nearest_vowels(phone: str, n: int = 2, exclude: tuple[str, ...] = ()) -> list[str]:
    """The ``n`` closest vowels in the quadrilateral, nearest first."""
    origin = _geometry(phone)
    if origin is None:
        return []
    skip = {strip_stress(phone), *(strip_stress(e) for e in exclude)}
    ranked = sorted(
        (p for p in VOWEL_GEOMETRY if p not in skip),
        key=lambda p: vowel_distance(phone, p),
    )
    return ranked[:n]


# ── IPA + spoken-safe display (soundcheck-ipa-never-shown) ─────────
# Vowels already carry an ``ipa`` glyph in VOWEL_GEOMETRY/SCHWA — used
# internally for adjacency and by the bank generator's vowel-quality items,
# but never surfaced in the weakest-contrasts label a learner actually reads
# on the hub panel. Consonants had no IPA mapping at all. Both close here.
CONSONANT_IPA: dict[str, str] = {
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h",
    "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p",
    "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "ʒ",
}


def ipa_for(phone: str) -> str:
    """ARPABET phone code -> its IPA glyph. Falls back to the phone code
    itself for anything unrecognised, so a caller can always render something
    rather than branch on a possible miss."""
    geo = _geometry(phone)
    if geo is not None:
        return geo["ipa"]
    return CONSONANT_IPA.get(strip_stress(phone), phone)


def spoken_key_for(phone: str) -> str:
    """A TTS-safe, real-word name for a phone — the Wells lexical-set keyword
    for vowels ('kit', 'fleece', ...), or the bare ARPABET letters for a
    consonant (already close to how a person says them aloud: "el", "ess").
    Deliberately never an IPA glyph — most TTS engines cannot pronounce those;
    they either spell the codepoint out or garble it silently. See
    services/pronounce/server.py's own artifact-detection docstring for what
    a mis-rendered IPA glyph looks like once it round-trips through a scorer.
    """
    geo = _geometry(phone)
    if geo is not None:
        return geo["key"]
    return strip_stress(phone)


# ── Consonant natural classes ─────────────────────────────────────
# Sharing place or manner is the consonant analogue of quadrilateral adjacency.

PLACE: dict[str, str] = {
    "P": "bilabial", "B": "bilabial", "M": "bilabial",
    "F": "labiodental", "V": "labiodental",
    "TH": "dental", "DH": "dental",
    "T": "alveolar", "D": "alveolar", "S": "alveolar", "Z": "alveolar",
    "N": "alveolar", "L": "alveolar",
    "SH": "postalveolar", "ZH": "postalveolar",
    "CH": "postalveolar", "JH": "postalveolar", "R": "postalveolar",
    "Y": "palatal",
    "K": "velar", "G": "velar", "NG": "velar", "W": "velar",
    "HH": "glottal",
}

MANNER: dict[str, str] = {
    "P": "stop", "B": "stop", "T": "stop", "D": "stop", "K": "stop", "G": "stop",
    "F": "fricative", "V": "fricative", "TH": "fricative", "DH": "fricative",
    "S": "fricative", "Z": "fricative", "SH": "fricative", "ZH": "fricative",
    "HH": "fricative",
    "CH": "affricate", "JH": "affricate",
    "M": "nasal", "N": "nasal", "NG": "nasal",
    "L": "approximant", "R": "approximant", "W": "approximant", "Y": "approximant",
}

VOICED: frozenset[str] = frozenset({
    "B", "D", "G", "V", "DH", "Z", "ZH", "JH", "M", "N", "NG", "L", "R", "W", "Y",
})


def shares_class(a: str, b: str) -> bool:
    """True when two consonants share place or manner — i.e. are confusable."""
    a, b = strip_stress(a), strip_stress(b)
    if a not in CONSONANTS or b not in CONSONANTS:
        return False
    return PLACE.get(a) == PLACE.get(b) or MANNER.get(a) == MANNER.get(b)


# ── Canonical contrast ids ────────────────────────────────────────
# Sorted, so IY/IH and IH/IY are the same contrast. This is the join key for
# FSRS state, telemetry and the bank index — it must be stable forever.

def contrast_id(kind: str, a: str, b: str | None = None) -> str:
    """``sub:IH/IY`` · ``del:R`` · ``ins:R`` · ``stress:record`` · ``grapheme:ea``."""
    if kind in ("del", "ins", "stress", "grapheme") or b is None:
        return f"{kind}:{a}"
    lo, hi = sorted((a, b))
    return f"{kind}:{lo}/{hi}"


# ── The target contrasts ──────────────────────────────────────────
# Universal, not personal. Derived from quadrilateral adjacency, the standard
# consonant confusion set, and the syllable-structure operations. The learner's
# own pair data drives SELECTION, never generation — a per-learner bank could
# not be committed, reviewed once, or work at cold start.
#
# ``merger_sensitive`` marks a contrast the reference corpus itself does not
# reliably make. cmudict transcribes cause/bought/cough/caught as AA, not AO, so
# scoring a learner on AA/AO would mark them wrong on a distinction their own
# reference speaker has merged. Generated, shipped, and OFF by default.
#
# ``note`` is the learner-facing one-liner and is baked into item ``explain``
# text at build time, so the runtime never needs this module for prose.

TARGET_CONTRASTS: list[dict] = [
    # ── Vowels: quadrilateral neighbours ──
    {"kind": "sub", "a": "IY", "b": "IH", "dimension": "vowel",
     "note": "FLEECE is long and tense; KIT is short and lax — not a shortened FLEECE."},
    {"kind": "sub", "a": "IH", "b": "EH", "dimension": "vowel",
     "note": "KIT sits higher and tighter than DRESS; the jaw opens for DRESS."},
    {"kind": "sub", "a": "EH", "b": "AE", "dimension": "vowel",
     "note": "TRAP is lower and flatter than DRESS — the jaw drops further."},
    {"kind": "sub", "a": "EY", "b": "EH", "dimension": "vowel",
     "note": "FACE glides upward and closes; DRESS holds one position."},
    {"kind": "sub", "a": "EY", "b": "IY", "dimension": "vowel",
     "note": "FACE starts lower and moves; FLEECE starts high and stays."},
    {"kind": "sub", "a": "UW", "b": "UH", "dimension": "vowel",
     "note": "GOOSE is long and tight; FOOT is short and loose."},
    {"kind": "sub", "a": "UW", "b": "OW", "dimension": "vowel",
     "note": "GOOSE holds high and back; GOAT glides down from a mid start."},
    {"kind": "sub", "a": "AA", "b": "AH", "dimension": "vowel",
     "note": "LOT is further back and more open than STRUT."},
    {"kind": "sub", "a": "AE", "b": "AH", "dimension": "vowel",
     "note": "TRAP is front and flat; STRUT is central."},
    {"kind": "sub", "a": "AA", "b": "AO", "dimension": "vowel", "merger_sensitive": True,
     "note": "LOT vs THOUGHT — about 40% of American speakers merge these entirely."},
    {"kind": "sub", "a": "OW", "b": "AO", "dimension": "vowel",
     "note": "GOAT glides and closes; THOUGHT holds one open, rounded position."},
    {"kind": "sub", "a": "AY", "b": "AA", "dimension": "vowel",
     "note": "PRICE moves from low to high; LOT stays low."},
    {"kind": "sub", "a": "AW", "b": "AA", "dimension": "vowel",
     "note": "MOUTH glides back and rounds; LOT does neither."},
    {"kind": "sub", "a": "OY", "b": "OW", "dimension": "vowel",
     "note": "CHOICE glides toward KIT; GOAT glides toward FOOT."},
    {"kind": "sub", "a": "ER", "b": "AH", "dimension": "vowel",
     "note": "NURSE is r-coloured all the way through; STRUT has no r in it."},

    # ── Consonants: the standard confusion set ──
    {"kind": "sub", "a": "L", "b": "R", "dimension": "consonant",
     "note": "L touches the ridge behind the teeth; R never touches anything."},
    {"kind": "sub", "a": "TH", "b": "S", "dimension": "consonant",
     "note": "TH puts the tongue between the teeth; S keeps it behind them."},
    {"kind": "sub", "a": "TH", "b": "F", "dimension": "consonant",
     "note": "Both are quiet fricatives — TH uses the tongue, F uses the lip."},
    {"kind": "sub", "a": "DH", "b": "Z", "dimension": "consonant",
     "note": "The voiced pair of TH/S — tongue between the teeth, not behind."},
    {"kind": "sub", "a": "DH", "b": "D", "dimension": "consonant",
     "note": "DH keeps airflow going; D stops it completely."},
    {"kind": "sub", "a": "V", "b": "W", "dimension": "consonant",
     "note": "V presses lip to teeth; W rounds both lips and touches nothing."},
    {"kind": "sub", "a": "V", "b": "B", "dimension": "consonant",
     "note": "V leaks air continuously; B blocks it and releases."},
    {"kind": "sub", "a": "S", "b": "SH", "dimension": "consonant",
     "note": "SH pulls the tongue back and rounds the lips slightly."},
    {"kind": "sub", "a": "CH", "b": "SH", "dimension": "consonant",
     "note": "CH begins with a full stop; SH is fricative throughout."},
    {"kind": "sub", "a": "JH", "b": "ZH", "dimension": "consonant",
     "note": "The voiced pair of CH/SH — JH starts with a stop."},
    {"kind": "sub", "a": "N", "b": "NG", "dimension": "consonant",
     "note": "N is at the front ridge; NG is at the soft palate, right back."},
    {"kind": "sub", "a": "N", "b": "L", "dimension": "consonant",
     "note": "Same tongue position — N sends air through the nose, L round the sides."},

    # ── Syllable structure: deletion ──
    # These map directly to the learner's own del/ins telemetry, and they are
    # where final-position errors live.
    {"kind": "del", "a": "T", "dimension": "syllable",
     "note": "A dropped final T takes the whole past tense with it."},
    {"kind": "del", "a": "D", "dimension": "syllable",
     "note": "A dropped final D is the difference between cleaned and clean."},
    {"kind": "del", "a": "S", "dimension": "syllable",
     "note": "A dropped final S removes the plural, or the third person."},
    {"kind": "del", "a": "Z", "dimension": "syllable",
     "note": "The voiced plural — dropped, and one becomes many becomes one again."},
    {"kind": "del", "a": "R", "dimension": "syllable", "accent": "rhotic",
     "note": "A dropped R after a vowel is standard in Australian and British English, and absent from General American."},
    {"kind": "del", "a": "L", "dimension": "syllable",
     "note": "A dropped L at the end of a syllable turns a word into its own shadow."},
    {"kind": "del", "a": "K", "dimension": "syllable",
     "note": "Final stops are quiet in English, but dropping one changes the word."},

    # ── Syllable structure: insertion ──
    {"kind": "ins", "a": "AH0", "dimension": "syllable",
     "note": "An extra vowel inside a consonant cluster adds a syllable that is not there."},
    {"kind": "ins", "a": "R", "dimension": "syllable", "accent": "linking-r",
     "note": "An R between two vowels is standard linking in Australian and British English."},
]

CONTRAST_INDEX: dict[str, dict] = {
    contrast_id(c["kind"], c["a"], c.get("b")): c for c in TARGET_CONTRASTS
}


def is_target(cid: str) -> bool:
    return cid in CONTRAST_INDEX


def merger_sensitive(cid: str) -> bool:
    return bool(CONTRAST_INDEX.get(cid, {}).get("merger_sensitive"))
