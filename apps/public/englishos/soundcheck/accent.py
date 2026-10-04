"""soundcheck — is this recorded "error" actually an error?

Pure, stdlib-only. Owns: the General American ↔ Australian English split table,
the scorer-artifact detector, and ``classify_pair()`` — the one function that
decides whether a row in ``dictionary/pronounce-pairs.json`` is evidence of a
real weakness, a legitimate accent feature, or noise from the scoring pipeline.

Source of truth for *what counts as a mistake*. Nothing downstream may weight a
confusion pair without passing it through here first.

No ``self``, no I/O — the caller loads the JSON and hands rows in.

## Why this exists

The pronunciation scorer compares against General American (``en-us``, cmudict).
The daily listening environment is Sydney. Those disagree, and the disagreement
is systematic rather than random: in non-rhotic Australian English, dropping the
/r/ in *car* is correct speech, and the scorer records it as a deletion error.
Left unfiltered, an adaptive drill would spend real practice time teaching a
learner to stop doing something their own environment does.

The posture is **target GenAm, tag the splits** — not "score against AusE".
A learner aiming at American English genuinely does need the rhotic /r/; the
game just has to say "this is an Australian feature, not a mistake" instead of
silently marking it wrong.

## The three verdicts

``error``        — a real weakness in both accents. Drill it.
``accent-split`` — correct in one accent, absent in the other. Teach the
                   difference; never score it as a miss.
``artifact``     — the scoring pipeline produced it, the mouth did not.
                   Discard entirely; it is not evidence of anything.

## The artifact detector is general, not a blocklist

``_ipa_to_arpa`` in the pronounce service falls back to ``cleaned.upper()`` when
an emitted IPA glyph is not in its equivalence map. Uppercasing an IPA glyph
yields a symbol that is not a legal ARPABET phone — ``ɐ`` becomes ``Ɐ`` (U+2C6F).
So "not a legal ARPABET phone" IS the artifact signature, and detecting it that
way catches the next such glyph too, rather than only the one already in the
store. Verified against real data: ``AH→Ɐ`` (n=5) was the top recorded pair, and
its alias fix landed 2026-05-16 21:57 — after which it never recurred, while
genuine pairs kept accumulating.
"""

from __future__ import annotations

from .contrasts import PHONES, strip_stress

# The null phone, as written by the pronounce event log for a deletion or the
# reference side of an insertion.
NULL = "∅"


# ── The split table ───────────────────────────────────────────────
# Hand-authored, and says so. Each row names a systematic difference between
# General American and Australian English that shows up in phone-level scoring.
#
# ``ops`` are the (op, ref, hyp) shapes this split explains. ``ref``/``hyp`` of
# None means "any". A row matches when op matches AND both phone slots match.

SPLITS: list[dict] = [
    {
        "id": "rhotic",
        "label": "Post-vocalic R",
        "ga": "pronounced", "aue": "absent",
        "ops": [("del", "R", None)],
        "note": (
            "Australian English is non-rhotic: car, start and nurse have no /r/ "
            "after the vowel. Dropping it is correct here. General American "
            "keeps it, so this is a real difference to learn — not a mistake "
            "you are making."
        ),
        "examples": "car, start, nurse, near",
    },
    {
        "id": "linking-r",
        "label": "Linking and intrusive R",
        "ga": "absent", "aue": "present",
        "ops": [("ins", None, "R")],
        "note": (
            "Australian English joins two vowels with an /r/ — law-r-and-order, "
            "idea-r-of. General American does not. An inserted R between vowels "
            "is an Australian feature, not an error."
        ),
        "examples": "law and order, idea of",
    },
    {
        "id": "weak-vowel-merger",
        "label": "Unstressed schwa and KIT",
        "ga": "distinct", "aue": "merged",
        "ops": [("sub", "AH", "IH"), ("sub", "IH", "AH")],
        "note": (
            "Australian English merges unstressed /ə/ and /ɪ/ — rabbit and abbot "
            "rhyme. General American keeps them apart. In an unstressed "
            "syllable this swap is an accent difference; in a stressed one it "
            "is a real contrast."
        ),
        "examples": "rabbit, abbot, roses, Rosa's",
        "stress_sensitive": True,
    },
    {
        "id": "flapping",
        "label": "The tapped T",
        "ga": "flapped", "aue": "flapped",
        "ops": [("sub", "T", "D"), ("sub", "D", "T")],
        "note": (
            "Both accents tap the /t/ between vowels — water, better and city "
            "all use it. Neither accent treats this as wrong, so scoring it as "
            "a substitution is an artifact of comparing against a citation-form "
            "dictionary."
        ),
        "examples": "water, better, city",
        "intervocalic_only": True,
    },
    {
        "id": "trap-bath",
        "label": "The BATH set",
        "ga": "TRAP", "aue": "variable",
        "ops": [("sub", "AE", "AA"), ("sub", "AA", "AE")],
        "note": (
            "Words like dance, chance and example take /æ/ in General American "
            "but vary by region in Australia. Either is defensible here; only "
            "the American form is defensible in the US."
        ),
        "examples": "dance, chance, example",
    },
]

SPLIT_INDEX: dict[str, dict] = {s["id"]: s for s in SPLITS}


