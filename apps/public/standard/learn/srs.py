"""Learn — SRS (spaced repetition) review loop on top of KB-sourced quizzes.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: SRS state file (`data/apps/learn/srs.json`), score→quality
mapping, sm2_schedule wrapper, review queue endpoints, streak math,
hub panel + voice intent methods, and the `_generate_quiz_for_slug`
helper that backs both lesson-side quiz generation and per-review
card regeneration.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._resolve_lesson_source`` (app.py),
``self.think`` / ``self.emit`` (BaseApp).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import parse_llm_json, web_route
from emptyos.sdk.srs import sm2_schedule, streak_days

if TYPE_CHECKING:
    from .app import LearnApp  # noqa: F401 — for type hints only


# ─── Bind to LearnApp class as ───────────────────────────────────────
#   _srs_path                  = _srs._srs_path
#   _load_srs                  = _srs._load_srs
#   _save_srs                  = _srs._save_srs                     # async
#   _srs_schedule              = _srs._srs_schedule                 # async
#   _generate_quiz_for_slug    = _srs._generate_quiz_for_slug       # async
#   _enrich_due_card           = _srs._enrich_due_card
#   api_review_due             = _srs.api_review_due                # GET  /api/review/due
#   api_review_next            = _srs.api_review_next               # GET  /api/review/next
#   api_review_grade           = _srs.api_review_grade              # POST /api/review/grade
#   api_review_stats           = _srs.api_review_stats              # GET  /api/review/stats
#   panel_review_due           = _srs.panel_review_due              # hub panel method
#   voice_start_review         = _srs.voice_start_review            # voice intent method
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────


QUIZ_SYSTEM = """You are a precise engineering tutor generating self-check questions.

Rules:
- Generate exactly 3 multiple-choice questions from the provided source text.
- Each question must have exactly 4 options labelled A, B, C, D.
- Exactly one option must be correct.
- Questions test understanding (concepts, procedures, distinctions), NOT verbatim recall.
- Distractors must be plausible — drawn from related-but-wrong concepts, not absurd.
- Output strict JSON only, no prose, no markdown fences.
"""

QUIZ_PROMPT_TEMPLATE = """Source note title: {title}

Source note body:
{body}

Output JSON shape (exactly):
{{
  "questions": [
    {{
      "q": "Question text?",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "A",
      "explanation": "Why A is right, why the others are wrong (1-2 sentences)"
    }},
    ...3 questions total...
  ]
}}"""


# ─── State persistence ──────────────────────────────────────────────


def _srs_path(self) -> Path:
    return self.data_dir / "srs.json"


def _load_srs(self) -> dict:
    f = self._srs_path()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _save_srs(self, data: dict) -> None:
    """Serialised write — delegated to BaseApp.write_lock so two grade
    calls don't race the srs.json file."""
    async with self.write_lock("srs"):
        f = self._srs_path()
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Score → SM-2 quality + scheduling ──────────────────────────────


def _score_to_quality(score: int) -> int:
    """Map quiz score (0-100) → SM-2 quality (0-5)."""
    if score < 33:
        return 0
    if score < 51:
        return 2
    if score < 76:
        return 3
    if score < 91:
        return 4
    return 5


async def _srs_schedule(self, slug: str, score: int) -> dict:
    """Update SRS state for `slug` based on a `score` (0-100). Returns the
    updated entry. Called from `api_submit_quiz` after grading and from
    `api_review_grade` after a review."""
    if not slug:
        return {}
    srs = self._load_srs()
    entry = srs.get(slug) or {
        "ease": 2.5,
        "review_count": 0,
        "next_review": date.today().isoformat(),
    }
    quality = _score_to_quality(score)
    sm2_schedule(entry, quality)
    entry["last_score"] = int(score)
    entry["last_reviewed"] = date.today().isoformat()
    entry["last_quality"] = quality
    srs[slug] = entry
    await self._save_srs(srs)
    asyncio.create_task(self.emit("learn:srs_scheduled", {
        "slug": slug, "score": score, "quality": quality,
        "next_review": entry["next_review"],
    }))
    return entry


# ─── Quiz generation helper (shared by lesson + review paths) ───────


async def _generate_quiz_for_slug(self, slug: str) -> dict:
    """Generate (or cache-fetch) a 3-question MCQ for a KB slug.

    Returns `{slug, title, questions: [...], cached: bool}` on success
    or `{error: str}` on failure. Cache keyed by sha256(slug:body).
    """
    source = await self._resolve_lesson_source(slug)
    if not source or source.get("error") or not source.get("body_md"):
        return {"error": source.get("error") if source else "source not found"}
    body = source["body_md"]
    cache_key = hashlib.sha256(f"{slug}:{body}".encode()).hexdigest()[:16]
    cache_file = self._quiz_cache_dir / f"{cache_key}.json"
    if cache_file.exists():
        try:
            return {"cached": True, **json.loads(cache_file.read_text(encoding="utf-8"))}
        except Exception:
            pass  # fall through and regenerate
    prompt = QUIZ_PROMPT_TEMPLATE.format(title=source.get("title", slug), body=body[:6000])
    raw = await self.think(prompt, system=QUIZ_SYSTEM, domain="text", temperature=0.4)
    parsed = parse_llm_json(raw) or {}
    questions = parsed.get("questions") or []
    if not isinstance(questions, list) or not questions:
        return {"error": "LLM returned no parseable questions", "raw": str(raw)[:500]}
    out = {"slug": slug, "title": source.get("title", slug), "questions": questions}
    try:
        cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"cached": False, **out}


# ─── Streak math ─────────────────────────────────────────────────────


