"""Dictation transcript polishing — shared cleanup over a raw STT transcript.

Extracted from ``apps/public/standard/hands-free`` when the ``dictation`` app
became the second consumer of the same "polish a speech-to-text transcript"
think call (CLAUDE.md rule 9 — extract on the second caller).

Pure-ish: ``clean_dictation`` takes a ``think_fn`` closure (e.g. ``self.think``),
never raises, and falls back to the input text on any empty/failed model reply.
No ``self``, no kernel access, no I/O — unit-testable with a fake ``think_fn``.

Posture: this polishes punctuation / casing / obvious homophones only. It does
NOT rephrase, shorten, or embellish — the user's words are preserved. The
optional ``vocabulary`` is a short list of the user's own custom terms (names,
project codes, acronyms) injected so the model doesn't "correct" them away. It
is a term list, never vault content — Rule-19 safe.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Callable, Protocol


CLEANUP_PROMPT = (
    "You are a transcription polisher. The text below is a raw speech-to-text "
    "transcript of a short command, capture, or dictated sentence. Your job is narrow:\n"
    "1. Add sensible punctuation and capitalisation.\n"
    "2. Fix OBVIOUS mis-hears caused by homophones (e.g. 'there' vs 'their') "
    "when context makes the correction unambiguous.\n"
    "3. Keep every content word the user said. Do NOT rephrase, shorten, or "
    "embellish.\n"
    "4. Return ONLY the cleaned text — no preamble, no explanation, no quotes."
)


class _ThinkFn(Protocol):
    def __call__(
        self, text: str, *, system: str = ..., domain: str = ..., temperature: float = ...
    ) -> Awaitable[str]: ...


def build_cleanup_system(vocabulary: str = "") -> str:
    """The system prompt, optionally extended with a preserve-these-terms clause.

    Kept separate so callers (and tests) can inspect the exact prompt without a
    model round-trip.
    """
    system = CLEANUP_PROMPT
    vocab = (vocabulary or "").strip()
    if vocab:
        # Collapse newlines/commas into a single comma list so the clause stays
        # one line regardless of how the user typed the setting.
        terms = [t.strip() for t in vocab.replace("\n", ",").split(",") if t.strip()]
        if terms:
            system += (
                "\n5. PRESERVE these exact terms / names / codes verbatim if you "
                "hear something close to them — never 'correct' them: "
                + ", ".join(terms)
                + "."
            )
    return system


async def clean_dictation(
    think_fn: "_ThinkFn | Callable",
    text: str,
    *,
    vocabulary: str = "",
) -> str:
    """Polish a raw STT transcript. Returns cleaned text; falls back to the input.

    ``think_fn`` is called as ``think_fn(text, system=..., domain="text",
    temperature=0.2)`` — i.e. ``self.think`` satisfies it directly. Any empty or
    raising reply yields the original ``text`` (this never raises).
    """
    text = (text or "").strip()
    if not text:
        return ""
    system = build_cleanup_system(vocabulary)
    try:
        cleaned = await think_fn(text, system=system, domain="text", temperature=0.2)
    except Exception:
        return text
    cleaned = (cleaned or "").strip()
    if not cleaned:
        return text
    # Models occasionally wrap the whole reply in quotes despite the instruction.
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) > 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned or text