# ── Artifact detection ────────────────────────────────────────────

def is_artifact_symbol(phone: str | None) -> bool:
    """True when a phone slot holds something the scorer invented.

    Legal values are an ARPABET phone, the null marker, or empty. Anything else
    is the ``cleaned.upper()`` fallback in the pronounce service leaking an
    uppercased IPA glyph into the store.
    """
    if phone is None:
        return False
    p = phone.strip()
    if not p or p == NULL:
        return False
    return strip_stress(p) not in PHONES


# ── Classification ────────────────────────────────────────────────

def classify_pair(
    ref: str | None,
    hyp: str | None,
    op: str,
    *,
    stressed: bool | None = None,
    intervocalic: bool | None = None,
) -> dict:
    """Decide what a recorded confusion pair actually is.

    ``stressed`` and ``intervocalic`` are optional context. When a split is
    conditional on them and the caller cannot supply the context, the split
    still matches — deliberately. A false "this might be your accent" is a
    missed drill; a false "this is your mistake" teaches the learner to correct
    something that was never wrong. The second error is worse, so ambiguity
    resolves toward ``accent-split``.

    Returns ``{verdict, split_id, note, confidence}`` where ``confidence``
    reflects whether the context needed to be assumed.
    """
    if is_artifact_symbol(ref) or is_artifact_symbol(hyp):
        bad = hyp if is_artifact_symbol(hyp) else ref
        return {
            "verdict": "artifact",
            "split_id": None,
            "note": (
                f"The symbol {bad!r} is not an ARPABET phone. The scorer emitted "
                "it as a fallback when it could not map an IPA glyph, so this "
                "row describes the pipeline rather than the speaker."
            ),
            "confidence": 1.0,
        }

    r = strip_stress(ref) if ref else None
    h = strip_stress(hyp) if hyp else None

    for split in SPLITS:
        for s_op, s_ref, s_hyp in split["ops"]:
            if op != s_op:
                continue
            if s_ref is not None and r != s_ref:
                continue
            if s_hyp is not None and h != s_hyp:
                continue

            assumed = False
            if split.get("stress_sensitive"):
                if stressed is True:
                    continue          # a stressed swap is a real contrast
                assumed = stressed is None
            if split.get("intervocalic_only"):
                if intervocalic is False:
                    continue
                assumed = assumed or intervocalic is None

            return {
                "verdict": "accent-split",
                "split_id": split["id"],
                "note": split["note"],
                "confidence": 0.6 if assumed else 1.0,
            }

    return {"verdict": "error", "split_id": None, "note": "", "confidence": 1.0}


def classify_row(row: dict) -> dict:
    """Classify one ``pronounce-pairs.json`` row, returning it annotated."""
    verdict = classify_pair(row.get("ref"), row.get("hyp"), row.get("op") or "sub")
    return {**row, **{f"_{k}": v for k, v in verdict.items()}}


def is_drillable(row: dict) -> bool:
    """Only a real error may weight item selection."""
    return classify_pair(
        row.get("ref"), row.get("hyp"), row.get("op") or "sub"
    )["verdict"] == "error"


# ── The read-out ──────────────────────────────────────────────────

def review_pairs(pairs: dict) -> dict:
    """Group a whole ``pronounce-pairs.json`` by verdict, counts included.

    The game's first act: tell the learner which of their recorded errors are
    not errors. Returns counts plus per-verdict rows sorted by count desc.
    """
    buckets: dict[str, list[dict]] = {"error": [], "accent-split": [], "artifact": []}
    for key, row in pairs.items():
        annotated = classify_row({**row, "pair": row.get("pair") or key})
        buckets[annotated["_verdict"]].append(annotated)

    for rows in buckets.values():
        rows.sort(key=lambda r: -int(r.get("count") or 0))

    def total(rows: list[dict]) -> int:
        return sum(int(r.get("count") or 0) for r in rows)

    counted = {k: total(v) for k, v in buckets.items()}
    grand = sum(counted.values()) or 1

    return {
        "rows": buckets,
        "pairs": {k: len(v) for k, v in buckets.items()},
        "occurrences": counted,
        "share": {k: round(v / grand, 3) for k, v in counted.items()},
        "headline": _headline(buckets, counted, grand),
    }


def _headline(buckets: dict, counted: dict, grand: int) -> str:
    """One sentence a human can act on, or silence when there is nothing to say."""
    discountable = counted["accent-split"] + counted["artifact"]
    if not discountable:
        return ""
    pct = round(100 * discountable / grand)
    parts = []
    if counted["artifact"]:
        top = buckets["artifact"][0]["pair"] if buckets["artifact"] else "?"
        parts.append(
            f"{counted['artifact']} came from the scorer rather than your mouth "
            f"(largest: {top})"
        )
    if counted["accent-split"]:
        ids = sorted({r["_split_id"] for r in buckets["accent-split"] if r["_split_id"]})
        labels = ", ".join(SPLIT_INDEX[i]["label"] for i in ids)
        parts.append(
            f"{counted['accent-split']} are Australian English being correct ({labels})"
        )
    return (
        f"{pct}% of your recorded slips are not mistakes — " + "; and ".join(parts) + "."
    )
