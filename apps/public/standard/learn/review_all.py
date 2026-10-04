"""Learn — unified review queue aggregating every SRS silo into one session.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the normalized card envelope, the per-silo rating adapters,
the cross-app gather (learn locally, dictionary + media via ``try_call_app``),
and the three unified endpoints. Learn's own single-source endpoints in
``srs.py`` are untouched — this module composes them, it does not replace them.

Silos share ``emptyos.sdk.srs`` semantics (``next_review`` is a due date), so
aggregating *what is due* is trivial. What differs is the rating scale and the
card identity, which is exactly what the adapters below normalize.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._load_srs`` / ``self._srs_schedule`` /
``self._generate_quiz_for_slug`` / ``_enrich_due_card`` (srs.py, via the bound
methods), ``self.try_call_app`` (BaseApp).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import LearnApp  # noqa: F401 — for type hints only


# ─── Bind to LearnApp class as ───────────────────────────────────────
#   _gather_review_sources = _review_all._gather_review_sources    # async
#   _unified_due_count     = _review_all._unified_due_count        # async
#   api_review_all         = _review_all.api_review_all            # GET  /api/review/all
#   api_review_quiz        = _review_all.api_review_quiz           # GET  /api/review/quiz/{slug}
#   api_review_grade_item  = _review_all.api_review_grade_item     # POST /api/review/grade-item
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


SOURCES = ("learn", "dictionary", "media", "soundcheck", "read-the-room")
OPTIONAL_SOURCES = ("dictionary", "media", "soundcheck", "read-the-room")

# Four silos implement `srs_due`/`srs_grade`, which looks like a duplication
# worth extracting into the SDK. It was measured (2026-08) and deliberately left
# alone: what they share is ~6 lines — the count-only guard, a date filter, a
# sort — while what differs is real. soundcheck additionally filters on
# `trials > 0`; each keys its rows differently (word / contrast / item_id);
# state lives in a flat dict, a nested one, and a separate file. A shared
# helper would need three parameters that exist *only* because the callers
# disagree, which is two functions wearing a trenchcoat. The genuinely common
# part — the schedule itself — is already shared, via `emptyos/sdk/srs.py`.
#
# `dictionary` serves TWO decks through its one contract (vocabulary words and
# the picture packs it absorbed from picture-dict on 2026-08-19). That is the
# app's own business — it tags each row with `kind` and owns the `pic:` id
# prefix that routes a grade back to the right store — so it stays one silo here.
#
# What was missing was not an abstraction but enforcement: the contract is
# pinned by `tests/test_unit_learn_srs_contract.py`, statically, because only
# one of the four exposes it over HTTP.

# Anki-style ratings, mapped onto each silo's native scale.
# The 0-5 scale round-trips through `sdk.srs.quality_to_rating` (<3 again,
# 3 hard, 4 good, 5 easy), so these values land back on the rating they name.
RATING_TO_Q5 = {"again": 1, "hard": 3, "good": 4, "easy": 5}  # media (0-5 quality)
RATING_TO_Q4 = {"again": 1, "hard": 2, "good": 3, "easy": 4}  # dictionary (1-4 ladder)

# learn grades through _srs_schedule, which takes a 0-100 score. Rough inverse
# of `sdk.srs.score_to_rating` — same table api_review_grade already uses.
RATING_TO_SCORE = {"again": 25, "hard": 65, "good": 85, "easy": 100}


# ─── Card envelope ──────────────────────────────────────────────────


def _learn_card(card: dict) -> dict:
    """An enriched learn SRS entry → envelope. Body stays empty: the MCQ is
    generated lazily when the card is reached, never during queue listing."""
    return {
        "app": "learn",
        "id": card.get("slug", ""),
        "kind": "quiz",
        "front": card.get("title") or card.get("slug", ""),
        "back": "",
        "due": card.get("next_review", ""),
        "meta": {
            "kind": card.get("kind", ""),
            "source_path": card.get("source_path", ""),
            "stability": card.get("stability"),
            "review_count": card.get("review_count"),
            "last_score": card.get("last_score"),
        },
    }


def _dictionary_card(card: dict) -> dict:
    """Dictionary serves two decks through one contract: saved vocabulary words
    and picture cards from its shipped packs (absorbed from the retired
    picture-dict app, 2026-08-19). The row says which via ``kind``.

    A picture card is answered *from the photograph* — the image is the prompt,
    not decoration. It rides in ``meta.image`` and ``front`` carries the name
    only as the fallback for a client that cannot render an image; the renderer
    hides the name until reveal so the answer is not sitting next to the
    question. Its ``id`` keeps the ``pic:`` prefix the app assigned, because
    that is what routes the grade back to the picture store rather than to a
    vocabulary word that happens to share the name.
    """
    if card.get("kind") == "picture":
        back = card.get("chinese", "") or ""
        if card.get("hint"):
            back = f"{back}\n\n{card['hint']}" if back else card["hint"]
        return {
            "app": "dictionary",
            "id": card.get("word", ""),
            "kind": "picture",
            "front": card.get("name", ""),
            "back": back,
            "due": card.get("next_review", ""),
            "meta": {
                "image": card.get("image", ""),
                "emoji": card.get("emoji", ""),
                "category": card.get("category", ""),
                "chinese": card.get("chinese", ""),
                "review_count": card.get("review_count", 0),
            },
        }
    back = card.get("definition", "") or ""
    if card.get("example"):
        back = f"{back}\n\n{card['example']}" if back else card["example"]
    return {
        "app": "dictionary",
        "id": card.get("word", ""),
        "kind": "word",
        "front": card.get("word", ""),
        "back": back,
        "due": card.get("next_review", ""),
        "meta": {
            "level": card.get("level", 0),
            "phonetic": card.get("phonetic", ""),
            "chinese": card.get("chinese", ""),
        },
    }


def _media_card(card: dict) -> dict:
    kind = "flashcard" if card.get("_type") == "flashcard" else "highlight"
    if kind == "flashcard":
        front, back = card.get("front", ""), card.get("back", "")
    else:
        front = card.get("text", "")
        back = card.get("note", "") or ""
    source = card.get("source")
    source_title = source.get("title", "") if isinstance(source, dict) else (source or "")
    return {
        "app": "media",
        "id": card.get("id", ""),
        "kind": kind,
        "front": front,
        "back": back,
        "due": card.get("next_review", ""),
        "meta": {
            "source_title": source_title,
            "tags": card.get("tags", []),
            "type": card.get("type", ""),
        },
    }


def _soundcheck_card(card: dict) -> dict:
    """A soundcheck card is a *contrast*, not an item.

    With hundreds of items nothing would ever come due at item level, and the
    memory being tested is "can I still hear these two apart" rather than any
    one word pair. The examples are illustrations of the contrast, so they may
    differ from the pair that was actually missed.
    """
    back = card.get("note", "") or ""
    if card.get("examples"):
        joined = " · ".join(card["examples"])
        back = f"{back}\n\n{joined}" if back else joined
    return {
        "app": "soundcheck",
        "id": card.get("contrast", ""),
        "kind": "sound",
        "front": card.get("label", "") or card.get("contrast", ""),
        "back": back,
        "due": card.get("next_review", ""),
        "meta": {
            "misses": card.get("misses", 0),
            "trials": card.get("trials", 0),
        },
    }


def _read_the_room_card(card: dict) -> dict:
    """A workplace remark, front; how to read it, back.

    ``id`` must be ``file or id`` — that is the key the app's own SRS store uses
    (``_merged`` builds it that way), so anything else grades a row that does not
    exist. The AU reading leads because that is the calibration being drilled;
    the US read is appended only when it was actually annotated and differs, since
    a missing US band means "not annotated" and must never render as agreement.
    """
    reading = card.get("reading") or {}
    au = reading.get("au") or {}
    us = reading.get("us") or {}

    au_label = au.get("label") or card.get("au_band", "")
    parts = []
    if au_label:
        parts.append(f"AU: {au_label}")
    if au.get("why"):
        parts.append(au["why"])
    if au.get("your_move"):
        parts.append(f"Your move: {au['your_move']}")
    us_label = us.get("label") or card.get("us_band", "")
    if us_label and us_label != au_label:
        parts.append(f"US read: {us_label}")

    srs = card.get("srs") or {}
    return {
        "app": "read-the-room",
        "id": card.get("file") or card.get("id", ""),
        "kind": "remark",
        "front": card.get("utterance", ""),
        "back": "\n\n".join(parts),
        "due": srs.get("next_review", ""),
        "meta": {
            "setting": card.get("setting", ""),
            "au_band": card.get("au_band", ""),
            "us_band": card.get("us_band", ""),
            "origin": card.get("origin", ""),
            "review_count": srs.get("review_count", 0),
        },
    }


_MAPPERS = {"learn": _learn_card, "dictionary": _dictionary_card,
            "media": _media_card, "soundcheck": _soundcheck_card,
            "read-the-room": _read_the_room_card}


def _interleave(by_source: dict, limit: int) -> list[dict]:
    """Round-robin across sources so one big deck can't starve the others."""
    out: list[dict] = []
    lists = [by_source.get(name) or [] for name in SOURCES]
    i = 0
    while len(out) < limit and any(i < len(lst) for lst in lists):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
                if len(out) >= limit:
                    break
        i += 1
    return out


