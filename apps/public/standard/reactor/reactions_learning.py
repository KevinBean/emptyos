"""Learning reactions — speaking, shadowing, interview, reader, dictionary."""

from __future__ import annotations

from emptyos.sdk import on_event


class LearningReactionsMixin:
    @on_event("speaking:session_started")
    async def on_speaking_started(self, event):
        self._log_action("speaking:session_started", event.data.get("scenario", "")[:30])

    @on_event("speaking:cards_added")
    async def on_speaking_cards(self, event):
        count = event.data.get("count", 0)
        self._log_action("speaking:cards_added", f"{count} SRS cards")

    @on_event("speaking:session_ended")
    async def on_speaking_done(self, event):
        turns = event.data.get("turns", 0)
        duration = event.data.get("duration", 0)
        self._log_action("speaking:session_ended", f"{turns} turns, {duration}s")
        if turns >= 10:
            msg = f"🎤 Great speaking session! {turns} turns, {duration}s"
            await self._notify(msg, kind="milestone")

    @on_event("shadowing:perfect")
    async def on_shadowing_perfect(self, event):
        self._log_action("shadowing:perfect", f"score: {event.data.get('score', 0)}")

    @on_event("shadowing:attempt")
    async def on_shadowing_attempt(self, event):
        self._log_action("shadowing:attempt", f"score: {event.data.get('score', '?')}")

    # Detached: measured at 16.7s on the live daemon for a single lesson
    # completion, which pinned the serial bus for that whole time. Pure side
    # effect (a log row + a journal ripple) — nothing reads its result.
    @on_event("learn:lesson_completed", background=True)
    async def on_learn_lesson_completed(self, event):
        course = event.data.get("course_id", "")[:40]
        idx = event.data.get("lesson_index")
        detail = f"{course} lesson {idx}" if idx is not None else course
        self._log_action("learn:lesson_completed", detail)
        await self._journal_ripple(
            "📚", f"Completed a lesson in {course or 'a course'}", dim="intellectual"
        )

    @on_event("english:level_up")
    async def on_level_up(self, event):
        self._log_action("english:level_up", "level up!")
        msg = "🎉 English level up! Keep going!"
        await self._notify(msg, priority="info", kind="milestone")

    @on_event("speak-sharper:analyzed")
    async def on_sharper(self, event):
        scores = event.data.get("scores", {})
        self._log_action(
            "speak-sharper:analyzed", f"precision: {scores.get('word_precision', '?')}/10"
        )

    @on_event("speak-sharper:pattern_detected")
    async def on_sharper_pattern(self, event):
        pattern = event.data.get("pattern", "")[:40]
        self._log_action("speak-sharper:pattern_detected", pattern)

    @on_event("voice-review:analyzed")
    async def on_voice_review(self, event):
        self._log_action("voice-review:analyzed", f"score: {event.data.get('score', '?')}")

    @on_event("lesson:generated")
    async def on_lesson(self, event):
        self._log_action("lesson:generated", "new lesson")

    @on_event("dictionary:word_saved")
    async def on_word_saved(self, event):
        self._log_action("dictionary:word_saved", event.data.get("word", "")[:30])

    @on_event("dictionary:word_reviewed")
    async def on_word_reviewed(self, event):
        self._log_action("dictionary:word_reviewed", event.data.get("word", "")[:30])

    @on_event("dictionary:pack_created")
    async def on_dictionary_pack_created(self, event):
        self._log_action("dictionary:pack_created", event.data.get("pack_id", "")[:30])

    @on_event("dictionary:picture_word_saved")
    async def on_dictionary_picture_word_saved(self, event):
        self._log_action("dictionary:picture_word_saved", event.data.get("word", "")[:30])

    @on_event("reader:opened")
    async def on_reader_opened(self, event):
        slug = event.data.get("slug", "")
        self._log_action("reader:opened", slug[:30])
        # Quiet log only — opening a book happens often, no journal ripple
        # (avoids noisy "📖 opened X" entries every paragraph navigation cycle).

    @on_event("reader:highlighted")
    async def on_reader_highlighted(self, event):
        title = event.data.get("title") or event.data.get("slug", "")
        text = (event.data.get("text") or "").strip().replace("\n", " ")
        note = (event.data.get("note") or "").strip()
        snippet = text[:140] + ("…" if len(text) > 140 else "")
        self._log_action("reader:highlighted", f"{title}: {snippet[:40]}")
        if snippet:
            line = f"Highlight from *{title}*: “{snippet}”"
            if note:
                line += f" — {note}"
            await self._journal_ripple("🌟", line, dim="intellectual")

    @on_event("reader:note_created")
    async def on_reader_note(self, event):
        title = event.data.get("title") or event.data.get("slug", "")
        path = event.data.get("path", "")
        para = event.data.get("paragraph", "?")
        self._log_action("reader:note_created", f"{title} p{para}")
        await self._journal_ripple(
            "📝", f"Saved a reading note from *{title}* (¶{para}) → [[{path}]]", dim="intellectual"
        )

    @on_event("reader:scene_generated")
    async def on_reader_scene(self, event):
        # Quiet log only — no journal ripple. The scene is a UI artifact, not a milestone.
        slug = event.data.get("slug", "")
        para = event.data.get("paragraph", "?")
        self._log_action("reader:scene_generated", f"{slug} p{para}")

    @on_event("kb:viewed")
    async def on_kb_viewed(self, event):
        self._log_action("kb:viewed", str(event.data.get("slug",""))[:50])

    @on_event("reader:book_imported")
    async def on_book_imported(self, event):
        """A book joined the library — low-frequency, worth a breadcrumb."""
        title = event.data.get("title") or event.data.get("slug", "")
        self._log_action("reader:book_imported", str(title)[:50])
        await self._journal_ripple("📖", f"Added to the library: {str(title)[:60]}", dim="intellectual")

    # ── Wired 2026-08-16 — previously declared-but-unheard (architecture review) ──

    @on_event("soundcheck:session_started")
    async def on_soundcheck_started(self, event):
        # Quiet log, matching speaking:session_started — starting a drill is
        # not a milestone, and a per-start ripple would bury the daily note.
        mode = event.data.get("mode", "")
        self._log_action("soundcheck:session_started", str(mode)[:30])

    @on_event("soundcheck:session_completed")
    async def on_soundcheck_completed(self, event):
        # Keys are soundcheck.engine.progress(): asked / right / accuracy.
        # A breadcrumb reading "rounds: ?" every time is worse than none.
        right = event.data.get("right", 0)
        asked = event.data.get("asked", 0)
        self._log_action("soundcheck:session_completed", f"{right}/{asked} correct")
        score = f" — {right}/{asked}" if asked else ""
        await self._journal_ripple(
            "🗣️", f"Finished a pronunciation session{score}", dim="intellectual"
        )

    @on_event("soundcheck:contrast_cleared")
    async def on_soundcheck_contrast_cleared(self, event):
        """An earned win: a contrast that was previously being failed.

        soundcheck computes `cleared` against the worst-list from *before* this
        session (see soundcheck/sessions.py::_summarise), so this fires only on
        a real improvement — which is what makes it worth a nudge.
        """
        cleared = [str(c) for c in (event.data.get("cleared") or []) if c]
        if not cleared:
            return
        label = ", ".join(cleared[:3]) + ("…" if len(cleared) > 3 else "")
        self._log_action("soundcheck:contrast_cleared", label[:50])
        msg = f"🎯 Cleared a sound contrast you were failing: {label}"
        await self._journal_ripple("🎯", f"Cleared sound contrast: {label}", dim="intellectual")
        await self._notify(msg, kind="milestone")

    @on_event("audio-course:course_added")
    async def on_audio_course_added(self, event):
        """A course joined the shelf — same shape as reader:book_imported."""
        course = event.data.get("course_id", "")
        chapters = event.data.get("chapters", "?")
        self._log_action("audio-course:course_added", f"{str(course)[:40]} ({chapters} ch)")
        await self._journal_ripple(
            "🎧", f"Added an audio course: {str(course)[:60]}", dim="intellectual"
        )

    @on_event("audio-course:exercise_cleared")
    async def on_audio_course_exercise_cleared(self, event):
        # Quiet log only — fires once per exercise transition, and a course has
        # many exercises, so a ripple each time would be per-drill chatter.
        self._log_action(
            "audio-course:exercise_cleared",
            f"{str(event.data.get('exercise', ''))[:30]} @ {event.data.get('score', '?')}",
        )

    @on_event("kb:figure_attached")
    async def on_kb_figure_attached(self, event):
        # Quiet log, matching kb:viewed — the figure is already visible in the
        # note it was attached to, so a journal line would just duplicate it.
        slug = event.data.get("slug", "")
        self._log_action("kb:figure_attached", str(slug)[:50])

    @on_event("kb:source_ingested")
    async def on_kb_source_ingested(self, event):
        # A client document registered as a project-scoped source (kb-source,
        # never the global KB). One user action produces the reference note and
        # every clause note at once, so the ripple is one line, not one per
        # clause. Wired 2026-10-02 (declared 09-30, unheard until then).
        slug = str(event.data.get("slug", ""))[:50]
        # The human-facing document id when the ingest had one; the note slug
        # otherwise. Both arrive from an HTTP body, so both are bounded.
        name = str(event.data.get("standard_id") or "")[:50] or slug or "a document"
        project = str(event.data.get("project", ""))[:40]
        clauses = int(event.data.get("clauses") or 0)
        count = f"{clauses} clause" + ("" if clauses == 1 else "s")
        self._log_action("kb:source_ingested", f"{slug} -> {project} ({count})")
        where = f" for {project}" if project else ""
        await self._journal_ripple("📑", f"Registered source {name}{where} ({count})", dim="occupational")

    # ── library: the reference manager ────────────────────────────────
    # Six emits that had no listener at all (P3, 2026-09-03) — a third of
    # the whole unheard-event finding came from this one app. Two earn a
    # journal ripple (adding a paper and finishing with it are real
    # intellectual events worth seeing in the daily note); the rest are
    # quiet logs, because a field edit or an SRS grade is bookkeeping and a
    # ripple per highlight would be per-page chatter.

    @on_event("library:paper_added")
    async def on_library_paper_added(self, event):
        title = str(event.data.get("title", "") or event.data.get("citekey", ""))[:60]
        self._log_action("library:paper_added", title)
        await self._journal_ripple(
            "📄", f"Added to the library: {title or 'a paper'}", dim="intellectual"
        )

    @on_event("library:sent_to_kb")
    async def on_library_sent_to_kb(self, event):
        citekey = str(event.data.get("citekey", ""))[:50]
        self._log_action("library:sent_to_kb", citekey)
        await self._journal_ripple(
            "🔖", f"Distilled {citekey or 'a paper'} into the knowledge base",
            dim="intellectual",
        )

    @on_event("library:highlight_added")
    async def on_library_highlight_added(self, event):
        # Quiet: highlighting is per-page and would flood the daily note.
        page = event.data.get("page")
        citekey = str(event.data.get("citekey", ""))[:50]
        self._log_action(
            "library:highlight_added", f"{citekey} p{page}" if page else citekey
        )

    @on_event("library:highlight_reviewed")
    async def on_library_highlight_reviewed(self, event):
        # Quiet: one row per SRS grade, same shape as the other drill events.
        self._log_action(
            "library:highlight_reviewed",
            f"{str(event.data.get('id', ''))[:30]} @ {event.data.get('rating', '?')}",
        )

    @on_event("library:paper_updated")
    async def on_library_paper_updated(self, event):
        # Quiet: a metadata edit / PDF attach is bookkeeping, not an event.
        # Two emit shapes reach here — `field` (single set_field / PDF upload)
        # and `updates` (a bulk edit) — so report whichever is present.
        data = event.data
        changed = data.get("field") or ", ".join(data.get("updates") or []) or "?"
        self._log_action(
            "library:paper_updated", f"{str(data.get('citekey', ''))[:40]}: {changed}"[:80]
        )

    @on_event("library:paper_deleted")
    async def on_library_paper_deleted(self, event):
        self._log_action("library:paper_deleted", str(event.data.get("citekey", ""))[:50])
