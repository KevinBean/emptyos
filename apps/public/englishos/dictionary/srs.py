"""Dictionary — SRS (spaced repetition) deck, review, and stats.

Extracted from app.py to keep the core under 800 lines (P4 Atomic).
Methods are bound to DictionaryApp via attribute assignment in app.py.

``srs_due`` / ``srs_grade`` are the cross-app contract the learn app's unified
review queue calls via ``call_app`` (plain kwargs, no ``request``). The HTTP
routes delegate to them so there is one implementation of the ladder.
"""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from emptyos.sdk import load_json, save_json, web_route
from emptyos.sdk.srs import RATINGS, fsrs_schedule, streak_days

from .shared import definition_of, display_level, due_card_key, thread_sequence

# Dictionary's HTTP route and learn both speak a 1-4 quality, and those numbers
# are exactly RATINGS' values — so the inverse is exact and is derived from the
# SDK rather than restated. (Do NOT reach for sdk.srs.quality_to_rating here:
# that converts the *0-5 SM-2* scale, where 3 means "hard", not "good".)
_QUALITY_TO_RATING = {v: k for k, v in RATINGS.items()}

# A word is treated as known once FSRS says it will still be recalled three
# weeks out. Replaces the old level>=6 test, which indexed a fixed ladder.
MASTERED_STABILITY_DAYS = 21.0

# Picture cards ride the same unified queue as words. The prefix is what keeps
# the two stores apart — a pack slug and a saved word can be the same string.
PIC_PREFIX = "pic:"



# ── Storage helpers ──────────────────────────────────────────


def _srs_path(self) -> Path:
    return self.data_dir / "srs.json"


def _load_srs(self) -> dict:
    return load_json(self._srs_path(), {})


def _save_srs(self, data: dict):
    save_json(self._srs_path(), data)


# ── Card context ─────────────────────────────────────────────
#
# `save_word` persists the sentence a word was met in and the source URL it came
# from (timestamped, when the source was a video). Until this existed the deck
# was built from `{word, level, streak}` alone, so every one of those was
# captured, written to the vault, and then dropped at the moment of review.


def _card_context_on(self) -> bool:
    return bool(self.app_config("feature.card-context.enabled", False))


def _threads_on(self) -> bool:
    return bool(self.app_config("feature.word-threads.enabled", False))


async def _card_context(self, word: str) -> dict:
    """Sentence / definition / phonetic / source for one card. Never raises —
    a card missing its context is still a reviewable card.

    Goes through `_vault_as_lookup`, the one reshaper that reads BOTH note
    layouts. Reading senses directly here looked equivalent and was not: the
    deck is overwhelmingly legacy `## Example` notes, so a sense-only read
    returned an empty sentence for ~87% of a real vault while the same word
    showed its example fine on the word page.
    """
    data = await self._read_vault_word(word)
    if not data:
        return {}
    parsed = self._vault_as_lookup(data) or {}
    meta = data.get("meta") or {}
    return {
        "definition": parsed.get("definition") or definition_of(data),
        "sentence": parsed.get("example", ""),
        "phonetic": meta.get("phonetic", "") or parsed.get("phonetic", ""),
        "source": meta.get("source") or "",
    }


def _order_new(self, words: list[str]) -> list[str]:
    """Introduction order for words not yet in SRS.

    Thread-aware when enabled: `random.shuffle` is what guaranteed a word family
    would be split across sessions, since every member had an equal chance of
    landing in a different day's deck. Frontmatter comes from the in-memory
    VaultIndex, so ordering N words costs no file reads.
    """
    if not self._threads_on():
        shuffled = list(words)
        random.shuffle(shuffled)
        return shuffled
    props_by_word: dict[str, dict] = {}
    for row in self.vault_query(tags=["vocabulary"]) or []:
        props = row.get("properties") or {}
        name = str(props.get("word") or row.get("name") or "").strip()
        if name:
            props_by_word[name] = props
    return thread_sequence([{"word": w, "meta": props_by_word.get(w, {})} for w in words])


# ── API: SRS Deck ────────────────────────────────────────────


