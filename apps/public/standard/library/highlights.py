"""Library — annotation / highlights on papers, and their optional
spaced-repetition resurfacing.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: appending a highlight to a paper's `## Highlights` vault-note
section. Human-curated content belongs in the vault, not `data/` — this is
the deliberate v1 scope (page + typed quote, not click-drag PDF text
selection; see `apps/public/standard/library/INTENT.md` § Future).

library-no-srs-resurfacing: a highlight can also be opt-in added to a review
queue that reuses the platform's shared FSRS scheduler (`emptyos/sdk/srs.py`)
— the same pure function `learn` already proved out, no new scheduler. The
queue itself (`data/apps/library/highlight-reviews.json`) is machine
telemetry (scheduling state, not human-authored content), so it lives in
`data/`, not the vault — a highlight's own text still only exists once, as
the vault bullet line; the review queue stores a copy for scheduling only,
identified by a fresh id (highlights have no stable id of their own in the
vault, being free-text bullets under `## Highlights`).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.srs import due_items, fsrs_schedule, review_stats
from emptyos.sdk.utils import path_segment_error

if TYPE_CHECKING:
    from .app import LibraryApp  # noqa: F401 — for type hints only


# ─── Bind to LibraryApp class as ──────────────────────────────────────────
#   api_add_highlight = _highlights.api_add_highlight
#   api_review_due     = _highlights.api_review_due
#   api_review_grade   = _highlights.api_review_grade
#   api_review_stats   = _highlights.api_review_stats
#   _reviews_path       = _highlights._reviews_path
#   _load_reviews       = _highlights._load_reviews
#   _save_reviews       = _highlights._save_reviews
#   proactive_source_reviews = _highlights.proactive_source_reviews
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────────


def _escape_line(text: str) -> str:
    """Keep a highlight/quote/note on a single markdown bullet line."""
    return " ".join((text or "").split())


@web_route("POST", "/api/papers/{citekey}/highlight")
async def api_add_highlight(self, request):
    """POST /api/papers/{citekey}/highlight {page, quote, note?, review?} —
    appends one line to the paper's `## Highlights` section:
    `- p.<page> — "<quote>" — <note> (<date>)`. When `review` is truthy,
    also creates a spaced-repetition review-queue entry for this highlight
    (due immediately), returned as `review_id`."""
    citekey = request.path_params.get("citekey", "")
    err = path_segment_error(citekey, "paper id")
    if err:
        return {"error": err}

    item = self.papers.detail(f"{citekey}.md")
    if not item:
        return {"error": "not found"}

    body = await request.json()
    page = body.get("page", "")
    quote = _escape_line(body.get("quote", ""))
    note = _escape_line(body.get("note", ""))
    if not quote and not note:
        return {"error": "quote or note required"}

    date_str = date.today().isoformat()
    bits = []
    if page:
        bits.append(f"p.{page}")
    if quote:
        bits.append(f'"{quote}"')
    if note:
        bits.append(note)
    line = "- " + " — ".join(bits) + f" ({date_str})"

    self.vault_append_section(f"{self.papers_dir()}/{citekey}.md", "Highlights", line)
    await self.emit("library:highlight_added", {"citekey": citekey, "page": page})

    review_id = None
    if body.get("review"):
        reviews = self._load_reviews()
        review_id = uuid.uuid4().hex[:12]
        reviews[review_id] = {
            "citekey": citekey,
            "page": page,
            "quote": quote,
            "note": note,
            "added": date_str,
            "next_review": date_str,  # due immediately — a fresh card
            "review_count": 0,
        }
        await self._save_reviews(reviews)

    return {"ok": True, "review_id": review_id}


# ─── Spaced-repetition resurfacing (library-no-srs-resurfacing) ──────────


def _reviews_path(self):
    return self.data_dir / "highlight-reviews.json"


def _load_reviews(self) -> dict:
    f = self._reviews_path()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _save_reviews(self, data: dict) -> None:
    """Serialised write — matches learn/srs.py's write_lock discipline so
    two grade calls (or an add-highlight racing a grade) don't tear the file."""
    async with self.write_lock("library-highlight-reviews"):
        self._reviews_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@web_route("GET", "/api/review/due")
async def api_review_due(self, request):
    """Highlights due for review, oldest-due first."""
    reviews = self._load_reviews()
    today = date.today().isoformat()
    entries = [{"id": rid, **item} for rid, item in reviews.items()]
    due = due_items(entries, today)
    due.sort(key=lambda x: x.get("next_review", ""))
    return {"items": due, "count": len(due)}


@web_route("POST", "/api/review/{review_id}/grade")
async def api_review_grade(self, request):
    """POST /api/review/{review_id}/grade {rating: again|hard|good|easy} —
    advances the highlight's FSRS schedule via the shared scheduler."""
    review_id = request.path_params.get("review_id", "")
    body = await request.json()
    rating = str(body.get("rating") or "").strip().lower()
    if rating not in ("again", "hard", "good", "easy"):
        return {"error": "rating must be one of again/hard/good/easy"}
    reviews = self._load_reviews()
    item = reviews.get(review_id)
    if not item:
        return {"error": "review item not found"}
    fsrs_schedule(item, rating)
    reviews[review_id] = item
    await self._save_reviews(reviews)
    await self.emit("library:highlight_reviewed", {"id": review_id, "rating": rating})
    return {"ok": True, "id": review_id, "item": item}


@web_route("GET", "/api/review/stats")
async def api_review_stats(self, request):
    reviews = self._load_reviews()
    return review_stats(list(reviews.values()))


async def proactive_source_reviews(self) -> list[dict]:
    """`[[contributes.proactive.source]]` — highlights due for spaced-repetition
    review (proactive-source-slot: mirrors `[[contributes.hub.panel]]`, letting
    an app opt a pull-source into the proactive scan without proactive/app.py
    having to know library exists). Fail-soft: returns [] on any read error so
    a broken reviews file can't break the scan for other sources."""
    try:
        reviews = self._load_reviews()
    except Exception:
        return []
    if not reviews:
        return []
    today = date.today().isoformat()
    entries = [{"id": rid, **item} for rid, item in reviews.items()]
    due = due_items(entries, today)
    if not due:
        return []
    n = len(due)
    return [{
        "kind": "srs-review",
        "text": f"{n} highlight{'s' if n != 1 else ''} due for review.",
        "urgency": "normal",
        "dedup_key": f"library-srs-review:{today}",  # 24h TTL → once/day while due
        "link": {"text": "Review highlights", "href": "/library/"},
    }]