# ─── Gather ─────────────────────────────────────────────────────────


def _learn_due(self, limit: int) -> tuple[list[dict], int]:
    """Learn's own due cards, oldest-due first. Local scan, no LLM."""
    srs = self._load_srs()
    today = date.today().isoformat()
    due = [
        (slug, entry)
        for slug, entry in srs.items()
        if entry.get("next_review", today) <= today
    ]
    due.sort(key=lambda x: x[1].get("next_review", ""))
    if limit <= 0:
        return [], len(due)
    cards = [self._enrich_due_card(slug, entry) for slug, entry in due[:limit]]
    return [_learn_card(c) for c in cards], len(due)


async def _gather_review_sources(self, limit: int = 30) -> dict:
    """Collect due cards from every silo. Optional apps fail soft.

    ``counts`` carries each source's *full* due count, not the truncated card
    list — the header chips and hub tile need the real number.

    Every source is asked for ``limit`` cards, not a per-source share: when one
    silo is empty the others must still fill the queue. Interleaving trims the
    surplus. The count-only path (``limit=0``) reads no vault notes at all,
    which is what the hub panel and voice intent use.
    """
    cards, count = _learn_due(self, limit)
    by_source: dict[str, list[dict]] = {"learn": cards}
    counts = {"learn": count}
    sources = {"learn": {"available": True, "error": ""}}

    for name in OPTIONAL_SOURCES:
        res, err = await self.try_call_app(name, "srs_due", limit=limit)
        if err or not isinstance(res, dict):
            by_source[name] = []
            counts[name] = 0
            sources[name] = {"available": False, "error": err or "unexpected response"}
            continue
        mapper = _MAPPERS[name]
        by_source[name] = [mapper(c) for c in (res.get("cards") or [])]
        counts[name] = int(res.get("due_count") or 0)
        sources[name] = {"available": True, "error": ""}

    return {"by_source": by_source, "counts": counts, "sources": sources}


