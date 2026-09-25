"""Learn — Coursera-style structured learning over KB notes.

A **course** is a `kind: doc` KB note tagged with `course`. Its frontmatter
carries an ordered `lessons:` list. Each lesson is either:

- ``kind: read`` — references a slug (concept / clause / case / lesson kb note); the
  player renders that note's body.
- ``kind: quiz`` — references the same slug as a prior read lesson; the
  player generates a 3-question MCQ via LLM from the source note's body.

Progress is per-course JSON at ``data/apps/learn/progress/<course-id>.json``.

Reuses ``apps/kb`` as the content store — never duplicates note bodies.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from emptyos.sdk import BaseApp, cli_command, slugify, web_route
from emptyos.sdk.utils import safe_path_segment

from . import srs as _srs
from . import review_all as _review_all
from . import citations as _citations
from . import diagnostic as _diagnostic
from . import video as _video
from .shared import _parse_lessons  # pure helper (shared with diagnostic.py)


# ─── SRS + citations bindings (defined in their modules) ───
# _srs_path / _load_srs / _save_srs / _srs_schedule live in srs.py;
# _annotate_pdf_citations lives in citations.py.


class LearnApp(BaseApp):
    async def setup(self):
        await super().setup()
        self._progress_dir = self.data_dir / "progress"
        self._progress_dir.mkdir(parents=True, exist_ok=True)
        self._quiz_cache_dir = self.data_dir / "quiz-cache"
        self._quiz_cache_dir.mkdir(parents=True, exist_ok=True)

    # ── SRS module bindings ──
    _srs_path               = _srs._srs_path
    _load_srs               = _srs._load_srs
    _save_srs               = _srs._save_srs
    _srs_schedule           = _srs._srs_schedule
    _generate_quiz_for_slug = _srs._generate_quiz_for_slug
    _enrich_due_card        = _srs._enrich_due_card
    api_review_due          = _srs.api_review_due
    api_review_next         = _srs.api_review_next
    api_review_grade        = _srs.api_review_grade
    api_review_stats        = _srs.api_review_stats
    panel_review_due        = _srs.panel_review_due
    voice_start_review      = _srs.voice_start_review

    # ── Unified review-queue bindings (review_all.py) ──
    _gather_review_sources  = _review_all._gather_review_sources
    _unified_due_count      = _review_all._unified_due_count
    api_review_all          = _review_all.api_review_all
    api_review_quiz         = _review_all.api_review_quiz
    api_review_grade_item   = _review_all.api_review_grade_item

    # ── Citations module bindings ──
    _annotate_pdf_citations = _citations._annotate_pdf_citations
    _annotate_wikilinks     = _citations._annotate_wikilinks

    # ── Diagnostic module bindings (dark-flagged) ──
    _diagnostic_enabled     = _diagnostic._diagnostic_enabled
    _diagnostic_slugs       = _diagnostic._diagnostic_slugs
    _mastery_level          = staticmethod(_diagnostic._mastery_level)
    _compute_mastery_score  = staticmethod(_diagnostic._compute_mastery_score)
    _confidence_weighted_mastery_enabled = _diagnostic._confidence_weighted_mastery_enabled
    _compute_plan           = _diagnostic._compute_plan
    api_diagnostic_status   = _diagnostic.api_diagnostic_status
    api_diagnostic_generate = _diagnostic.api_diagnostic_generate
    api_diagnostic_submit   = _diagnostic.api_diagnostic_submit
    api_diagnostic_apply    = _diagnostic.api_diagnostic_apply

    # ── Lesson video bindings (video.py, dark-flagged) ──
    _lesson_video_enabled   = _video._lesson_video_enabled
    _video_pipeline         = _video._video_pipeline
    _video_lesson           = _video._video_lesson
    _video_inputs           = _video._video_inputs
    _video_source           = _video._video_source
    _open_video_run         = _video._open_video_run
    _drive_video_run        = _video._drive_video_run
    _queue_course_videos    = _video._queue_course_videos
    _video_run_view         = _video._video_run_view
    _publish_lesson_video   = _video._publish_lesson_video
    _lesson_video_dir       = _video._lesson_video_dir
    _load_video_index       = _video._load_video_index
    api_video_start         = _video.api_video_start
    api_video_run           = _video.api_video_run
    api_video_resume        = _video.api_video_resume
    api_video_discard       = _video.api_video_discard
    api_lesson_video        = _video.api_lesson_video
    api_video_file          = _video.api_video_file
    api_video_queue_course  = _video.api_video_queue_course
    panel_video_runs        = _video.panel_video_runs

    # ── Dark-default flag gate (tutorial series + verify loop) ──
    def _tutorial_enabled(self) -> bool:
        # Settings service first (⚙ panel toggle, live), then emptyos.toml.
        v = self.setting("learn.feature.tutorial.enabled", None)
        if v is not None:
            return bool(v)
        return bool(self.app_config("feature.tutorial.enabled", False))

    # ─── Course catalog ─────────────────────────────────────────

    @web_route("GET", "/api/courses")
    async def api_list_courses(self, request):
        courses = []
        for note in self.vault_query(tags=["course"]):
            path = note.get("path", "")
            fm = self.vault_get_properties(path) or note.get("properties") or {}
            # Filter: must be a kind:doc note with a course_id (drops unrelated
            # vault notes that happen to be tagged "course").
            if fm.get("kind") != "doc" or not fm.get("course_id"):
                continue
            lessons = _parse_lessons(fm)
            course_id = fm.get("course_id")
            progress = self._load_progress(course_id)
            completed = set(progress.get("completed_lessons", []))
            courses.append({
                "id": course_id,
                "title": fm.get("title") or path,
                "description": fm.get("description") or "",
                "level": fm.get("level") or "",
                "duration_min": fm.get("duration_min") or 0,
                "lesson_count": len(lessons),
                "completed_count": len([i for i in range(len(lessons)) if i in completed]),
                "domain": fm.get("domain") or "",
                "topic": fm.get("topic") or "",
                "started_at": progress.get("started_at"),
                "completed_at": progress.get("completed_at"),
                "tutorial": bool(fm.get("tutorial")),
                "verify_status": fm.get("verify_status") or "",
                "_path": path,
            })
        courses.sort(key=lambda c: (c.get("completed_at") is None, c.get("title", "")))
        return {"courses": courses}

    @web_route("GET", "/api/courses/{course_id}")
    async def api_course_detail(self, request):
        course_id = request.path_params["course_id"]
        course = self._find_course(course_id)
        if not course:
            return {"error": "course not found"}
        fm = course["fm"]
        lessons_raw = _parse_lessons(fm)
        progress = self._load_progress(course_id)
        completed = set(progress.get("completed_lessons", []))
        lessons = []
        for i, lesson in enumerate(lessons_raw):
            if not isinstance(lesson, dict):
                continue
            lessons.append({
                "index": i,
                "title": lesson.get("title") or f"Lesson {i+1}",
                "kind": lesson.get("kind", "read"),
                "slug": lesson.get("slug", ""),
                "duration_min": lesson.get("duration_min", 0),
                "completed": i in completed,
            })
        return {
            "id": course_id,
            "title": fm.get("title") or course["path"],
            "description": fm.get("description") or "",
            "level": fm.get("level") or "",
            "duration_min": fm.get("duration_min") or 0,
            "domain": fm.get("domain") or "",
            "topic": fm.get("topic") or "",
            "tutorial": bool(fm.get("tutorial")),
            "verify": {
                "status": fm.get("verify_status") or "",
                "ts": fm.get("verify_ts") or "",
                "part": fm.get("verify_part") or "",
                "step": int(fm.get("verify_step") or 0),
                "error": fm.get("verify_error") or "",
            },
            "lessons": lessons,
            "video_enabled": self._lesson_video_enabled(),
            "progress": {
                "completed_count": len(completed),
                "total": len(lessons),
                "last_opened": progress.get("last_opened", 0),
                "started_at": progress.get("started_at"),
                "completed_at": progress.get("completed_at"),
            },
            "course_note_path": course["path"],
        }

    # ─── Lesson player ─────────────────────────────────────────

    @web_route("GET", "/api/courses/{course_id}/lessons/{idx}")
    async def api_lesson(self, request):
        course_id = request.path_params["course_id"]
        try:
            idx = int(request.path_params["idx"])
        except ValueError:
            return {"error": "lesson index must be integer"}
        course = self._find_course(course_id)
        if not course:
            return {"error": "course not found"}
        lessons = _parse_lessons(course["fm"])
        if idx < 0 or idx >= len(lessons):
            return {"error": "lesson index out of range"}
        lesson = lessons[idx]
        slug = lesson.get("slug", "")
        source = await self._resolve_lesson_source(slug)
        # Mark as last_opened
        self._update_progress(course_id, last_opened=idx)
        # Emit course_started on first open
        progress = self._load_progress(course_id)
        if not progress.get("started_at"):
            self._update_progress(course_id, started_at=_now_iso())
            self.spawn_background(self.emit("learn:course_started", {"course_id": course_id}))
        return {
            "course_id": course_id,
            "index": idx,
            "title": lesson.get("title") or f"Lesson {idx+1}",
            "kind": lesson.get("kind", "read"),
            "slug": slug,
            "source": source,  # {title, body_html or body_md, path, kind}
            "prev": idx - 1 if idx > 0 else None,
            "next": idx + 1 if idx + 1 < len(lessons) else None,
            "total": len(lessons),
            "completed": idx in set(self._load_progress(course_id).get("completed_lessons", [])),
            "video_enabled": self._lesson_video_enabled(),
        }

    @web_route("POST", "/api/courses/{course_id}/lessons/{idx}/complete")
    async def api_complete_lesson(self, request):
        course_id = request.path_params["course_id"]
        try:
            idx = int(request.path_params["idx"])
        except ValueError:
            return {"error": "lesson index must be integer"}
        course = self._find_course(course_id)
        if not course:
            return {"error": "course not found"}
        lessons = _parse_lessons(course["fm"])
        progress = self._load_progress(course_id)
        completed = set(progress.get("completed_lessons", []))
        completed.add(idx)
        progress["completed_lessons"] = sorted(completed)
        # Course completion check
        if len(completed) >= len(lessons) and not progress.get("completed_at"):
            progress["completed_at"] = _now_iso()
            self.spawn_background(self.emit("learn:course_completed", {"course_id": course_id}))
        self._save_progress(course_id, progress)
        self.spawn_background(self.emit("learn:lesson_completed", {"course_id": course_id, "lesson_index": idx}))
        return {"ok": True, "completed_count": len(completed), "total": len(lessons)}

    # ─── Quiz generation ───────────────────────────────────────

    @web_route("POST", "/api/courses/{course_id}/lessons/{idx}/quiz")
    async def api_generate_quiz(self, request):
        course_id = request.path_params["course_id"]
        try:
            idx = int(request.path_params["idx"])
        except ValueError:
            return {"error": "lesson index must be integer"}
        course = self._find_course(course_id)
        if not course:
            return {"error": "course not found"}
        lessons = _parse_lessons(course["fm"])
        if idx < 0 or idx >= len(lessons):
            return {"error": "lesson index out of range"}
        lesson = lessons[idx]
        slug = lesson.get("slug", "")
        # Delegates to the shared helper in srs.py so review-mode regen
        # uses the exact same cache + prompt path.
        return await self._generate_quiz_for_slug(slug)

    @web_route("POST", "/api/courses/{course_id}/lessons/{idx}/quiz/submit")
    async def api_submit_quiz(self, request):
        course_id = request.path_params["course_id"]
        try:
            idx = int(request.path_params["idx"])
        except ValueError:
            return {"error": "lesson index must be integer"}
        body = await self.read_json(request)
        answers = body.get("answers") or {}  # {0: "A", 1: "C", ...}
        questions = body.get("questions") or []
        if not questions:
            return {"error": "questions array required"}
        correct = 0
        details = []
        for i, q in enumerate(questions):
            picked = answers.get(str(i)) or answers.get(i)
            right = (q.get("answer") or "").strip().upper()
            ok = (picked or "").strip().upper() == right
            if ok:
                correct += 1
            details.append({"index": i, "picked": picked, "correct_answer": right, "ok": ok, "explanation": q.get("explanation", "")})
        score = round(100 * correct / max(1, len(questions)))
        progress = self._load_progress(course_id)
        attempts = progress.get("quiz_attempts", [])
        attempts.append({"lesson": idx, "score": score, "correct": correct, "total": len(questions), "ts": _now_iso()})
        progress["quiz_attempts"] = attempts[-50:]  # cap history
        self._save_progress(course_id, progress)
        # Schedule the source slug for SRS review based on this score.
        # Find the lesson's source slug to anchor the SRS entry.
        try:
            course = self._find_course(course_id)
            if course:
                lessons = _parse_lessons(course["fm"])
                if 0 <= idx < len(lessons):
                    slug = lessons[idx].get("slug", "")
                    if slug:
                        await self._srs_schedule(slug, score)
        except Exception:
            pass  # don't fail the quiz submit if SRS is broken
        self.spawn_background(self.emit("learn:quiz_taken", {"course_id": course_id, "lesson_index": idx, "score": score}))
        return {"score": score, "correct": correct, "total": len(questions), "details": details}

    # ─── Progress reset / status ───────────────────────────────

    @web_route("POST", "/api/courses/{course_id}/reset")
    async def api_reset_progress(self, request):
        course_id = request.path_params["course_id"]
        f = self._progress_dir / f"{course_id}.json"
        if f.exists():
            f.unlink()
        return {"ok": True}

    # ─── PDF serving (for citation anchors) ────────────────────

    @web_route("GET", "/api/pdf/{vault_rel_path:path}")
    async def api_pdf_serve(self, request):
        """Serve a PDF binary from the vault. Path-traversal-safe,
        .pdf-only. The daemon auth gate already applies to this route."""
        from starlette.responses import FileResponse, JSONResponse
        rel = (request.path_params.get("vault_rel_path") or "").strip()
        if not rel:
            return JSONResponse({"error": "path required"}, status_code=400)
        if not rel.lower().endswith(".pdf"):
            return JSONResponse({"error": "only .pdf paths served"}, status_code=400)
        # Resolve under vault_root + reject escapes.
        try:
            vault_root = self.vault_root.resolve()
            candidate = (vault_root / rel).resolve()
            candidate.relative_to(vault_root)  # raises if escape
        except (ValueError, OSError):
            return JSONResponse({"error": "invalid path"}, status_code=403)
        if not candidate.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(candidate), media_type="application/pdf",
                            filename=candidate.name)

    # ─── In-lesson reader notes ────────────────────────────────

    @staticmethod
    def _escape_for_blockquote(text: str) -> str:
        """Make `text` safe to drop inside `> ...` lines.

        - Collapse internal newlines to single spaces (multi-line selections
          render as one quoted line).
        - Backslash-escape any leading `>` characters that would create a
          nested blockquote.
        - Strip control characters.
        """
        if not text:
            return ""
        cleaned = re.sub(r"\s+", " ", text).strip()
        # Escape leading >; let the blockquote prefix stay clean.
        cleaned = re.sub(r"^>", r"\>", cleaned)
        # Strip ASCII control chars (keep tabs out too).
        cleaned = "".join(c for c in cleaned if c >= " " or c == "\n")
        return cleaned

    @web_route("POST", "/api/lessons/note")
    async def api_save_note(self, request):
        """Append a reader note to a KB note's `## Reader notes` section.

        Body: {slug, quote, note, course_id?, lesson_index?}
        """
        body = await self.read_json(request)
        slug = (body.get("slug") or "").strip()
        quote = (body.get("quote") or "").strip()
        note = (body.get("note") or "").strip()
        course_id = (body.get("course_id") or "").strip()
        try:
            lesson_index = int(body.get("lesson_index"))
        except (TypeError, ValueError):
            lesson_index = None
        if not slug:
            return {"error": "slug required"}
        if not note and not quote:
            return {"error": "note or quote required"}
        # Resolve slug → vault path via kb (kwargs!).
        try:
            kb_note = await self.call_app("kb", "get_note", slug=slug)
        except Exception as e:
            return {"error": f"kb lookup failed: {e}"}
        if not kb_note or kb_note.get("error"):
            return {"error": kb_note.get("error") if kb_note else "not found"}
        path = kb_note.get("path") or ""
        if not path:
            return {"error": "kb note has no path"}
        # Compose the entry.
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        backref_bits = []
        if course_id:
            if lesson_index is not None:
                backref_bits.append(f"[[{course_id}]] lesson {lesson_index + 1}")
            else:
                backref_bits.append(f"[[{course_id}]]")
        backref = " · " + " ".join(backref_bits) if backref_bits else ""
        lines = [f"- **{ts}**{backref}"]
        if quote:
            lines.append(f"  > {self._escape_for_blockquote(quote)}")
        if note:
            lines.append("")  # blank line between blockquote and body
            lines.append(f"  {note}")
        entry = "\n".join(lines) + "\n"
        # Serialised append under per-path lock.
        async with self.note_lock(path):
            self.vault_append_section(path, "Reader notes", entry)
            # Flip author to "both" if currently user-authored.
            props = self.vault_get_properties(path) or {}
            current_author = (props.get("author") or "user").lower()
            if current_author != "both" and current_author != "ai":
                self.vault_update(path, {"author": "both"})
        self.spawn_background(self.emit("learn:note_saved", {
            "slug": slug, "path": path, "course_id": course_id,
            "lesson_index": lesson_index,
        }))
        return {"ok": True, "slug": slug, "path": path}

    @web_route("GET", "/api/lessons/notes/{slug:path}")
    async def api_get_notes(self, request):
        """Return the parsed `## Reader notes` for a KB slug, newest-first."""
        slug = (request.path_params.get("slug") or "").strip()
        if not slug:
            return {"notes": []}
        try:
            kb_note = await self.call_app("kb", "get_note", slug=slug)
        except Exception as e:
            return {"error": f"kb lookup failed: {e}"}
        if not kb_note or kb_note.get("error"):
            return {"notes": []}
        path = kb_note.get("path") or ""
        if not path:
            return {"notes": []}
        section = self.vault_read_section(path, "Reader notes") or ""
        notes = _parse_reader_notes(section)
        # Newest first.
        notes.sort(key=lambda n: n.get("date", ""), reverse=True)
        return {"slug": slug, "path": path, "count": len(notes), "notes": notes}

    # ─── Course authoring (wizard backend) ─────────────────────

    @web_route("GET", "/api/authoring/kb-notes")
    async def api_authoring_kb_notes(self, request):
        """Filterable list of KB notes for the wizard's picker.

        Query params: kind, domain, q (substring search on slug + title).
        Returns: [{slug, title, kind, domain, topic, has_pdf}, ...]
        """
        qp = request.query_params
        kind_filter = (qp.get("kind") or "").strip()
        domain_filter = (qp.get("domain") or "").strip()
        q = (qp.get("q") or "").strip().lower()
        out: list[dict] = []
        for note in self.vault_query(tags=["kb"]):
            fm = note.get("properties") or {}
            kind = (fm.get("kind") or "").strip()
            if not kind or kind == "doc":
                continue  # skip course docs themselves; offer concepts/clauses/cases/refs/lessons
            if kind_filter and kind != kind_filter:
                continue
            domain = (fm.get("domain") or "").strip()
            if domain_filter and domain != domain_filter:
                continue
            path = note.get("path", "")
            slug = path.rsplit("/", 1)[-1].replace(".md", "") if path else ""
            title = (fm.get("title") or slug).strip()
            if q and q not in slug.lower() and q not in title.lower():
                continue
            out.append({
                "slug": slug,
                "title": title,
                "kind": kind,
                "domain": domain,
                "topic": (fm.get("topic") or "").strip(),
                "has_pdf": bool(fm.get("local_pdf")),
            })
        out.sort(key=lambda n: (n["kind"], n["title"].lower()))
        return {"notes": out, "count": len(out)}

    @web_route("POST", "/api/courses/save")
    async def api_course_save(self, request):
        """Create or update a course (kind:doc KB note tagged `course`).

        Body shape:
          {course_id, title, description, level, domain, topic, duration_min,
           audience?, prerequisites?, lessons: [{slug, title, kind, duration_min?}, ...]}

        Slug is derived from title if course_id is empty. The same endpoint
        handles both create (writes new file) and update (overwrites in place).
        """
        body = await self.read_json(request)
        title = (body.get("title") or "").strip()
        if not title:
            return {"error": "title required"}
        lessons_in = body.get("lessons") or []
        if not isinstance(lessons_in, list) or not lessons_in:
            return {"error": "at least one lesson required"}
        # Normalize and validate each lesson.
        lessons_clean: list[dict] = []
        for i, l in enumerate(lessons_in):
            if not isinstance(l, dict):
                continue
            slug = (l.get("slug") or "").strip()
            if not slug:
                return {"error": f"lesson {i + 1} missing slug"}
            lessons_clean.append({
                "slug": slug,
                "title": (l.get("title") or "").strip() or f"Lesson {i + 1}",
                "kind": (l.get("kind") or "read").strip().lower(),
                "duration_min": int(l.get("duration_min") or 0),
            })
        course_id = (body.get("course_id") or "").strip() or _slugify(title)
        rel_path = f"30_Resources/EmptyOS/kb/docs/course-{course_id}.md"
        # Read existing frontmatter if updating, to preserve `created`.
        existing = self.vault_get_properties(rel_path) or {}
        created = existing.get("created") or datetime.now().date().isoformat()
        fm = {
            "tags": ["kb", "course"],
            "kind": "doc",
            "course_id": course_id,
            "title": title,
            "description": (body.get("description") or "").strip(),
            "level": (body.get("level") or "intermediate").strip(),
            "domain": (body.get("domain") or "").strip(),
            "topic": (body.get("topic") or "").strip(),
            "duration_min": int(body.get("duration_min") or 0),
            "created": created,
            "updated": datetime.now().date().isoformat(),
            "lessons_json": json.dumps(lessons_clean, ensure_ascii=False),
        }
        # Optional extras only if provided.
        for opt in ("audience", "prerequisites", "tutorial"):
            v = body.get(opt)
            if v:
                fm[opt] = v
        # Auto-generated body — the lessons are the content.
        body_md = (
            f"# {title}\n\n"
            f"{fm['description'] or 'A learning course.'}\n\n"
            f"Edit this course's lessons via the **Edit** button in `/learn/`.\n"
        )
        # Use vault_index's update path for both create + overwrite (it handles both).
        vi = self.kernel.services.get_optional("vault_index")
        if vi:
            # Always write via create_note — VaultIndex create_note overwrites
            # if the file exists, and re-indexes either way.
            vi.create_note(rel_path, fm, body_md)
        else:
            self.vault_create_note(rel_path, fm, body_md)
        is_update = bool(existing)
        self.spawn_background(self.emit(
            "learn:course_saved",
            {"course_id": course_id, "path": rel_path, "is_update": is_update},
        ))
        return {"ok": True, "course_id": course_id, "path": rel_path, "is_update": is_update}

    # ─── Tutorial verify loop (Lathe borrow, dark-flagged) ──────

    @web_route("POST", "/api/tutorial/verify-result")
    async def api_tutorial_verify_result(self, request):
        """Record a tutorial-series verification status on its course note.

        Called by the /eos-tutorial-verify skill — never by a UI button, so an
        unclicked button can never strand a course at `verifying`. Body:
          {course_id, status: verifying|verified|skipped|failed,
           part?, failed_step?, error?}

        `skipped` means a required toolchain wasn't installed — explicitly NOT
        a failure. Writes verify_* frontmatter on the course note.
        """
        if not self._tutorial_enabled():
            return {"error": "disabled", "hint": "enable learn.feature.tutorial.enabled in Settings"}
        body = await self.read_json(request)
        course_id = (body.get("course_id") or "").strip()
        status = (body.get("status") or "").strip().lower()
        if status not in ("verifying", "verified", "skipped", "failed"):
            return {"error": f"invalid status '{status}'"}
        course = self._find_course(course_id)
        if not course:
            return {"error": "course not found"}
        props = {
            "verify_status": status,
            "verify_ts": _now_iso(),
        }
        if status == "failed":
            props["verify_part"] = (body.get("part") or "").strip()
            props["verify_step"] = int(body.get("failed_step") or 0)
            props["verify_error"] = (body.get("error") or "").strip()
        else:
            # Clear stale failure detail from a previous run.
            props["verify_part"] = ""
            props["verify_step"] = 0
            props["verify_error"] = ""
        self.vault_update(course["path"], props)
        self.spawn_background(self.emit(
            "learn:tutorial_verified",
            {"course_id": course_id, "status": status, "error": props.get("verify_error", "")},
        ))
        return {"ok": True, "course_id": course_id, "status": status}

    # ─── CLI ────────────────────────────────────────────────────

    @cli_command("learn")
    async def cli_learn(self, course_id: str = ""):
        """List courses, or show progress for a specific course."""
        if not course_id:
            res = await self.api_list_courses(None)
            for c in res.get("courses", []):
                done = c["completed_count"]
                total = c["lesson_count"]
                pct = round(100 * done / max(1, total))
                print(f"  {c['id']:<30} {pct:>3}% ({done}/{total})  {c['title']}")
            return
        course = self._find_course(course_id)
        if not course:
            print(f"Course not found: {course_id}")
            return
        progress = self._load_progress(course_id)
        lessons = _parse_lessons(course["fm"])
        completed = set(progress.get("completed_lessons", []))
        print(f"\n{course['fm'].get('title', course_id)}")
        print(f"  Progress: {len(completed)}/{len(lessons)} lessons\n")
        for i, lesson in enumerate(lessons):
            mark = "✓" if i in completed else " "
            print(f"  [{mark}] {i+1}. {lesson.get('title', '')} ({lesson.get('kind', 'read')})")

    # ─── Internals ──────────────────────────────────────────────

    def _find_course(self, course_id: str) -> dict | None:
        for note in self.vault_query(tags=["course"]):
            path = note.get("path", "")
            fm = self.vault_get_properties(path) or note.get("properties") or {}
            cid = fm.get("course_id") or fm.get("slug") or _slugify(fm.get("title", ""))
            if cid == course_id:
                return {"fm": fm, "path": path}
        return None

    async def _resolve_lesson_source(self, slug: str) -> dict:
        if not slug:
            return {}
        try:
            res = await self.call_app("kb", "get_note", slug=slug)
        except Exception as e:
            return {"error": f"kb lookup failed: {e}"}
        if not res or res.get("error"):
            return {"error": res.get("error") if res else "not found"}
        props = res.get("properties") or {}
        body_md = res.get("body") or ""
        # Annotate `[[ref-slug]] p.<N>` citations with PDF-anchor buttons
        # for any ref that carries `local_pdf:` frontmatter. Returns the
        # body unchanged when no upgradeable citations are found.
        try:
            body_md = await self._annotate_pdf_citations(body_md)
        except Exception:
            pass  # never break lesson load on citation failure
        # Convert remaining [[wikilinks]] to standard markdown links → kb app.
        # Runs after PDF annotation so already-upgraded citations are left alone.
        try:
            body_md = self._annotate_wikilinks(body_md)
        except Exception:
            pass
        return {
            "slug": slug,
            "title": props.get("title") or res.get("name") or slug,
            "kind": props.get("kind") or "",
            "body_md": body_md,
            "path": res.get("path") or "",
            "domain": props.get("domain") or "",
            "topic": props.get("topic") or "",
        }

    def _progress_path(self, course_id: str) -> Path | None:
        """Progress file for a course, or None if the id isn't a plain slug.

        course_id comes from a path param and _save_progress OVERWRITES the
        target -- the only overwrite among the traversal sinks found in the
        2026-07-19 audit. _find_course runs AFTER the write, inside a
        try/except for SRS scheduling, so it never gated it.
        """
        if not safe_path_segment(course_id):
            return None
        return self._progress_dir / f"{course_id}.json"

    def _load_progress(self, course_id: str) -> dict:
        f = self._progress_path(course_id)
        if f is None or not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_progress(self, course_id: str, progress: dict) -> None:
        f = self._progress_path(course_id)
        if f is None:
            return
        f.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update_progress(self, course_id: str, **fields) -> None:
        progress = self._load_progress(course_id)
        progress.update(fields)
        self._save_progress(course_id, progress)


_NOTE_HEADER_RE = re.compile(
    r"^- \*\*(?P<date>[^*]+)\*\*"
    r"(?:\s*·\s*\[\[(?P<course_id>[^\]]+)\]\](?:\s+lesson\s+(?P<lesson>\d+))?)?",
)


def _parse_reader_notes(section: str) -> list[dict]:
    """Parse the body of a `## Reader notes` section into structured entries.

    Each entry starts with `- **<date>**` and may carry a `> quote` line
    and/or body paragraph(s). Unmatched leading content surfaces as
    `{raw: ...}` so legacy/hand-written notes aren't lost.
    """
    if not section:
        return []
    lines = section.splitlines()
    out: list[dict] = []
    current: dict | None = None
    quote_buf: list[str] = []
    body_buf: list[str] = []

    def _flush():
        if current is None:
            return
        if quote_buf:
            current["quote"] = " ".join(s.strip() for s in quote_buf).strip()
        if body_buf:
            current["note"] = "\n".join(body_buf).strip()
        out.append(current)

    for raw in lines:
        m = _NOTE_HEADER_RE.match(raw)
        if m:
            _flush()
            quote_buf = []
            body_buf = []
            current = {
                "date": m.group("date").strip(),
                "course_id": (m.group("course_id") or "").strip(),
                "quote": "",
                "note": "",
            }
            lesson = m.group("lesson")
            if lesson is not None:
                try:
                    current["lesson_index"] = int(lesson) - 1
                except ValueError:
                    pass
            continue
        if current is None:
            stripped = raw.strip()
            if stripped:
                out.append({"raw": stripped})
            continue
        line = raw.rstrip()
        if line.lstrip().startswith(">"):
            quote_buf.append(line.lstrip()[1:].strip())
        elif line.strip():
            body_buf.append(line.strip())
    _flush()
    return out


def _slugify(text: str) -> str:
    return slugify(text, max_len=None, fallback="course")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
