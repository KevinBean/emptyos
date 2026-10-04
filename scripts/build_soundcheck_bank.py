#!/usr/bin/env python3
"""Build the soundcheck item bank from the CMU Pronouncing Dictionary.

Offline, deterministic, and run by hand — the daemon never imports this. Output
is committed JSONL under ``apps/public/englishos/soundcheck/bank/``
so the bank is identical on every install and reviewable in ``git diff``.

    python scripts/build_soundcheck_bank.py                # regenerate
    python scripts/build_soundcheck_bank.py --check        # drift gate, exit 1
    python scripts/build_soundcheck_bank.py --report       # crosswalk audit
    python scripts/build_soundcheck_bank.py --dimension vowel

## Licensing — why this reads a file instead of importing a package

The ``cmudict`` PyPI *wrapper* is GPL-3.0. The dictionary *data* it bundles is
BSD-2-Clause (Carnegie Mellon), which is freely redistributable with the notice
retained. So this script locates ``cmudict/data/cmudict.dict`` and parses it
with ``open()`` — the GPL wrapper is never imported, nothing links against it,
and no runtime dependency is added. ``bank/CMUDICT-LICENSE.txt`` carries the
required notice.

``wordfreq`` supplies Zipf frequencies and is already declared in the project's
``english`` extra. Its *code* is Apache-2.0 but its frequency *data* is
CC BY-SA 4.0, so the values are used only at build time (word floors and
difficulty) and are never written into the shipped bank — see
``BUILD_ONLY_KEYS``.

## The three traps this filters, all measured rather than assumed

A naive pass over 126k entries produces a bank that looks fine and is not:

1. **Proper nouns flood every contrast** — ``aaron/ellen``, ``bali/bonnie``,
   ``adam/atom``, plus single letters (``c``, ``v``, ``b``) in *every* result.
   No offline proper-noun or POS filter exists on this machine (nltk, spacy and
   pyphen are all absent), so the defence is a length floor plus a hand-curated
   name list, and a human review pass over the shipped word list.
2. **Inflection is grammar, not phonics** — the top three contrasts by raw
   count are ``S~T``, ``D~Z`` and ``D~T``, almost entirely past-tense and plural
   suffixes (``asked/asks``). Excluded unless the deletion is the *point* of the
   item, as it is in the syllable dimension.
3. **Ranking by count picks the wrong contrasts** — ``L~R``, the single most
   important contrast for a Mandarin-speaking learner, sits *below* three suffix
   artifacts. So generation is driven by a curated list, never by frequency.

## The review gate

Items go to ``<dimension>.jsonl`` only when a human has passed them. Everything
else lands in ``_pending.jsonl``, which is committed but never loaded — the
runtime cannot serve an unreviewed item, which is a stronger guarantee than
remembering to check. ``curation.json`` records the durable human decisions and
is re-applied on every regeneration, so judgement survives a corpus update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PARENT = ROOT / "apps" / "public" / "englishos"
APP_DIR = APP_PARENT / "soundcheck"
BANK_DIR = APP_DIR / "bank"

if str(APP_PARENT) not in sys.path:
    sys.path.insert(0, str(APP_PARENT))

from soundcheck import contrasts as C  # noqa: E402
from soundcheck.shared import slug  # noqa: E402

# ── Tunables, every one of them measured ──────────────────────────

ZIPF_CORE = 3.5      # 14,514 words. At 3.0 the pool doubles and starts testing
                     # vocabulary instead of perception; at 4.0 it halves and
                     # the low-yield contrasts starve.
ZIPF_RESCUE = 3.0    # only for contrasts under RESCUE_FLOOR at the core gate
RESCUE_FLOOR = 12
MIN_LETTERS = 3      # kills the single-letter name pollution outright
MAX_LETTERS = 12
CAP_PER_CONTRAST = 24
CAP_SYLLABLE_MORPH = 60
CAP_SYLLABLE_LEXICAL = 40
CAP_CLUSTER = 30
CAP_EPENTHESIS = 20
SAME_RATE = 0.20     # share of odd-one-out items with no odd member

# Suffixes whose presence makes a "contrast" a morphology lesson rather than a
# perception one. A pair differing only by one of these is excluded from the
# vowel/consonant dimensions — but is exactly what the syllable dimension wants.
INFLECTION_PHONES = {"S", "Z", "T", "D", "IH0", "AH0"}

# Hand-authored, and says so. No offline name list exists here, and cross-language
# frequency was rejected as a detector — it misfires on hotel, radio, piano, taxi,
# and a wrong exclusion is invisible. This is the honest small version; additions
# belong in curation.json where a human decision is recorded permanently.
COMMON_NAMES = frozenset("""
aaron abby adam alan albert alec alex alexa alexis alfred alice alicia allan allen
alma alvin alyssa amanda amber amy andre andrea andrew andy angela anita ann anna
anne annie anthony april archie arnold arthur ashley ashton audrey austin barbara
barry beatrice becky ben benjamin bernard bert bertha bessie beth betty beverly
bill billy bob bobby bonnie brad bradley brandon brenda brian bruce bryan calvin
cameron carl carla carlos carmen carol carole caroline carolyn carrie casey
catherine cathy cecil charles charlie charlotte chester chris christian christina
christine christopher cindy claire clara clarence claude clayton clifford clinton
clyde cody colin connie conrad cora corey craig crystal curtis cynthia daisy dale
dallas dan dana daniel danny darrell darren dave david dawn dean deborah debra
delia denise dennis derek diana diane dick dolores don donald donna dora doris
dorothy doug douglas duane dustin dwight earl ed eddie edgar edith edna edward
edwin eileen elaine eleanor elena elizabeth ella ellen elmer eloise elsie emily
emma eric erica erin ernest ester esther ethel eugene eunice eva evan evelyn
felix fernando flora florence floyd frances francis frank franklin fred freda
freddie frederick gabriel gail gary gene geneva george gerald geraldine gilbert
gina gladys glen glenda glenn gloria gordon grace grant greg gregory gretchen
guadalupe guy harold harriet harry harvey hazel heather hector heidi helen henry
herbert herman hilda holly homer hope howard hubert hugh ian ida irene iris irma
irving isaac isabel ivan jack jackie jacob jaime james jamie jan jane janet janice
jason jay jean jeanette jeff jeffery jeffrey jenna jennie jennifer jenny jeremy
jerome jerry jesse jessica jessie jill jim jimmie jimmy joan joann joanne jody joe
joel john johnnie johnny jon jonathan jordan jose joseph josephine josh joshua joy
joyce juan juanita judith judy julia julian julie june justin karen karl kate
katherine kathleen kathryn kathy katie kay keith kelly ken kenneth kent kevin kim
kimberly kirk kristen kristin kurt kyle lance larry laura lauren laurie lawrence
lee leigh lena leo leon leonard leroy leslie lester lewis lila lillian lillie
linda lindsay lisa lloyd logan lois lola lonnie loren lorena lorraine lou louis
louise lucas lucia lucille lucy luis luke lula luther lydia lyle lynn mabel mack
madeline mae marc marcia marcus margaret margie maria marian marie marilyn marion
marjorie mark marlene marsha marshall martha martin marvin mary mathew matt
matthew maureen maurice max maxine may megan melanie melinda melissa melvin
mercedes meredith micheal michael michele michelle miguel mike mildred milton
minnie miranda miriam misty mitchell molly monica morris moses muriel myra myrtle
nadine nancy naomi natalie nathan neal neil nelson nettie nicholas nichole nick
nicole nina noah nora norma norman olga olive oliver olivia ollie omar opal ora
orville oscar otis otto owen pablo pam pamela pat patricia patrick patsy patty
paul paula pauline pearl pedro peggy penny percy perry pete peter phil philip
phillip phoebe phyllis polly preston priscilla rachel ralph ramon ramona randall
randy raul ray raymond rebecca regina reginald rene rex rhonda ricardo richard
rick ricky rita rob robert roberta roberto robin rochelle rodney roger roland ron
ronald ronnie rosa rosalie rose rosemary ross roy ruby rudolph rufus russell ruth
ryan sabrina sally salvador sam samantha samuel sandra sandy sara sarah saul scott
sean seth shane shannon sharon shaun shawn sheila shelia shelley sherri sherry
shirley sidney silvia simon sonia sonya sophia spencer stacey stacy stanley stella
stephanie stephen steve steven stewart stuart sue susan susie suzanne sylvia tammy
tanya tara ted teresa terrance terri terry thelma theodore theresa thomas tim
timmy timothy tina toby todd tom tommy toni tony tracy travis trevor troy tyler
tyrone valerie van vanessa velma vera verna vernon veronica vicki vickie vicky
victor victoria vincent viola violet virgil virginia vivian wade wallace walter
wanda warren wayne wendell wendy wesley whitney wilbur wiley wilfred will willard
william willie willis wilma wilson winifred winston woodrow yolanda yvonne zachary
africa alabama alaska albany america arizona asia aspen athens atlanta austin bali
berlin boston brazil britain cairo canada chile china cuba dallas delhi denver
detroit dublin egypt england europe france fresno geneva georgia germany ghana
greece haiti hawaii holland houston india indiana iowa iran iraq ireland israel
italy jamaica japan jersey jordan kansas kenya korea kuwait laos leon lima london
madrid maine malta memphis mexico miami milan montana moscow munich nairobi naples
nepal nevada newark norway ohio oman ontario oregon oslo oxford panama paris peking
peru poland prague quebec reno rome russia rwanda salem samoa seattle seoul serbia
seville siberia sicily somalia spain sudan sweden sydney syria taiwan tampa texas
tibet togo tokyo tulsa tunisia turkey uganda ukraine utah venice vermont vienna
vietnam virginia wales warsaw yemen zaire zambia
""".split())

# Words that are legal English but make an unpleasant drill item.
TASTE_REJECT = frozenset("""
ass asses arse bastard bitch cock crap damn dick fuck hell piss shit slut tit twat
whore nigger nazi rape
""".split())


# ── cmudict, read as data ─────────────────────────────────────────

def find_cmudict(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise SystemExit(f"--cmudict: no such file: {p}")
    for base in map(Path, sys.path):
        candidate = base / "cmudict" / "data" / "cmudict.dict"
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "cmudict data not found. Install it for the build only:\n"
        "    pip install cmudict\n"
        "or pass --cmudict /path/to/cmudict.dict"
    )


def parse_cmudict(path: Path) -> tuple[dict[str, list[str]], dict[str, list[list[str]]]]:
    """``primary[word] -> phones`` and ``variants[word] -> [phones, ...]``.

    Variants are kept: the stress goldmine (REcord vs reCORD) lives entirely in
    the alternate pronunciations, and a word with two of them is precisely the
    word that must never appear in an eye-modality item.
    """
    primary: dict[str, list[str]] = {}
    variants: dict[str, list[list[str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            head, _, rest = line.partition(" ")
            if not rest:
                continue
            word = head
            alt = False
            if word.endswith(")") and "(" in word:
                word, _, _ = word.partition("(")
                alt = True
            phones = rest.split()
            if not phones:
                continue
            variants[word].append(phones)
            if not alt and word not in primary:
                primary[word] = phones
    return primary, dict(variants)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── word gating ───────────────────────────────────────────────────

def _zipf():
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        raise SystemExit(
            "wordfreq is required for the build:  pip install 'emptyos[english]'"
        )
    return zipf_frequency


VOWEL_LETTERS = set("aeiouy")


def acceptable(word: str, curation: dict) -> bool:
    if not word.isalpha():
        return False
    if not (MIN_LETTERS <= len(word) <= MAX_LETTERS):
        return False
    if word in COMMON_NAMES or word in TASTE_REJECT:
        return False
    if word in set(curation.get("reject", ())):
        return False
    # Acronyms and initialisms read as words to cmudict but are useless drill
    # items — "aaa" and "nyc" have no ordinary pronunciation to practise.
    if not (VOWEL_LETTERS & set(word)):
        return False
    if len(set(word)) == 1:
        return False
    return True


def syllables_of(phones: list[str]) -> int:
    return sum(1 for p in phones if p[-1:].isdigit())


def stress_index(phones: list[str]) -> int:
    """0-based index among the vowels of the primary-stressed one, or -1."""
    idx = -1
    seen = 0
    for p in phones:
        if p[-1:].isdigit():
            if p.endswith("1"):
                idx = seen
            seen += 1
    return idx


def bare(phones: list[str]) -> tuple[str, ...]:
    return tuple(C.strip_stress(p) for p in phones)


def labelled(phones: list[str]) -> tuple[str, ...]:
    """Stress-stripped, except AH, where the digit is the whole distinction."""
    return tuple(C.split_ah(p) for p in phones)


# ── the O(N·L) pair finder ────────────────────────────────────────

def substitution_pairs(pool: dict[str, tuple[str, ...]]) -> dict[tuple, list[tuple]]:
    """Bucket by (index, everything-but-that-index); same bucket = one-phone apart.

    Linear in the corpus rather than quadratic — measured under a second over
    the full dictionary, where a pairwise comparison would be ~8 billion checks.
    """
    buckets: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for word, phones in pool.items():
        for i in range(len(phones)):
            buckets[(i, phones[:i], phones[i + 1:])].append((word, phones[i]))

    found: dict[tuple, list[tuple]] = defaultdict(list)
    for members in buckets.values():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                (w1, p1), (w2, p2) = members[a], members[b]
                if p1 == p2:
                    continue
                key = tuple(sorted((C.strip_stress(p1), C.strip_stress(p2))))
                found[key].append((w1, w2, p1, p2))
    return found


def deletion_pairs(pool: dict[str, tuple[str, ...]]) -> list[tuple]:
    """(long, short, dropped_phone, index) where dropping one phone yields short."""
    index = set(pool.values())
    by_seq: dict[tuple, str] = {}
    for word, phones in pool.items():
        by_seq.setdefault(phones, word)

    out = []
    for word, phones in pool.items():
        for i in range(len(phones)):
            shorter = phones[:i] + phones[i + 1:]
            if shorter in index:
                other = by_seq.get(shorter)
                if other and other != word:
                    out.append((word, other, phones[i], i))
    return out


def is_inflectional(long_phones: tuple, short_phones: tuple, dropped: str) -> bool:
    """Is the difference just a grammatical ending?"""
    return (
        C.strip_stress(dropped) in {"S", "Z", "T", "D"}
        and len(long_phones) == len(short_phones) + 1
        and long_phones[:-1] == short_phones
    )


# ── item construction ─────────────────────────────────────────────

def _word(text: str, phones: list[str], focus: int | None = None) -> dict:
    w = {"text": text, "arpa": " ".join(phones)}
    if focus is not None:
        w["focus"] = focus
    return w


def make_minimal_pair(cid: str, spec: dict, stim: str, other: str,
                      stim_phones: list[str], other_phones: list[str],
                      stim_sound: str, other_sound: str,
                      difficulty: int, why: list[str], freq: list[float],
                      position: str, verified: bool) -> dict:
    stem = f"mp-{slug(cid)}-{slug(stim)}-{slug(other)}"
    return {
        "id": f"{stem}-{slug(stim)}",
        "v": 1,
        "stem_id": stem,
        "dimension": spec["dimension"],
        "shape": "minimal_pair",
        "contrast_id": cid,
        "phones": [C.strip_stress(stim_sound), C.strip_stress(other_sound)],
        "difficulty": difficulty,
        "why_hard": why,
        "words": [_word(stim, stim_phones), _word(other, other_phones)],
        "options": [{"id": "a", "label": stim}, {"id": "b", "label": other}],
        "answer": ["a"],
        "miss_maps_to": {"b": {"op": "sub",
                               "ref": C.strip_stress(stim_sound),
                               "hyp": C.strip_stress(other_sound)}},
        "explain": spec.get("note", ""),
        "freq": freq,
        "syllables": syllables_of(stim_phones),
        "position": position,
        "merger_sensitive": bool(spec.get("merger_sensitive")),
        "accent": spec.get("accent"),
        "src": "cmu+wordfreq",
        "verified": verified,
        "calibration": False,
    }


def make_deletion_item(cid: str, spec: dict, long_word: str, short_word: str,
                       long_phones: list[str], short_phones: list[str],
                       dropped: str, difficulty: int, why: list[str],
                       freq: list[float], morphological: bool,
                       verified: bool, position: str = "final") -> dict:
    stem = f"del-{slug(cid)}-{slug(long_word)}-{slug(short_word)}"
    return {
        "id": f"{stem}-a",
        "v": 1,
        "stem_id": stem,
        "dimension": "syllable",
        "shape": "minimal_pair",
        "contrast_id": cid,
        "phones": [C.strip_stress(dropped)],
        "difficulty": difficulty,
        "why_hard": why,
        "words": [_word(long_word, long_phones), _word(short_word, short_phones)],
        "options": [{"id": "a", "label": long_word}, {"id": "b", "label": short_word}],
        "answer": ["a"],
        "miss_maps_to": {"b": {"op": "del", "ref": C.strip_stress(dropped),
                               "hyp": None}},
        "explain": spec.get("note", ""),
        "freq": freq,
        "syllables": syllables_of(long_phones),
        "position": position,
        "merger_sensitive": False,
        "accent": spec.get("accent"),
        "src": "cmu+wordfreq",
        "verified": verified,
        "calibration": False,
        "tags": ([("morphological" if morphological else "lexical"), position]),
    }


def make_stress_item(word: str, phones: list[str], syllables: int,
                     stressed: int, difficulty: int, freq: float,
                     verified: bool) -> dict:
    stem = f"st-{slug(word)}"
    options = [{"id": str(i + 1), "label": f"beat {i + 1}"} for i in range(syllables)]
    beat = " ".join("DA" if i == stressed else "da" for i in range(syllables))
    return {
        "id": f"{stem}-pos",
        "v": 1,
        "stem_id": stem,
        "dimension": "stress",
        "shape": "stress",
        "contrast_id": C.contrast_id("stress", slug(word)),
        "phones": [],
        "difficulty": difficulty,
        "why_hard": ["multisyllabic"] if syllables >= 3 else [],
        "words": [_word(word, phones)],
        "options": options,
        "answer": [str(stressed + 1)],
        "explain": f"{word} is {beat} — the beat falls on syllable {stressed + 1}.",
        "freq": [freq],
        "syllables": syllables,
        "beat": beat,
        "position": "nucleus",
        "merger_sensitive": False,
        "accent": None,
        "src": "cmu+wordfreq",
        "verified": verified,
        "calibration": False,
    }


def make_spelling_item(grapheme: str, word: str, phones: list[str],
                       vowel_id: str, sounds: list[str], vowels_by_id: dict,
                       difficulty: int, verified: bool, note: str,
                       freq: float = 0.0) -> dict:
    stem = f"gs-{slug(grapheme)}-{slug(word)}"
    options = []
    for vid in sounds:
        v = vowels_by_id.get(vid)
        if not v:
            continue
        options.append({"id": vid, "label": f"/{v['ipa']}/ as in {v['key']}"})
    answer_arpa = (vowels_by_id.get(vowel_id) or {}).get("arpa")
    miss = {}
    for opt in options:
        if opt["id"] == vowel_id:
            continue
        hyp = (vowels_by_id.get(opt["id"]) or {}).get("arpa")
        if hyp and answer_arpa:
            miss[opt["id"]] = {"op": "sub", "ref": answer_arpa, "hyp": hyp}
    return {
        "id": f"{stem}-sort",
        "v": 1,
        "stem_id": stem,
        "dimension": "spelling",
        "shape": "sort",
        "contrast_id": C.contrast_id("grapheme", grapheme),
        "phones": [p for p in [answer_arpa] if p],
        "difficulty": difficulty,
        "why_hard": [f"fan-{len(sounds)}"],
        "words": [_word(word, phones)],
        "options": options,
        "answer": [vowel_id],
        "miss_maps_to": miss,
        "explain": note,
        "freq": [freq],
        "syllables": syllables_of(phones),
        "position": "nucleus",
        "merger_sensitive": False,
        "accent": None,
        "src": "crosswalk+cmu",
        "verified": verified,
        "calibration": False,
    }


# ── difficulty ────────────────────────────────────────────────────

def rate_difficulty(freqs: list[float], syllables: int, adjacent: bool,
                    position: str) -> tuple[int, list[str]]:
    score, why = 1, []
    lo = min(freqs) if freqs else 5.0
    if lo < 4.0:
        score += 1
        why.append("uncommon")
    if lo < ZIPF_CORE:
        score += 1
        why.append("rare")
    if syllables >= 3:
        score += 1
        why.append("multisyllabic")
    if adjacent:
        score += 1
        why.append("adjacent-sounds")
    if position == "final":
        score += 1
        why.append("final-position")
    return min(5, score), why


def adjacent_sounds(a: str, b: str) -> bool:
    a, b = C.strip_stress(a), C.strip_stress(b)
    if a in C.VOWELS and b in C.VOWELS:
        return C.vowel_distance(a, b) < 0.35
    return C.shares_class(a, b)


# ── the crosswalk cross-audit ─────────────────────────────────────

def audit_crosswalk(primary: dict[str, list[str]], curation: dict) -> dict:
    """Check every crosswalk example word against cmudict.

    A free deliverable: the crosswalk's 151 correspondences were hand-authored
    and, by its own track notes, only lightly spot-checked. Joining them against
    a real dictionary audits most of them as a side effect of building this
    bank, and ``--check`` fails on any *new* disagreement thereafter.
    """
    from dictionary import crosswalk  # noqa: PLC0415 — build-time only

    by_id = {v["id"]: v for v in crosswalk.VOWELS}
    known = set(curation.get("audit_known", ()))
    match, mismatch, oov, skipped = 0, [], [], 0

    for edge in crosswalk.EDGES:
        vowel = by_id.get(edge["vowel"]) or {}
        want = vowel.get("arpa")
        if not want:
            skipped += 1                      # r-coloured: no single phone
            continue
        for word in [w.strip().lower() for w in edge["examples"].split(",")]:
            if not word:
                continue
            phones = primary.get(word)
            if not phones:
                oov.append(word)
                continue
            vowels = [C.strip_stress(p) for p in phones if p[-1:].isdigit()]
            if want in vowels:
                match += 1
            else:
                key = f"{edge['grapheme']}:{word}"
                mismatch.append({"key": key, "word": word, "grapheme": edge["grapheme"],
                                 "claimed": want, "cmudict": vowels,
                                 "known": key in known})

    return {"match": match, "mismatch": mismatch, "oov": sorted(set(oov)),
            "skipped": skipped,
            "new": [m for m in mismatch if not m["known"]]}


# ── generation ────────────────────────────────────────────────────

# ── the mechanised half of review ─────────────────────────────────

AUTO_MIN_ZIPF = 4.2      # ~7,000 words — everyday vocabulary, not merely attested
AUTO_MIN_LETTERS = 4
AUTO_MAX_DIFFICULTY = 3
AUTO_MAX_FAN = 4


def clears_auto_bar(item: dict) -> bool:
    """Can this item be approved without a human looking at it?

    Review is not one job. Some of it is judgement no script can do — is this
    word worth a learner's time, does the explanation read well, is the pairing
    fair. Some of it is a bar, and a bar can be stated and enforced. This is the
    second half only, and it is deliberately strict: it approves the obvious,
    and everything with any question about it stays pending.

    The frequency floor is doing most of the work. At Zipf 4.2 a word is part of
    everyday vocabulary, which excludes almost every proper noun and loanword
    that survived the name list — ``ahmad`` and ``bach`` are attested but nowhere
    near this band.
    """
    if item.get("merger_sensitive") or item.get("accent"):
        return False                      # a human decides what accent teaching says
    if item.get("difficulty", 5) > AUTO_MAX_DIFFICULTY:
        return False
    if "rescue" in (item.get("why_hard") or []):
        return False                      # below the core frequency gate by design
    if any(f"fan-{n}" in (item.get("why_hard") or [])
           for n in range(AUTO_MAX_FAN + 1, 10)):
        return False                      # too many buckets to choose between
    for word in item.get("words") or []:
        text = word.get("text", "")
        if len(text) < AUTO_MIN_LETTERS or text in COMMON_NAMES:
            return False
    freqs = item.get("freq") or []
    if not freqs or min(freqs) < AUTO_MIN_ZIPF:
        return False
    return True


def build(primary, variants, curation, zipf, *, only=None) -> tuple[dict, list, dict]:
    """Return ``({dimension: [items]}, pending, stats)``."""
    core: dict[str, tuple[str, ...]] = {}
    rescue: dict[str, tuple[str, ...]] = {}
    freq: dict[str, float] = {}

    for word, phones in primary.items():
        if not acceptable(word, curation):
            continue
        z = zipf(word, "en")
        if z < ZIPF_RESCUE:
            continue
        freq[word] = z
        key = labelled(phones)
        (core if z >= ZIPF_CORE else rescue)[word] = key

    out: dict[str, list[dict]] = defaultdict(list)
    pending: list[dict] = []
    stats: dict[str, int] = defaultdict(int)

    approved = set(curation.get("approve", ()))
    rejected_ids = set(curation.get("reject_ids", ()))

    def emit(item: dict, dimension: str) -> None:
        if item["id"] in rejected_ids:
            return
        # Hand-authored content was reviewed when it was written — a human chose
        # the words, the distractors and the explanation. The pending gate exists
        # to stop *generated* items shipping unlooked-at, and holding hand-written
        # ones behind it would mean approving a file you just authored.
        hand = item.get("src") == "hand"
        item["verified"] = (
            hand or item["id"] in approved or clears_auto_bar(item)
        )
        (out[dimension] if item["verified"] else pending).append(item)
        stats[f"{dimension}:{'verified' if item['verified'] else 'pending'}"] += 1

    core_pairs = substitution_pairs(core)
    all_pairs = substitution_pairs({**rescue, **core})

    # ── substitution contrasts: vowels + consonants ──
    for spec in C.TARGET_CONTRASTS:
        if spec["kind"] != "sub":
            continue
        cid = C.contrast_id("sub", spec["a"], spec["b"])
        if only and spec["dimension"] != only:
            continue
        key = tuple(sorted((C.strip_stress(spec["a"]), C.strip_stress(spec["b"]))))
        found = core_pairs.get(key, [])
        tag_rescue = False
        if len(found) < RESCUE_FLOOR:
            found = all_pairs.get(key, [])
            tag_rescue = True

        rng = random.Random(hashlib.sha256(cid.encode()).hexdigest()[:8])
        candidates = sorted(found)
        rng.shuffle(candidates)

        made = 0
        for w1, w2, p1, p2 in candidates:
            if made >= CAP_PER_CONTRAST:
                break
            if w1 not in freq or w2 not in freq:
                continue
            ph1, ph2 = primary[w1], primary[w2]
            if is_inflectional(bare(ph1), bare(ph2), p1):
                continue
            position = _position_of(ph1, p1)
            diff, why = rate_difficulty([freq[w1], freq[w2]],
                                        syllables_of(ph1),
                                        adjacent_sounds(p1, p2), position)
            if tag_rescue:
                why = why + ["rescue"]
            for stim, other, sp, op_, sph, oph in (
                (w1, w2, p1, p2, ph1, ph2), (w2, w1, p2, p1, ph2, ph1)
            ):
                item = make_minimal_pair(cid, spec, stim, other, sph, oph, sp, op_,
                                         diff, why, [freq[stim], freq[other]],
                                         position, verified=False)
                emit(item, spec["dimension"])
            made += 1

    # ── syllable structure: deletion ──
    if not only or only == "syllable":
        _build_deletions(core, primary, freq, emit)

    # ── word stress ──
    if not only or only == "stress":
        _build_stress(core, primary, freq, emit, variants)

    # ── spelling ↔ sound, from the crosswalk ──
    if not only or only == "spelling":
        _build_spelling(primary, emit, zipf)

    # ── connected speech, hand-authored ──
    if not only or only == "connected":
        _build_connected(emit)

    return dict(out), pending, dict(stats)


def _position_of(phones: list[str], target: str) -> str:
    barephones = bare(phones)
    try:
        i = barephones.index(C.strip_stress(target))
    except ValueError:
        return "medial"
    if C.strip_stress(target) in C.VOWELS:
        return "nucleus"
    if i == 0:
        return "initial"
    if i == len(barephones) - 1:
        return "final"
    return "medial"


def _drop_position(long_phones: tuple, short_phones: tuple, dropped: str) -> str:
    """Which end of the word lost a sound.

    Found by walking to the first divergence, so it reports where the phone
    actually was rather than where a deletion usually is.
    """
    i = 0
    while i < len(short_phones) and long_phones[i] == short_phones[i]:
        i += 1
    if i == 0:
        return "initial"
    if i >= len(long_phones) - 1:
        return "final"
    return "medial"


def _build_deletions(core, primary, freq, emit) -> None:
    by_phone: dict[str, list[tuple]] = defaultdict(list)
    for long_w, short_w, dropped, _i in deletion_pairs(core):
        by_phone[C.strip_stress(dropped)].append((long_w, short_w, dropped))

    for spec in C.TARGET_CONTRASTS:
        if spec["kind"] != "del":
            continue
        cid = C.contrast_id("del", spec["a"])
        rows = sorted(by_phone.get(C.strip_stress(spec["a"]), []))
        rng = random.Random(hashlib.sha256(cid.encode()).hexdigest()[:8])
        rng.shuffle(rows)

        morph = lex = 0
        for long_w, short_w, dropped in rows:
            lp, sp = primary[long_w], primary[short_w]
            morphological = is_inflectional(bare(lp), bare(sp), dropped)
            if morphological and morph >= CAP_SYLLABLE_MORPH:
                continue
            if not morphological and lex >= CAP_SYLLABLE_LEXICAL:
                continue
            # Where the phone actually sits. Asserting "final" for every
            # deletion mislabels trail/rail and terror/error, which are initial
            # — a different and much rarer error than a dropped ending, and one
            # that must not inherit the final-position difficulty bump.
            position = _drop_position(bare(lp), bare(sp), dropped)
            if position != "final" and morphological:
                morphological = False
            if morphological and morph >= CAP_SYLLABLE_MORPH:
                continue
            if not morphological and lex >= CAP_SYLLABLE_LEXICAL:
                continue
            if morphological:
                morph += 1
            else:
                lex += 1
            diff, why = rate_difficulty([freq[long_w], freq[short_w]],
                                        syllables_of(lp), False, position)
            emit(make_deletion_item(cid, spec, long_w, short_w, lp, sp, dropped,
                                    diff, why, [freq[long_w], freq[short_w]],
                                    morphological, verified=False,
                                    position=position), "syllable")
            if morph >= CAP_SYLLABLE_MORPH and lex >= CAP_SYLLABLE_LEXICAL:
                break


def _build_stress(core, primary, freq, emit, variants) -> None:
    rng = random.Random(hashlib.sha256(b"stress").hexdigest()[:8])
    words = sorted(w for w in core if 2 <= syllables_of(primary[w]) <= 4)
    rng.shuffle(words)
    for word in words[: CAP_PER_CONTRAST * 6]:
        phones = primary[word]
        n = syllables_of(phones)
        idx = stress_index(phones)
        if idx < 0 or n < 2 or len(word) < 4:
            continue
        # A word cmudict gives two stress patterns for has no single right
        # answer — REcord and reCORD are both correct, depending on the part of
        # speech. Those belong to a homograph shape with a carrier sentence,
        # which needs hand-written frames; here they would just be unfair.
        if len({stress_index(v) for v in variants.get(word, [phones])}) > 1:
            continue
        diff, _why = rate_difficulty([freq[word]], n, False, "nucleus")
        emit(make_stress_item(word, phones, n, idx, diff, freq[word],
                              verified=False), "stress")


def _build_connected(emit) -> None:
    """The one dimension nothing derives.

    cmudict records citation forms, so it knows nothing about tapping, linking
    or reduction. These items are hand-written in ``connected_data.py`` and are
    marked ``src: "hand"`` and verified at authoring time — the review this
    dimension needs happened when the words were chosen, not afterwards.
    """
    from soundcheck import connected_data as cd  # noqa: PLC0415 — build-time only

    for spoken, full, wrong_a, wrong_b, note in cd.REDUCTIONS:
        stem = f"cs-red-{slug(spoken)}"
        options = [{"id": "a", "label": full},
                   {"id": "b", "label": wrong_a},
                   {"id": "c", "label": wrong_b}]
        emit({
            "id": f"{stem}-sort", "v": 1, "stem_id": stem,
            "dimension": "connected", "shape": "sort",
            "contrast_id": C.contrast_id("reduce", slug(spoken)),
            "phones": [], "difficulty": 3, "why_hard": ["reduction"],
            # The reduced spelling IS the stimulus: a voice given "wanna" says
            # the sound a learner actually meets, where one given "want to"
            # would say the careful form nobody uses.
            "words": [{"text": spoken}],
            "options": options, "answer": ["a"],
            "explain": f"“{spoken}” is “{full}”. {note}",
            "freq": [], "syllables": 0, "position": "medial",
            "merger_sensitive": False, "accent": None,
            "src": "hand", "verified": True, "calibration": False,
            "tags": ["reduction"],
        }, "connected")

    for a, b, same, note in cd.FLAP_PAIRS:
        stem = f"cs-flap-{slug(a)}-{slug(b)}"
        emit({
            "id": f"{stem}-presence", "v": 1, "stem_id": stem,
            "dimension": "connected", "shape": "presence",
            "contrast_id": C.contrast_id("flap", f"{slug(a)}-{slug(b)}"),
            "phones": ["T", "D"], "difficulty": 3, "why_hard": ["flap"],
            # Both words are one stimulus so the voice says them together and
            # the question can be about the pair rather than either word.
            "words": [{"text": f"{a}, {b}"}],
            "prompt": "Do these two sound the same?",
            "options": [{"id": "yes", "label": "The same"},
                        {"id": "no", "label": "Different"}],
            "answer": ["yes" if same else "no"],
            "explain": note,
            "freq": [], "syllables": 0, "position": "medial",
            "merger_sensitive": False, "accent": None,
            "src": "hand", "verified": True, "calibration": False,
            "tags": ["flap", "same" if same else "different"],
        }, "connected")

    for phrase, links, wrong_a, wrong_b, note in cd.LINKING:
        stem = f"cs-link-{slug(phrase)}"
        emit({
            "id": f"{stem}-sort", "v": 1, "stem_id": stem,
            "dimension": "connected", "shape": "sort",
            "contrast_id": C.contrast_id("link", slug(phrase)),
            "phones": [], "difficulty": 2, "why_hard": ["linking"],
            "words": [{"text": phrase}],
            "prompt": "Which sound carries over to the next word?",
            "options": [{"id": "a", "label": f"/{links}/"},
                        {"id": "b", "label": f"/{wrong_a}/"},
                        {"id": "c", "label": f"/{wrong_b}/"}],
            "answer": ["a"],
            "explain": note,
            "freq": [], "syllables": 0, "position": "final",
            "merger_sensitive": False, "accent": None,
            "src": "hand", "verified": True, "calibration": False,
            "tags": ["linking"],
        }, "connected")


def _build_spelling(primary, emit, zipf) -> None:
    from dictionary import crosswalk  # noqa: PLC0415 — build-time only

    graph = crosswalk.crosswalk_graph()
    vowels_by_id = {v["id"]: v for v in crosswalk.VOWELS}
    fan = {g["id"]: g for g in graph["graphemes"]}

    for edge in crosswalk.EDGES:
        g = fan.get(edge["grapheme"])
        if not g or g.get("fan", 0) < 2:
            continue
        vowel = vowels_by_id.get(edge["vowel"]) or {}
        if not vowel.get("arpa"):
            continue
        note = (
            f"⟨{crosswalk.grapheme_label(edge['grapheme'])}⟩ spells "
            f"{g['fan']} different vowels. Here it is /{vowel['ipa']}/ "
            f"as in {vowel['key']}."
        )
        for word in [w.strip().lower() for w in edge["examples"].split(",")]:
            if not word or word not in primary:
                continue
            phones = primary[word]
            vowels = [C.strip_stress(p) for p in phones if p[-1:].isdigit()]
            if vowel["arpa"] not in vowels:
                continue           # cmudict disagrees — the audit reports it
            # Difficulty is the ambiguity of the spelling AND the rarity of the
            # word. Fan alone pinned every ⟨a⟩ item at 5, which rated "about"
            # as hard as anything in the bank.
            z = zipf(word, "en")
            diff, _why = rate_difficulty([z], syllables_of(phones), False, "nucleus")
            diff = min(5, diff + max(0, g["fan"] - 2))
            emit(make_spelling_item(edge["grapheme"], word, phones, edge["vowel"],
                                    g["sounds"], vowels_by_id,
                                    diff, False, note, z), "spelling")


# ── writing ───────────────────────────────────────────────────────

#: Keys used while building but never shipped. `freq` holds wordfreq Zipf
#: values, and wordfreq's *data* is CC BY-SA 4.0: shipping it would bind the
#: bank to share-alike and attribution. Nothing reads it at runtime — it only
#: feeds `rate_difficulty` here — so it stays out of the bank (editions M10).
BUILD_ONLY_KEYS = ("freq",)


def render(items: list[dict]) -> str:
    return "".join(
        json.dumps({k: v for k, v in i.items() if k not in BUILD_ONLY_KEYS},
                   ensure_ascii=False, sort_keys=True) + "\n"
        for i in sorted(items, key=lambda i: i["id"])
    )


def load_curation() -> dict:
    path = BANK_DIR / "curation.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_all(by_dim: dict, pending: list, meta: dict, *, check: bool) -> int:
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    planned: dict[Path, str] = {}
    for dim, items in by_dim.items():
        planned[BANK_DIR / f"{dim}.jsonl"] = render(items)
    for dim in ("vowel", "consonant", "syllable", "stress", "connected", "spelling"):
        planned.setdefault(BANK_DIR / f"{dim}.jsonl", "")
    planned[BANK_DIR / "_pending.jsonl"] = render(pending)
    planned[BANK_DIR / "meta.json"] = json.dumps(meta, indent=2, sort_keys=True) + "\n"

    drift = [p for p, body in planned.items()
             if (p.read_text(encoding="utf-8") if p.is_file() else None) != body]
    if check:
        for p in drift:
            print(f"DRIFT: {p.relative_to(ROOT)}")
        return 1 if drift else 0

    for path, body in planned.items():
        path.write_text(body, encoding="utf-8")
    return 0


def write_license(cmudict_path: Path) -> None:
    src = cmudict_path.parent / "LICENSE"
    dest = BANK_DIR / "CMUDICT-LICENSE.txt"
    if src.is_file():
        BANK_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8", errors="replace"),
                        encoding="utf-8")


# ── entry point ───────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cmudict", help="path to cmudict.dict")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1; write nothing")
    ap.add_argument("--report", action="store_true",
                    help="print the crosswalk cross-audit and exit")
    ap.add_argument("--dimension", help="regenerate one dimension only")
    args = ap.parse_args(argv)

    path = find_cmudict(args.cmudict)
    primary, variants = parse_cmudict(path)
    curation = load_curation()

    audit = audit_crosswalk(primary, curation)
    if args.report:
        print(f"crosswalk audit: {audit['match']} match, "
              f"{len(audit['mismatch'])} mismatch, {len(audit['oov'])} OOV, "
              f"{audit['skipped']} skipped (r-coloured)")
        for m in audit["mismatch"]:
            flag = " " if m["known"] else "*"
            print(f" {flag} {m['grapheme']:6} {m['word']:12} "
                  f"claims {m['claimed']:3} cmudict says {','.join(m['cmudict'])}")
        if audit["oov"]:
            print(f"   not in cmudict: {', '.join(audit['oov'])}")
        return 0

    zipf = _zipf()
    by_dim, pending, stats = build(primary, variants, curation, zipf,
                                   only=args.dimension)

    meta = {
        "generator": "scripts/build_soundcheck_bank.py",
        "generated": date.today().isoformat(),   # a date, not a timestamp
        "cmudict_sha256": sha256_of(path),
        "zipf_core": ZIPF_CORE,
        "zipf_rescue": ZIPF_RESCUE,
        "counts": {d: len(v) for d, v in sorted(by_dim.items())},
        "pending": len(pending),
        "crosswalk_audit": {"match": audit["match"],
                            "mismatch": len(audit["mismatch"]),
                            "new_mismatch": len(audit["new"]),
                            "oov": len(audit["oov"])},
    }

    if audit["new"]:
        print("NEW crosswalk mismatches (add to curation.json audit_known "
              "once reviewed):")
        for m in audit["new"]:
            print(f"  {m['key']}  claims {m['claimed']} "
                  f"cmudict says {','.join(m['cmudict'])}")

    if not args.check:
        write_license(path)

    rc = write_all(by_dim, pending, meta, check=args.check)
    verified = sum(len(v) for v in by_dim.values())
    print(f"{verified} verified · {len(pending)} pending review · "
          f"{len(by_dim)} dimensions")
    if not args.check and pending and not verified:
        print("\nEverything is pending: nothing ships until a human approves it.")
        print("Review bank/_pending.jsonl, then add ids to curation.json "
              "\"approve\" and re-run.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