async def _unified_due_count(self) -> dict:
    """Count-only variant for the hub panel + voice intent — reads no vault
    notes and generates no quizzes."""
    _, learn_count = _learn_due(self, 0)
    counts = {"learn": learn_count}
    for name in OPTIONAL_SOURCES:
        res, err = await self.try_call_app(name, "srs_due", limit=0)
        counts[name] = 0 if (err or not isinstance(res, dict)) else int(res.get("due_count") or 0)
    return {"total": sum(counts.values()), "counts": counts}


# ─── HTTP endpoints ─────────────────────────────────────────────────


@web_route("GET", "/api/review/all")
async def api_review_all(self, request):
    """The unified queue: every silo's due cards, interleaved."""
    try:
        limit = int(request.query_params.get("limit", "30"))
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(200, limit))

    gathered = await self._gather_review_sources(limit)
    cards = _interleave(gathered["by_source"], limit)
    return {
        "cards": cards,
        "counts": gathered["counts"],
        "sources": gathered["sources"],
        "total_due": sum(gathered["counts"].values()),
    }


@web_route("GET", "/api/review/quiz/{slug}")
async def api_review_quiz(self, request):
    """Generate the MCQ for one *specific* queued learn card.

    ``/api/review/next`` always picks the oldest-due slug, which is wrong once
    cards are interleaved — the queue decides the order, not the store.
    """
    slug = (request.path_params.get("slug") or "").strip()
    if not slug:
        return {"error": "slug required"}
    return await self._generate_quiz_for_slug(slug)


@web_route("POST", "/api/review/grade-item")
async def api_review_grade_item(self, request):
    """Grade any queued card, routing to its owning silo.

    Body: ``{app, id, rating}`` (rating: again|hard|good|easy) — or, for a
    learn MCQ that auto-scored itself, ``{app: "learn", id, score: 0-100}``.
    """
    body = await self.read_json(request)
    app = (body.get("app") or "").strip()
    item_id = (body.get("id") or "").strip()
    rating = (body.get("rating") or "").strip().lower()
    score = body.get("score")

    if app not in SOURCES:
        return {"error": f"unknown app '{app}'"}
    if not item_id:
        return {"error": "id required"}
    if score is None and rating not in RATING_TO_Q5:
        return {"error": "rating must be one of: again, hard, good, easy"}

    if app == "learn":
        if score is not None:
            try:
                s = max(0, min(100, int(score)))
            except (TypeError, ValueError):
                return {"error": "score must be 0-100"}
        else:
            s = RATING_TO_SCORE[rating]
        entry = await self._srs_schedule(item_id, s)
        return {"ok": True, "app": app, "id": item_id, "next_review": entry.get("next_review", "")}

    if score is not None:
        return {"error": f"'{app}' cards are graded by rating, not score"}

    if app == "dictionary":
        res, err = await self.try_call_app(
            "dictionary", "srs_grade", word=item_id, quality=RATING_TO_Q4[rating]
        )
    elif app == "read-the-room":
        # sm2_schedule, so the 0-5 quality scale — same as media, not the
        # dictionary's 1-4 ladder.
        res, err = await self.try_call_app(
            "read-the-room", "srs_grade", id=item_id, quality=RATING_TO_Q5[rating]
        )
    elif app == "soundcheck":
        res, err = await self.try_call_app(
            "soundcheck", "srs_grade", contrast=item_id, quality=RATING_TO_Q4[rating]
        )
    else:
        res, err = await self.try_call_app(
            "media", "srs_grade", item_id=item_id, quality=RATING_TO_Q5[rating]
        )

    if err:
        return {"error": err}
    if isinstance(res, dict) and res.get("error"):
        return {"error": res["error"]}
    next_review = res.get("next_review", "") if isinstance(res, dict) else ""
    return {"ok": True, "app": app, "id": item_id, "next_review": next_review}
