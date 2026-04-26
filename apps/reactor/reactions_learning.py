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
        self._log_action("speak-sharper:analyzed", f"precision: {scores.get('word_precision', '?')}/10")

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

    @on_event("reader:highlight_added")
    async def on_highlight(self, event):
        self._log_action("reader:highlight_added", f"id: {event.data.get('id', '')}")

    @on_event("reader:review_completed")
    async def on_reader_review(self, event):
        self._log_action("reader:review_completed", f"quality: {event.data.get('quality', '?')}")

    @on_event("reader:session_logged")
    async def on_reader_session(self, event):
        mins = event.data.get("minutes", 0)
        self._log_action("reader:session_logged", f"{mins}min reading")
        await self._journal_ripple("📚", f"Read for {mins} minutes")
