"""Dictionary — SRS (spaced repetition) deck, review, and stats.

Extracted from app.py to keep the core under 800 lines (P4 Atomic).
Methods are bound to DictionaryApp via attribute assignment in app.py.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from emptyos.sdk import load_json, save_json, web_route

SRS_INTERVALS = [0, 1, 3, 7, 14, 30, 60, 120]  # days


# ── Storage helpers ──────────────────────────────────────────

def _srs_path(self) -> Path:
    return self.data_dir / "srs.json"


def _load_srs(self) -> dict:
    return load_json(self._srs_path(), {})


def _save_srs(self, data: dict):
    save_json(self._srs_path(), data)


# ── API: SRS Deck ────────────────────────────────────────────

@web_route("GET", "/api/srs/deck")
async def api_srs_deck(self, request):
    """Build SRS review deck: due words + new words."""
    limit = int(request.query_params.get("limit", "20"))
    srs = self._load_srs()
    vault_words = await self._vault_words()
    today = date.today().isoformat()

    due = []
    for w, entry in srs.items():
        if entry.get("next_review", today) <= today:
            due.append({"word": w, **entry})
    due.sort(key=lambda x: x.get("level", 0))

    in_srs = set(srs.keys())
    new = [{"word": w, "level": 0, "streak": 0, "new": True}
           for w in vault_words if w not in in_srs]
    random.shuffle(new)

    deck = due[:limit]
    remaining = limit - len(deck)
    if remaining > 0:
        deck.extend(new[:remaining])

    return {
        "deck": deck,
        "due_count": len(due),
        "new_count": len(new),
        "total_words": len(vault_words),
    }


# ── API: SRS Review ──────────────────────────────────────────

@web_route("POST", "/api/srs/review")
async def api_srs_review(self, request):
    """Review a word. quality: 1=forgot, 2=hard, 3=good, 4=easy."""
    body = await request.json()
    word = body.get("word", "").strip()
    quality = int(body.get("quality", 3))
    if not word:
        return {"error": "word required"}
    if quality < 1 or quality > 4:
        return {"error": "quality must be 1-4"}

    srs = self._load_srs()
    entry = srs.get(word, {"level": 0, "streak": 0, "reviews": 0})
    today = date.today().isoformat()

    level = entry.get("level", 0)
    streak = entry.get("streak", 0)

    if quality <= 1:
        level = 0
        streak = 0
    elif quality == 2:
        streak = 0
    elif quality == 3:
        level = min(level + 1, 7)
        streak += 1
    else:
        level = min(level + 2, 7)
        streak += 1

    next_review = (date.today() + timedelta(days=SRS_INTERVALS[level])).isoformat()

    entry["level"] = level
    entry["streak"] = streak
    entry["reviews"] = entry.get("reviews", 0) + 1
    entry["next_review"] = next_review
    entry["last_reviewed"] = today

    srs[word] = entry
    self._save_srs(srs)

    await self.emit("dictionary:word_reviewed", {
        "word": word, "quality": quality, "level": level
    })

    return {"ok": True, "word": word, "level": level, "next_review": next_review}


# ── API: SRS Stats ───────────────────────────────────────────

@web_route("GET", "/api/srs/stats")
async def api_srs_stats(self, request):
    """Review statistics."""
    srs = self._load_srs()
    vault_words = await self._vault_words()
    today = date.today().isoformat()

    total = len(srs)
    due = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
    mastered = sum(1 for e in srs.values() if e.get("level", 0) >= 6)
    learning = sum(1 for e in srs.values() if 1 <= e.get("level", 0) <= 5)
    new_count = len(vault_words) - total
    total_reviews = sum(e.get("reviews", 0) for e in srs.values())

    levels = {}
    for e in srs.values():
        lvl = e.get("level", 0)
        levels[lvl] = levels.get(lvl, 0) + 1

    review_dates = sorted(set(
        e.get("last_reviewed", "") for e in srs.values() if e.get("last_reviewed")
    ), reverse=True)
    streak = 0
    check = date.today()
    for rd in review_dates:
        if rd == check.isoformat():
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    return {
        "total_words": len(vault_words),
        "in_srs": total,
        "due_today": due,
        "mastered": mastered,
        "learning": learning,
        "new": new_count,
        "total_reviews": total_reviews,
        "review_streak": streak,
        "levels": levels,
    }
