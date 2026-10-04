"""soundcheck — the item bank: loading it, indexing it, and choosing what comes next.

Owns: JSONL bank loading, the by-dimension/by-contrast indexes, the adaptive
``select_next()`` scoring pass, and the projection from a wrong answer to the
telemetry events it implies. Source of truth for *which question to ask*.

Everything except ``load_bank`` is pure — no ``self``, no I/O, no kernel — so
the adaptive logic is unit-testable without a daemon, which is the same
discipline ``dictionary/crosswalk.py::crosswalk_graph`` follows and the reason
the hardest logic in this app can be tested at all.

## Two decisions worth not re-litigating

**FSRS state is keyed on the contrast, not the item.** With ~1,400 items,
item-level scheduling means nothing is ever due and the review queue is
permanently empty. The thing being remembered is "can I hear FLEECE against
KIT"; an individual word pair is a fresh probe of that memory, and reusing one
probe teaches the probe rather than the contrast. Item ids carry only a
last-seen timestamp, for anti-repeat.

**Interleaving is a filter, not a penalty term.** A penalty can always be
out-voted by a large enough error weight — which is precisely how a
weakness-weighted selector ends up serving twenty identical rounds in a row. A
filter cannot be out-voted, so the anti-repeat rule runs before scoring and
removes candidates outright.

Only *validated* confusion pairs reach the score. A scorer artifact or an
Australian-English feature contributes nothing, which is the entire reason
``accent.py`` is built before this module.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

from emptyos.sdk.srs import retrievability

from . import shared
from .accent import is_drillable
from .contrasts import PHONES as _KNOWN_PHONES, strip_stress


# ── Loading ───────────────────────────────────────────────────────

def load_bank(bank_dir: Path | str, *, include_merger_sensitive: bool = False) -> dict:
    """Read every ``<dimension>.jsonl`` and build the indexes.

    ``_pending.jsonl`` is never read. It exists so a generated-but-unreviewed
    item is committed and diffable while remaining unservable — the runtime
    cannot show a question no human has passed, which is a stronger guarantee
    than remembering to check.
    """
    bank_dir = Path(bank_dir)
    items: list[dict] = []
    if bank_dir.is_dir():
        for path in sorted(bank_dir.glob("*.jsonl")):
            if path.name.startswith("_"):
                continue
            items.extend(_read_jsonl(path))

    if not include_merger_sensitive:
        items = [i for i in items if not i.get("merger_sensitive")]

    return index_items(items)


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue          # one bad line must not lose the file
                if isinstance(row, dict) and row.get("verified"):
                    out.append(row)
    except OSError:
        return []
    return out


def index_items(items: list[dict]) -> dict:
    """Pure — build the lookup structures a selection pass needs."""
    by_dimension: dict[str, list[dict]] = {}
    by_contrast: dict[str, list[dict]] = {}
    by_id: dict[str, dict] = {}
    by_stem: dict[str, list[dict]] = {}

    for item in items:
        by_id[item.get("id", "")] = item
        by_dimension.setdefault(item.get("dimension", ""), []).append(item)
        by_contrast.setdefault(item.get("contrast_id", ""), []).append(item)
        if item.get("stem_id"):
            by_stem.setdefault(item["stem_id"], []).append(item)

    return {
        "items": items,
        "by_dimension": by_dimension,
        "by_contrast": by_contrast,
        "by_id": by_id,
        "by_stem": by_stem,
        "contrasts": sorted(by_contrast),
        "dimensions": sorted(by_dimension),
        "count": len(items),
    }


# ── Telemetry → per-contrast error weight ─────────────────────────

def _recency(stamp: str, today: date | None = None) -> float:
    """Half-life decay, so a slip from March counts for less than one from today."""
    if not stamp:
        return 0.5
    try:
        seen = datetime.fromisoformat(str(stamp)[:10]).date()
    except ValueError:
        return 0.5
    days = max(0, ((today or date.today()) - seen).days)
    return 0.5 ** (days / shared.RECENCY_HALFLIFE_DAYS)


def contrast_phones(cid: str) -> list[str]:
    """The phones a contrast id is about — ``sub:IH/IY`` → ``[IH, IY]``."""
    if ":" not in cid:
        return []
    kind, rest = cid.split(":", 1)
    if kind in ("stress", "grapheme"):
        return []
    return [strip_stress(p) for p in rest.split("/") if p]


def _pair_matches(row: dict, kind: str, phones: set[str]) -> bool:
    """Does this recorded pair count as evidence for this contrast?

    The op must agree, and — for a substitution — *both* sides must be the two
    phones in question. Requiring only one side would let ``IY→EY`` count as
    evidence for ``sub:IH/IY`` merely because it touches IY, which would blur
    every contrast sharing a phone into one undifferentiated blob.
    """
    if (row.get("op") or "sub") != kind:
        return False
    ref = strip_stress(row.get("ref") or "")
    hyp = strip_stress(row.get("hyp") or "")
    if kind == "sub":
        return bool(ref) and bool(hyp) and {ref, hyp} == phones
    # del names the phone that vanished; ins names the one that appeared.
    return (ref or hyp) in phones


def phone_contamination(pairs: dict | None) -> dict[str, float]:
    """What share of each phone's recorded slips came from evidence we discard.

    ``weak-phones.json`` stores a bare scalar per phone with no op, no partner
    and no context, so it **cannot** be run through the accent classifier the
    way a pair can. That is not a small gap: measured on real data, 100% of the
    ``R`` count comes from non-rhotic deletions that are correct Australian
    English, and 67% of ``AH`` comes from a scorer artifact plus the weak-vowel
    merger. Left alone, the per-phone channel launders exactly the evidence the
    pair-level filter just threw out, and ``del:R`` climbs the rankings on it.

    The pairs *do* carry that context, so contamination is measurable: for each
    phone, the fraction of its pair-attributed count that a validated read
    rejects. ``error_weight`` then discounts the phone channel by what is left.
    """
    touched: dict[str, float] = {}
    rejected: dict[str, float] = {}
    for row in (pairs or {}).values():
        count = float(row.get("count") or 0)
        if count <= 0:
            continue
        keep = is_drillable(row)
        for raw in (row.get("ref"), row.get("hyp")):
            if not raw:
                continue
            phone = strip_stress(raw)
            if phone in ("", "∅") or phone not in _KNOWN_PHONES:
                continue
            touched[phone] = touched.get(phone, 0.0) + count
            if not keep:
                rejected[phone] = rejected.get(phone, 0.0) + count

    return {
        phone: shared.clamp(rejected.get(phone, 0.0) / total, 0.0, 1.0)
        for phone, total in touched.items() if total > 0
    }


def error_weight(
    cid: str,
    *,
    weak: dict | None = None,
    pairs: dict | None = None,
    perception: dict | None = None,
    today: date | None = None,
    contamination: dict[str, float] | None = None,
) -> float:
    """Raw (un-normalised) evidence that this contrast is a real problem.

    Three sources, deliberately unequal. Production evidence is what the learner
    actually said wrong. Per-phone counts are weaker, being about one side of a
    pair rather than the confusion itself — and are additionally discounted by
    their measured contamination, since that store cannot be accent-validated on
    its own. Perception evidence is weakest of all: recognising and producing
    are different skills, and weighting our own output at parity is how the
    selector would end up confirming its own priors.
    """
    phones = set(contrast_phones(cid))
    kind = cid.split(":", 1)[0] if ":" in cid else ""
    if contamination is None:
        contamination = phone_contamination(pairs)

    prod = 0.0
    for row in (pairs or {}).values():
        if not is_drillable(row):
            continue                       # artifact or accent — not evidence
        if _pair_matches(row, kind, phones):
            prod += float(row.get("count") or 0) * _recency(row.get("last_seen"), today)

    phone_pull = 0.0
    for phone in phones:
        row = (weak or {}).get(phone)
        if not row:
            continue
        clean = 1.0 - contamination.get(phone, 0.0)
        phone_pull += float(row.get("occurrences") or 0) * _recency(
            row.get("last_seen"), today
        ) * clean

    perc = float((perception or {}).get(cid, {}).get("misses") or 0) * _recency(
        (perception or {}).get(cid, {}).get("last_seen", ""), today
    )

    return (
        shared.PROD_WEIGHT * prod
        + shared.PHONE_WEIGHT * phone_pull
        + shared.PERCEPTION_WEIGHT * perc
    )


def error_pull_map(
    contrasts: list[str],
    *,
    weak: dict | None = None,
    pairs: dict | None = None,
    perception: dict | None = None,
    today: date | None = None,
) -> dict[str, float]:
    """Normalise error weights to 0..1 across the whole bank.

    Log-scaled: the difference between 1 slip and 4 matters far more than the
    difference between 40 and 44, and a linear scale would let one runaway
    contrast crowd out everything else.
    """
    contamination = phone_contamination(pairs)   # measured once, not per contrast
    raw = {
        cid: error_weight(cid, weak=weak, pairs=pairs, perception=perception,
                          today=today, contamination=contamination)
        for cid in contrasts
    }
    top = max(raw.values(), default=0.0)
    if top <= 0:
        return {cid: 0.0 for cid in contrasts}
    denom = math.log1p(top)
    return {cid: math.log1p(v) / denom for cid, v in raw.items()}


# ── Scoring one candidate ─────────────────────────────────────────

def due_boost(cid: str, srs: dict | None, today: date | None = None) -> float:
    """1.0 when never seen or fully forgotten, → 0 when freshly reviewed."""
    row = (srs or {}).get(cid) or {}
    stability = float(row.get("s") or 0)
    if stability <= 0:
        return 1.0
    last = row.get("last_reviewed") or ""
    try:
        seen = datetime.fromisoformat(str(last)[:10]).date()
    except ValueError:
        return 1.0
    elapsed = max(0, ((today or date.today()) - seen).days)
    return shared.clamp(1.0 - retrievability(elapsed, stability), 0.0, 1.0)


def coverage_gap(cid: str, srs: dict | None) -> float:
    """Untested contrasts pull hardest; the pull decays as evidence accumulates."""
    trials = float(((srs or {}).get(cid) or {}).get("trials") or 0)
    return 1.0 / math.sqrt(1.0 + trials)


def score_item(
    item: dict,
    *,
    error_pull: dict[str, float],
    srs: dict | None = None,
    target_difficulty: float = 2.0,
    recent_stems: tuple[str, ...] = (),
    today: date | None = None,
) -> float:
    """The selection formula. Higher wins."""
    cid = item.get("contrast_id", "")
    mismatch = abs(float(item.get("difficulty") or 2) - target_difficulty) / 4.0
    recent = 1.0 if item.get("stem_id") in recent_stems else 0.0

    return (
        shared.W_ERROR_PULL * error_pull.get(cid, 0.0)
        + shared.W_DUE * due_boost(cid, srs, today)
        + shared.W_COVERAGE * coverage_gap(cid, srs)
        + shared.W_DIFFICULTY_FIT * (1.0 - shared.clamp(mismatch, 0.0, 1.0))
        - shared.W_RECENT * recent
    )


# ── The anti-repeat filter ────────────────────────────────────────

def eligible(
    candidates: list[dict],
    *,
    recent_contrasts: list[str],
    seen_items: set[str] | None = None,
) -> list[dict]:
    """Drop candidates that would repeat a contrast too soon.

    A filter rather than a penalty: see the module docstring. If filtering would
    empty the pool the filter relaxes rather than failing — running out of
    questions is a worse outcome than two near neighbours.
    """
    seen_items = seen_items or set()
    window = recent_contrasts[-shared.REPEAT_WINDOW:]
    saturated = {
        cid for cid in set(window)
        if window.count(cid) >= shared.MAX_IN_WINDOW
    }

    def pool(banned: set[str], skip_seen: bool) -> list[dict]:
        return [
            c for c in candidates
            if c.get("contrast_id") not in banned
            and not (skip_seen and c.get("id") in seen_items)
        ]

    # Relax in steps rather than all at once. Dropping straight from the full
    # ban to no ban lets a small pool repeat: block the last two, find nothing,
    # give up entirely, and the heaviest contrast wins three times running. Each
    # rung gives up the least it can, and the last rung still forbids an
    # immediate repeat — the one thing that always reads as broken.
    ladder = [
        set(recent_contrasts[-shared.NO_REPEAT_SLOTS:]) | saturated,
        set(recent_contrasts[-shared.NO_REPEAT_SLOTS:]),
        set(recent_contrasts[-1:]),
    ]
    for banned in ladder:
        for skip_seen in (True, False):
            found = pool(banned, skip_seen)
            if found:
                return found
    return candidates


# ── Selection ─────────────────────────────────────────────────────

def select_next(
    bank: dict,
    *,
    weak: dict | None = None,
    pairs: dict | None = None,
    perception: dict | None = None,
    srs: dict | None = None,
    recent_contrasts: list[str] | None = None,
    recent_stems: tuple[str, ...] = (),
    seen_items: set[str] | None = None,
    dimensions: tuple[str, ...] = (),
    shapes: tuple[str, ...] = (),
    target_difficulty: float = 2.0,
    rng=None,
    today: date | None = None,
) -> dict | None:
    """Pick the next item. Pure; ``rng`` is injected so tests are deterministic.

    Cold start needs no separate branch: with no telemetry every ErrorPull is
    0, every DueBoost is 1 and heat sits at its default, so the formula
    collapses to difficulty fit plus jitter — a sane spread across dimensions.
    One code path, nothing to drift.
    """
    import random as _random

    rng = rng or _random.Random()
    pool = list(bank.get("items") or [])
    if dimensions:
        pool = [i for i in pool if i.get("dimension") in dimensions]
    if shapes:
        pool = [i for i in pool if i.get("shape") in shapes]
    if not pool:
        return None

    pool = eligible(
        pool,
        recent_contrasts=list(recent_contrasts or []),
        seen_items=seen_items,
    )
    if not pool:
        return None

    pull = error_pull_map(
        sorted({i.get("contrast_id", "") for i in pool}),
        weak=weak, pairs=pairs, perception=perception, today=today,
    )

    scored = sorted(
        (
            (
                score_item(
                    item,
                    error_pull=pull,
                    srs=srs,
                    target_difficulty=target_difficulty,
                    recent_stems=recent_stems,
                    today=today,
                ),
                item,
            )
            for item in pool
        ),
        key=lambda pair: -pair[0],
    )

    return _softmax_pick(scored, rng)


def _softmax_pick(scored: list[tuple[float, dict]], rng) -> dict:
    """Sample from the top band rather than taking the argmax.

    Deterministic argmax makes every session identical, which is both boring and
    gameable — the learner starts recognising items instead of sounds.
    """
    top = scored[: shared.SOFTMAX_TOP_N]
    if len(top) == 1:
        return top[0][1]
    best = top[0][0]
    weights = [math.exp((s - best) / shared.SOFTMAX_TEMPERATURE) for s, _ in top]
    total = sum(weights) or 1.0
    roll = rng.random() * total
    upto = 0.0
    for weight, (_, item) in zip(weights, top):
        upto += weight
        if roll <= upto:
            return item
    return top[-1][1]


def plan_order(
    bank: dict,
    n: int,
    *,
    weak: dict | None = None,
    pairs: dict | None = None,
    perception: dict | None = None,
    srs: dict | None = None,
    dimensions: tuple[str, ...] = (),
    shapes: tuple[str, ...] = (),
    rng=None,
    today: date | None = None,
) -> list[str]:
    """Choose ``n`` items up front and freeze the sequence.

    Used by the dual-pass diagnostic, where both passes must ask the same
    questions in the same order — otherwise the disagreement between eye and ear
    measures which items each pass happened to draw rather than the learner.

    Runs the ordinary selector ``n`` times, threading the accumulating history
    through, so a frozen order is subject to exactly the same anti-repeat and
    weighting rules as an adaptive session. Only the *timing* of the decision
    changes, never the decision.
    """
    chosen: list[str] = []
    contrasts: list[str] = []
    stems: list[str] = []
    seen: set[str] = set()

    for _ in range(max(0, int(n))):
        item = select_next(
            bank,
            weak=weak, pairs=pairs, perception=perception, srs=srs,
            recent_contrasts=contrasts,
            recent_stems=tuple(stems),
            seen_items=seen,
            dimensions=dimensions, shapes=shapes,
            rng=rng, today=today,
        )
        if item is None:
            break
        iid = item.get("id", "")
        # ``eligible`` relaxes all the way to "return everything" rather than
        # starving an adaptive session, which means it can hand back an item
        # already used. That is a reasonable answer for a live round and a wrong
        # one here: a frozen pass must be a *set*, or the eye/ear join — which
        # keys on stem_id — would compare a stem against itself.
        if iid in seen:
            break
        chosen.append(iid)
        contrasts.append(item.get("contrast_id", ""))
        stems.append(item.get("stem_id", ""))
        seen.add(iid)
    return chosen


def calibration_set(bank: dict, *, limit: int = shared.CALIBRATION_LENGTH,
                    weak: dict | None = None, pairs: dict | None = None,
                    today: date | None = None) -> list[dict]:
    """The fixed first-session sweep — one item per contrast, no adaptation.

    Ordered by error pull, so even a brand-new session leads with whatever the
    existing telemetry already says is worst. An install with no telemetry at
    all falls back to bank order, which is stable and therefore comparable
    between learners.
    """
    marked = [i for i in (bank.get("items") or []) if i.get("calibration")]
    pool = marked or list(bank.get("items") or [])

    seen: set[str] = set()
    one_each: list[dict] = []
    for item in pool:
        cid = item.get("contrast_id", "")
        if cid in seen:
            continue
        seen.add(cid)
        one_each.append(item)

    pull = error_pull_map(sorted(seen), weak=weak, pairs=pairs, today=today)
    one_each.sort(key=lambda i: (-pull.get(i.get("contrast_id", ""), 0.0),
                                 i.get("id", "")))
    return one_each[:limit]


# ── Answer → telemetry events ─────────────────────────────────────

def events_for_answer(item: dict, picked: list[str], *, correct: bool) -> list[dict]:
    """What a wrong answer implies about which sounds were confused.

    The bank declares this per option in ``miss_maps_to`` rather than the engine
    inferring it, so an ambiguous distractor can simply say nothing. Returns the
    ``{op, ref, hyp, confidence, word}`` shape ``dictionary.log_pronounce_events``
    consumes.

    A correct answer, a timeout, or a miss with no declared mapping all yield
    nothing. Logging a bare "you were wrong" with no identified confusion would
    inflate a phone's count without saying anything about which contrast failed.
    """
    if correct or not picked:
        return []

    mapping = item.get("miss_maps_to") or {}
    confidence = shared.SHAPE_CONFIDENCE.get(item.get("shape", ""), 0.5)
    if confidence <= 0:
        return []

    word = ""
    words = item.get("words") or []
    if words:
        word = words[0].get("text", "")

    events = []
    for choice in picked:
        mapped = mapping.get(choice)
        if not mapped:
            continue
        events.append({
            "op": mapped.get("op", "sub"),
            "ref": mapped.get("ref"),
            "hyp": mapped.get("hyp"),
            "confidence": confidence,
            "word": word,
        })
    return events