@web_route("GET", "/api/srs/deck")
async def api_srs_deck(self, request):
    """Build SRS review deck: due words + new words."""
    limit = int(request.query_params.get("limit", "20"))
    srs = self._load_srs()
    vault_words = await self._vault_words()
    today = date.today().isoformat()

    hard = self.difficulty_map()
    due = []
    for w, entry in srs.items():
        if entry.get("next_review", today) <= today:
            # `level` is a derived display figure for the UI's dots, not stored
            # state — see shared.display_level.
            due.append({"word": w, **entry, "level": display_level(entry),
                        "difficulty": hard.get(w, 0)})
    due.sort(key=due_card_key)      # overdue first, then hardest — see shared.py

    in_srs = set(srs.keys())
    new = [
        {"word": w, "level": 0, "new": True, "difficulty": hard.get(w, 0)}
        for w in self._order_new([w for w in vault_words if w not in in_srs])
    ]

    deck = due[:limit]
    remaining = limit - len(deck)
    if remaining > 0:
        deck.extend(new[:remaining])

    # Only the cards actually being served — enriching `new` would read a note
    # per word for words nobody is about to see.
    if self._card_context_on():
        deck = [{**card, **await self._card_context(card["word"])} for card in deck]

    return {
        "deck": deck,
        "due_count": len(due),
        "new_count": len(new),
        "total_words": len(vault_words),
    }


# ── Cross-app contract (learn's unified review queue) ────────


async def srs_due(self, limit: int = 20) -> dict:
    """Due cards only — no new-word introduction (that stays in the deck UI).

    Serves **both** decks this app owns: saved vocabulary words, and picture
    cards from the shipped packs. Picture rows carry ``kind: "picture"`` and a
    ``pic:``-prefixed word so ``srs_grade`` can route them back to the right
    store; word rows keep the shape they have always had.

    ``limit <= 0`` means count-only: ``cards`` is empty and no vault notes are
    read, which is what the hub-panel / voice count path wants.
    """
    srs = self._load_srs()
    today = date.today().isoformat()

    # Sorted below, once the count-only path has had its chance to bail: the
    # interleave relies on oldest-overdue-first, the count does not.
    due = [{"word": w, **e} for w, e in srs.items() if e.get("next_review", today) <= today]

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20

    if limit <= 0:
        # Count-only (the hub panel and the voice "how many are due" verb). It
        # wants a length, so neither the rating lookup nor the sort is attached
        # here — the map walks the whole vocabulary corpus, and this path is the
        # frequently-called one.
        pics = await self.picture_due(limit=0)
        return {"cards": [], "due_count": len(due) + int(pics.get("due_count", 0))}

    hard = self.difficulty_map()
    for card in due:
        card["difficulty"] = hard.get(card["word"], 0)
    due.sort(key=due_card_key)      # overdue first, then hardest — see shared.py

    pics = await self.picture_due(limit=limit)
    pic_cards = pics.get("cards") or []

    # Interleave the two decks rather than merging them by date, then cut to
    # `limit` BEFORE hydrating so a word card that loses the cut never costs a
    # vault read.
    #
    # Round-robin, not a date sort. Before the merge these were two silos and
    # learn's own `_interleave` guaranteed each a slot; folding them into one
    # app put them in one slot, and a pure date sort then starved pictures
    # completely — a deck of words overdue since April fills any sane limit
    # before a card due today. Each deck stays oldest-first internally.
    pics_sorted = sorted(pic_cards, key=lambda x: x.get("next_review") or "")
    merged = []
    for i in range(max(len(due), len(pics_sorted))):
        if i < len(due):
            merged.append(("word", due[i]))
        if i < len(pics_sorted):
            merged.append(("picture", pics_sorted[i]))
    merged = merged[:limit]

    cards = []
    for kind, row in merged:
        if kind == "picture":
            cards.append({
                "kind": "picture",
                "word": PIC_PREFIX + row["slug"],
                "name": row["name"],
                "chinese": row.get("chinese", ""),
                "hint": row.get("hint", ""),
                "category": row.get("category", ""),
                "emoji": row.get("emoji", ""),
                "image": row.get("image", ""),
                "next_review": row.get("next_review", today),
                "review_count": row.get("review_count", 0),
            })
            continue
        word = row["word"]
        data = await self._read_vault_word(word)
        meta = (data or {}).get("meta") or {}
        # Same reshaper as the deck and the word page. `example` is not a
        # frontmatter field at all, so the old `meta.get("example")` read was
        # dead in every layout, and learn's unified queue showed bare words.
        parsed = (self._vault_as_lookup(data) if data else None) or {}
        cards.append(
            {
                "kind": "word",
                "word": word,
                "definition": parsed.get("definition") or definition_of(data),
                "example": parsed.get("example", ""),
                "source": meta.get("source") or "",
                "chinese": meta.get("chinese", ""),
                "phonetic": meta.get("phonetic", ""),
                "next_review": row.get("next_review", today),
            }
        )
    return {"cards": cards, "due_count": len(due) + int(pics.get("due_count", 0))}