def _enrich_due_card(self, slug: str, entry: dict) -> dict:
    """Add display fields (title, kind, source_path) to an SRS entry."""
    # Synchronous best-effort lookup via VaultIndex (no kb.get_note round-trip
    # — this is for the queue listing, not the quiz body).
    vi = self.kernel.services.get_optional("vault_index")
    title = slug
    kind = ""
    source_path = ""
    if vi:
        for path, fdata in vi._files.items():
            if path.rsplit("/", 1)[-1].replace(".md", "") == slug:
                props = fdata.get("properties", {}) or {}
                title = props.get("title") or slug
                kind = props.get("kind", "")
                source_path = path
                break
    return {
        "slug": slug,
        "title": title,
        "kind": kind,
        "source_path": source_path,
        "next_review": entry.get("next_review", ""),
        "ease": entry.get("ease", 2.5),
        "review_count": entry.get("review_count", 0),
        "last_score": entry.get("last_score"),
        "last_reviewed": entry.get("last_reviewed"),
    }


# ─── HTTP endpoints ─────────────────────────────────────────────────


@web_route("GET", "/api/review/due")
async def api_review_due(self, request):
    """List cards due at or before today, oldest-due first."""
    srs = self._load_srs()
    today = date.today().isoformat()
    due_entries = [
        (slug, entry)
        for slug, entry in srs.items()
        if entry.get("next_review", today) <= today
    ]
    due_entries.sort(key=lambda x: x[1].get("next_review", ""))
    cards = [_enrich_due_card(self, slug, entry) for slug, entry in due_entries]
    return {"cards": cards, "count": len(cards)}


@web_route("GET", "/api/review/next")
async def api_review_next(self, request):
    """Fetch the next due card with its MCQ ready to answer."""
    srs = self._load_srs()
    today = date.today().isoformat()
    due_entries = [
        (slug, entry)
        for slug, entry in srs.items()
        if entry.get("next_review", today) <= today
    ]
    due_entries.sort(key=lambda x: x[1].get("next_review", ""))
    if not due_entries:
        return {"empty": True, "remaining": 0}
    slug, entry = due_entries[0]
    quiz = await self._generate_quiz_for_slug(slug)
    if quiz.get("error"):
        return {"error": quiz["error"], "slug": slug}
    return {
        "slug": slug,
        "title": quiz.get("title", slug),
        "questions": quiz.get("questions", []),
        "cached": quiz.get("cached", False),
        "remaining": len(due_entries),
        "srs": _enrich_due_card(self, slug, entry),
    }


@web_route("POST", "/api/review/grade")
async def api_review_grade(self, request):
    """Grade a review. Accepts either {slug, quality: 0-5} (review-mode
    button click) OR {slug, score: 0-100} (auto-scored MCQ submission)."""
    body = await self.read_json(request)
    slug = (body.get("slug") or "").strip()
    if not slug:
        return {"error": "slug required"}
    quality = body.get("quality")
    score = body.get("score")
    if quality is None and score is None:
        return {"error": "must pass quality (0-5) or score (0-100)"}
    if quality is not None:
        try:
            q = int(quality)
        except (TypeError, ValueError):
            return {"error": "quality must be an integer 0-5"}
        if q < 0 or q > 5:
            return {"error": "quality must be 0-5"}
        # Inverse of _score_to_quality (rough; for emission/logging only).
        approx_score = {0: 10, 1: 25, 2: 45, 3: 65, 4: 85, 5: 100}[q]
        entry = await self._srs_schedule(slug, approx_score)
        entry["graded_quality"] = q
    else:
        try:
            s = int(score)
        except (TypeError, ValueError):
            return {"error": "score must be 0-100"}
        s = max(0, min(100, s))
        entry = await self._srs_schedule(slug, s)
    return {"ok": True, "slug": slug, "entry": entry}


@web_route("GET", "/api/review/stats")
async def api_review_stats(self, request):
    """Aggregate stats for the hub panel + review header."""
    srs = self._load_srs()
    today = date.today().isoformat()
    total = len(srs)
    due_today = sum(1 for e in srs.values() if e.get("next_review", today) <= today)
    reviewed_today = sum(1 for e in srs.values() if e.get("last_reviewed") == today)
    streak = streak_days(e.get("last_reviewed") for e in srs.values())
    unified = await self._unified_due_count()
    return {
        "total_cards": total,
        "due_today": due_today,
        "reviewed_today": reviewed_today,
        "streak_days": streak,
        # Additive: the unified queue's cross-silo counts. The four keys above
        # stay learn-only so existing callers keep their meaning.
        "unified": {"total_due": unified["total"], "counts": unified["counts"]},
    }


# ─── Hub panel ──────────────────────────────────────────────────────


async def panel_review_due(self) -> dict | None:
    """Dashboard stat-tile: cards due today across every SRS silo. None when 0
    (panel drops silently per hub-panels rule).

    One aggregate tile, not one per source — the unified queue is the single
    entry point, so three tiles for one action would be dashboard noise.
    """
    unified = await self._unified_due_count()
    due_today = unified["total"]
    if due_today <= 0:
        return None
    return {
        "label": f"{due_today} due",
        "value": due_today,
        "href": "/learn/#review",
        "icon": "🔁",
    }


# ─── Voice intent ───────────────────────────────────────────────────


async def voice_start_review(self) -> dict:
    """Aura intent: open the unified review queue with a status say."""
    unified = await self._unified_due_count()
    due_today = unified["total"]
    if due_today == 0:
        return {"say": "No cards due for review right now."}
    plural = "card" if due_today == 1 else "cards"
    return {
        "say": f"You have {due_today} {plural} due. Opening review now.",
        "link": {"text": f"Review {due_today} {plural}", "href": "/learn/#review"},
        "card": {
            "renderer": "stat-tile",
            "title": "Review queue",
            "data": {"label": f"{due_today} due", "value": due_today},
        },
    }
