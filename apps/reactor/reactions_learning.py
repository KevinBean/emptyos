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
            await self._notify(msg)
            await self._telegram(msg)

    @on_event("shadowing:perfect")
    async def on_shadowing_perfect(self, event):
        self._log_action("shadowing:perfect", f"score: {event.data.get('score', 0)}")

    @on_event("shadowing:attempt")
    async def on_shadowing_attempt(self, event):
        self._log_action("shadowing:attempt", f"score: {event.data.get('score', '?')}")

    @on_event("english:level_up")
    async def on_level_up(self, event):
        self._log_action("english:level_up", "level up!")
        msg = "🎉 English level up! Keep going!"
        await self._notify(msg, priority="info")
        await self._telegram(msg)

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
