"""Learn — diagnostic-first personalization loop (dark-flagged).

A **diagnostic** is an upfront placement test spanning a course's distinct
read-lesson concepts. The user answers; each concept (slug) is scored into a
mastery level (mastered / developing / weak); a **personalized plan** is then
proposed that drops mastered concepts, keeps developing ones, and front-loads
the weak ones. Applying the plan rewrites the course's `lessons_json` so the
syllabus is reshaped around what the user doesn't yet know.

Reuses ``_generate_quiz_for_slug`` (srs.py) per concept — same cache + prompt
path as lesson quizzes, so a diagnostic costs nothing extra once the per-slug
quizzes are cached. Mastery is stored in the per-course progress JSON
(``data/apps/learn/progress/<course-id>.json``) under ``mastery`` /
``diagnostic`` — course-scoped, the right granularity for reshaping *this*
course (SRS state stays global per-slug and is still fed on submit).

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``self._find_course`` / ``self._load_progress`` /
``self._save_progress`` (app.py), ``self._generate_quiz_for_slug`` /
``self._srs_schedule`` (srs.py), ``self.think`` / ``self.emit`` (BaseApp).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

from .shared import _parse_lessons  # leaf module — no cycle (unlike .app)

if TYPE_CHECKING:
    from .app import LearnApp  # noqa: F401 — for type hints only


# ─── Bind to LearnApp class as ───────────────────────────────────────
#   _diagnostic_enabled    = _diagnostic._diagnostic_enabled
#   _diagnostic_slugs      = _diagnostic._diagnostic_slugs
#   _mastery_level         = _diagnostic._mastery_level          # @staticmethod
#   _compute_plan          = _diagnostic._compute_plan
#   api_diagnostic_status  = _diagnostic.api_diagnostic_status   # GET  /api/courses/{id}/diagnostic
#   api_diagnostic_generate= _diagnostic.api_diagnostic_generate # POST /api/courses/{id}/diagnostic
#   api_diagnostic_submit  = _diagnostic.api_diagnostic_submit   # POST /api/courses/{id}/diagnostic/submit
#   api_diagnostic_apply   = _diagnostic.api_diagnostic_apply    # POST /api/courses/{id}/diagnostic/apply
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

# Mastery thresholds (per-concept score 0-100).
_MASTERED_AT = 80
_DEVELOPING_AT = 40

# Defaults for diagnostic shape.
_DEFAULT_PER_SLUG = 2     # questions sampled per concept
_DEFAULT_MAX_SLUGS = 6    # cap distinct concepts probed (keeps the test short)


def _diagnostic_enabled(self) -> bool:
    """Dark-default gate. Settings service first (⚙ panel toggle, live),
    then emptyos.toml. Mirrors `_tutorial_enabled` in app.py."""
    v = self.setting("learn.feature.diagnostic.enabled", None)
    if v is not None:
        return bool(v)
    return bool(self.app_config("feature.diagnostic.enabled", False))


def _diagnostic_slugs(self, course: dict) -> list[str]:
    """Distinct read-lesson slugs of a course, in syllabus order.

    A `read` lesson references the concept the diagnostic probes; quiz
    lessons reuse the same slug, so we dedupe to one entry per concept.
    """
    seen: list[str] = []
    for lesson in _parse_lessons(course["fm"]):
        slug = (lesson.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        # Probe concepts the player can actually read — `read` lessons (and
        # any lesson with a slug if no kind is set).
        kind = (lesson.get("kind") or "read").strip().lower()
        if kind in ("read", ""):
            seen.append(slug)
    return seen


def _mastery_level(score: int) -> str:
    if score >= _MASTERED_AT:
        return "mastered"
    if score >= _DEVELOPING_AT:
        return "developing"
    return "weak"


def _compute_plan(self, course: dict, mastery: dict) -> list[dict]:
    """Reshape the course's lessons around mastery.

    weak concepts first, developing next, mastered dropped. Each kept
    concept keeps *all* its original lessons (read + quiz), preserving
    their titles/durations. Concepts the diagnostic never probed (not in
    `mastery`) are treated as `developing` so nothing is silently lost.
    """
    order = {"weak": 0, "developing": 1, "mastered": 2}
    # Group original lessons by slug, preserving order within a group.
    groups: dict[str, list[dict]] = {}
    group_order: list[str] = []
    for lesson in _parse_lessons(course["fm"]):
        slug = (lesson.get("slug") or "").strip()
        if not slug:
            continue
        if slug not in groups:
            groups[slug] = []
            group_order.append(slug)
        groups[slug].append(lesson)

    def _rank(slug: str) -> tuple[int, int]:
        level = (mastery.get(slug) or {}).get("level", "developing")
        return (order.get(level, 1), group_order.index(slug))

    plan: list[dict] = []
    for slug in sorted(group_order, key=_rank):
        level = (mastery.get(slug) or {}).get("level", "developing")
        if level == "mastered":
            continue  # already known — skip
        plan.extend(groups[slug])
    return plan


# ─── HTTP endpoints ─────────────────────────────────────────────────


@web_route("GET", "/api/courses/{course_id}/diagnostic")
async def api_diagnostic_status(self, request):
    """Stored diagnostic result + last proposed plan for a course."""
    if not self._diagnostic_enabled():
        return {"enabled": False}
    course_id = request.path_params["course_id"]
    course = self._find_course(course_id)
    if not course:
        return {"error": "course not found"}
    progress = self._load_progress(course_id)
    diag = progress.get("diagnostic") or {}
    return {
        "enabled": True,
        "course_id": course_id,
        "taken_at": diag.get("taken_at"),
        "mastery": progress.get("mastery") or {},
        "concept_count": len(self._diagnostic_slugs(course)),
        "personalized": bool(course["fm"].get("personalized")),
    }


@web_route("POST", "/api/courses/{course_id}/diagnostic")
async def api_diagnostic_generate(self, request):
    """Generate a placement test spanning the course's concepts.

    Reuses the per-slug quiz cache; samples up to `per_slug` questions from
    each of up to `max_slugs` concepts. Each question carries its `slug` so
    submit can score per concept.
    """
    if not self._diagnostic_enabled():
        return {"error": "disabled", "hint": "enable learn.feature.diagnostic.enabled in Settings"}
    course_id = request.path_params["course_id"]
    course = self._find_course(course_id)
    if not course:
        return {"error": "course not found"}
    body = await self.read_json(request) if request is not None else {}
    try:
        per_slug = max(1, min(3, int(body.get("per_slug") or _DEFAULT_PER_SLUG)))
    except (TypeError, ValueError):
        per_slug = _DEFAULT_PER_SLUG
    try:
        max_slugs = max(1, min(12, int(body.get("max_slugs") or _DEFAULT_MAX_SLUGS)))
    except (TypeError, ValueError):
        max_slugs = _DEFAULT_MAX_SLUGS

    slugs = self._diagnostic_slugs(course)[:max_slugs]
    if not slugs:
        return {"error": "course has no readable concepts to diagnose"}

    # Generate per-slug quizzes concurrently (each hits the shared cache).
    quizzes = await asyncio.gather(
        *[self._generate_quiz_for_slug(s) for s in slugs],
        return_exceptions=True,
    )
    questions: list[dict] = []
    probed: list[str] = []
    for slug, quiz in zip(slugs, quizzes):
        if isinstance(quiz, Exception) or not isinstance(quiz, dict) or quiz.get("error"):
            continue
        qs = quiz.get("questions") or []
        if not qs:
            continue
        probed.append(slug)
        for q in qs[:per_slug]:
            if not isinstance(q, dict) or not q.get("q"):
                continue
            questions.append({**q, "slug": slug, "concept": quiz.get("title", slug)})
    if not questions:
        return {"error": "could not generate any diagnostic questions"}
    return {
        "course_id": course_id,
        "concepts": probed,
        "concept_count": len(probed),
        "question_count": len(questions),
        "questions": questions,
    }


@web_route("POST", "/api/courses/{course_id}/diagnostic/submit")
async def api_diagnostic_submit(self, request):
    """Score a diagnostic per concept, store mastery, propose a plan.

    Body: {answers: {globalIndex: "A"}, questions: [{slug, answer, ...}]}.
    Does NOT mutate the course — `apply` does. Feeds SRS per concept so the
    diagnostic seeds the review queue exactly like a lesson quiz.
    """
    if not self._diagnostic_enabled():
        return {"error": "disabled"}
    course_id = request.path_params["course_id"]
    course = self._find_course(course_id)
    if not course:
        return {"error": "course not found"}
    body = await self.read_json(request)
    answers = body.get("answers") or {}
    questions = body.get("questions") or []
    if not questions:
        return {"error": "questions array required"}

    # Tally correct/total per concept slug.
    per_slug: dict[str, dict] = {}
    details: list[dict] = []
    for i, q in enumerate(questions):
        slug = (q.get("slug") or "").strip()
        if not slug:
            continue
        picked = answers.get(str(i)) or answers.get(i)
        right = (q.get("answer") or "").strip().upper()
        ok = (picked or "").strip().upper() == right
        bucket = per_slug.setdefault(slug, {"correct": 0, "total": 0})
        bucket["total"] += 1
        if ok:
            bucket["correct"] += 1
        details.append({"index": i, "slug": slug, "picked": picked,
                        "correct_answer": right, "ok": ok,
                        "explanation": q.get("explanation", "")})

    mastery: dict[str, dict] = {}
    summary = {"weak": [], "developing": [], "mastered": []}
    now = datetime.now().isoformat(timespec="seconds")
    for slug, b in per_slug.items():
        score = round(100 * b["correct"] / max(1, b["total"]))
        level = _mastery_level(score)
        mastery[slug] = {"score": score, "level": level,
                         "correct": b["correct"], "total": b["total"], "ts": now}
        summary[level].append(slug)
        # Seed SRS — a diagnostic is a real assessment of this concept.
        try:
            await self._srs_schedule(slug, score)
        except Exception:
            pass

    # Persist mastery on the course progress (course-scoped, not global).
    progress = self._load_progress(course_id)
    progress["mastery"] = {**(progress.get("mastery") or {}), **mastery}
    progress["diagnostic"] = {
        "taken_at": now,
        "summary": summary,
        "question_count": len(details),
    }
    self._save_progress(course_id, progress)

    plan = self._compute_plan(course, progress["mastery"])
    original_count = len(_parse_lessons(course["fm"]))
    asyncio.create_task(self.emit("learn:diagnostic_taken", {
        "course_id": course_id,
        "weak": len(summary["weak"]),
        "developing": len(summary["developing"]),
        "mastered": len(summary["mastered"]),
    }))
    return {
        "course_id": course_id,
        "mastery": mastery,
        "summary": summary,
        "details": details,
        "plan_lesson_count": len(plan),
        "original_lesson_count": original_count,
        "plan_changed": len(plan) != original_count,
    }


@web_route("POST", "/api/courses/{course_id}/diagnostic/apply")
async def api_diagnostic_apply(self, request):
    """Reshape the course's syllabus around the stored mastery.

    Rewrites `lessons_json` (frontmatter-only mutation — body untouched),
    flags the note `personalized`, and resets progress since lesson indices
    changed. The propose/confirm split: submit computes, apply commits.
    """
    if not self._diagnostic_enabled():
        return {"error": "disabled"}
    course_id = request.path_params["course_id"]
    course = self._find_course(course_id)
    if not course:
        return {"error": "course not found"}
    progress = self._load_progress(course_id)
    mastery = progress.get("mastery") or {}
    if not mastery:
        return {"error": "no diagnostic taken — submit a diagnostic first"}
    plan = self._compute_plan(course, mastery)
    if not plan:
        return {"error": "personalized plan is empty (every concept mastered?)"}
    self.vault_update(course["path"], {
        "lessons_json": json.dumps(plan, ensure_ascii=False),
        "updated": datetime.now().date().isoformat(),
        "personalized": True,
    })
    # Lesson indices changed — completed_lessons no longer map. Keep mastery
    # + diagnostic record, drop positional progress.
    progress["completed_lessons"] = []
    progress["last_opened"] = 0
    progress.pop("completed_at", None)
    self._save_progress(course_id, progress)
    asyncio.create_task(self.emit("learn:course_personalized", {
        "course_id": course_id, "lesson_count": len(plan),
    }))
    return {"ok": True, "course_id": course_id, "lesson_count": len(plan)}