async def srs_grade(self, word: str, quality: int = 0, rating: str = "") -> dict:
    """Grade one card from the unified queue.

    ``word`` is either a vocabulary word or a ``pic:<slug>`` picture card —
    the prefix is what keeps the two decks apart, because a pack slug and a
    saved word can be the same string (``tiger``) and one flat store would let
    them overwrite each other.

    Accepts either the 1-4 ``quality`` (1=forgot, 2=hard, 3=good, 4=easy — what
    the HTTP route and learn send) or the ``again|hard|good|easy`` ``rating``
    vocabulary directly. ``rating`` wins when both are given.

    Scheduling is ``sdk.srs.fsrs_schedule``, as in every other silo that feeds
    learn. The old fixed ladder (SRS_INTERVALS, indexed by a ``level`` counter)
    was dictionary's alone and is gone. Existing rows are NOT migrated: FSRS
    treats an entry with no ``s`` as a first exposure and seeds it on this
    review, so every saved word keeps the ``next_review`` it already had.
    """
    word = (word or "").strip()
    if not word:
        return {"error": "word required"}

    if rating:
        rating = str(rating).lower()
        if rating not in RATINGS:
            return {"error": f"rating must be one of {', '.join(RATINGS)}"}
    else:
        try:
            quality = int(quality)
        except (TypeError, ValueError):
            return {"error": "quality must be 1-4"}
        if quality < 1 or quality > 4:
            return {"error": "quality must be 1-4"}
        rating = _QUALITY_TO_RATING[quality]

    if word.startswith(PIC_PREFIX):
        return await self.picture_grade(word[len(PIC_PREFIX):], rating)

    srs = self._load_srs()
    entry = srs.get(word, {})
    fsrs_schedule(entry, rating)
    srs[word] = entry
    self._save_srs(srs)

    await self.emit("dictionary:word_reviewed",
                    {"word": word, "rating": rating, "quality": RATINGS[rating]})

    return {"ok": True, "word": word,
            "next_review": entry.get("next_review", ""),
            "review_count": int(entry.get("review_count", 0))}


# ── API: SRS Review ──────────────────────────────────────────


@web_route("POST", "/api/srs/review")
async def api_srs_review(self, request):
    """Review a word. quality: 1=forgot, 2=hard, 3=good, 4=easy."""
    body = await request.json()
    return await self.srs_grade(word=body.get("word", ""), quality=body.get("quality", 3))


# ── API: SRS Stats ───────────────────────────────────────────


@web_route("GET", "/api/srs/stats")
async def api_srs_stats(self, request):
    """Review statistics."""
    srs = self._load_srs()
    vault_words = await self._vault_words()
    today = date.today().isoformat()

    def _stability(e: dict) -> float | None:
        try:
            v = e.get("s")
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    total = len(srs)
    due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
    # Buckets read FSRS stability, not the retired `level` ladder. A row that has
    # never been graded under FSRS has no `s` yet and counts as still learning
    # rather than mastered — the conservative direction.
    mastered = sum(1 for e in srs.values()
                   if (_stability(e) or 0.0) >= MASTERED_STABILITY_DAYS)
    learning = total - mastered
    new_count = len(vault_words) - total
    total_reviews = sum(int(e.get("review_count", e.get("reviews", 0)) or 0)
                        for e in srs.values())

    # Stability bands, replacing the old per-level histogram. Same shape (a
    # dict the UI charts), same purpose — how far through the deck the learner is.
    bands = {"new": 0, "days": 0, "weeks": 0, "months": 0}
    for e in srs.values():
        st = _stability(e)
        if st is None:
            bands["new"] += 1
        elif st < 7:
            bands["days"] += 1
        elif st < MASTERED_STABILITY_DAYS:
            bands["weeks"] += 1
        else:
            bands["months"] += 1

    streak = streak_days(e.get("last_reviewed", "") for e in srs.values())

    # Only 4-5 stars. A 1-2 star word is rated, not struggling, and counting every
    # rated word here would make the number grow simply because the reader used
    # the feature — which is the opposite of what it is meant to tell them.
    hard = self.difficulty_map()
    hardest = sum(1 for rating in hard.values() if rating >= 4)

    return {
        "total_words": len(vault_words),
        "rated": len(hard),
        "hard": hardest,
        "in_srs": total,
        "due_today": due,
        "mastered": mastered,
        "learning": learning,
        "new": new_count,
        "total_reviews": total_reviews,
        "review_streak": streak,
        "stability_bands": bands,
    }
