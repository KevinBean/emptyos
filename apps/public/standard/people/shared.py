"""people — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so helper modules (ai_surfaces/engagement/workload) can import these directly
without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O.
"""

from __future__ import annotations

import re
from pathlib import Path


_DEFAULT_ROLE = "assignee"

_FILENAME_PREFIX = "@"  # legacy contacts convention; new notes use id-based names

AI_SUGGEST_SYSTEM = (
    "You are a relationship coach. Given a contact list and overdue flags, "
    "pick exactly 3 people the user should reach out to this week.\n"
    "Weight: overdue + low-recent-contact + energy='gives' + higher trust_level.\n"
    "Do NOT: pick three overdue people if that ignores energy/trust; invent "
    "names not in the list; return more or fewer than 3; wrap JSON in markdown "
    "fences; add commentary before/after the array."
)

AI_SUGGEST_FORMAT = (
    "Return a JSON array of exactly 3 objects:\n"
    '[{"name": "exact name from list", "action": "call/message/coffee/meal", '
    '"reason": "1 sentence why now", "opener": "suggested opening message"}]\n'
    "Return ONLY valid JSON."
)

CHAT_SYSTEM = (
    "You are a personal relationship assistant. Answer the user's question "
    "about a specific person based ONLY on the information provided. "
    "If the info doesn't contain the answer, say so plainly.\n"
    "Do NOT: invent facts about the person; speculate about their inner state "
    "beyond what the notes support; answer as if you are the person."
)

PERSONA_SYSTEM = (
    "You describe a person's personality in 2-3 sentences based only on "
    "recorded interactions and notes about them. Focus on communication "
    "style, values, and relationship dynamics.\n"
    "Do NOT: use clinical labels (narcissist/introvert/etc.); speculate "
    "about childhood or trauma; flatter; exceed 3 sentences."
)

CLARITY_SYSTEM = (
    "You judge whether a one-line task description gives enough information to "
    "confidently decide who or what should do it — is it clear what needs doing, "
    "and roughly what 'done' looks like? A short task can still be clear (e.g. "
    "'file this month's expense report') if the action and target are unambiguous.\n"
    "Do NOT: mark a task unclear just because it's short or lacks polish; ask for "
    "detail that wouldn't change who should do it; nitpick phrasing."
)

CLARIFY_QUESTION_SYSTEM = (
    "The task below is too ambiguous to confidently decide who should do it. Ask "
    "ONE short, specific question that resolves the biggest ambiguity — the one "
    "that would most change who or what should handle it.\n"
    "Do NOT: ask more than one question; ask about something that wouldn't change "
    "the delegation decision; use a vague prompt like 'can you clarify?'."
)

DELEGATE_SYSTEM = (
    "You help decide, for one task, whether the user should do it themselves, "
    "hand it to a specific human collaborator, or delegate it to a specific AI "
    "model or AI agent — choosing exactly one candidate from the list given. "
    "Favor the user or a human when the task needs judgment, taste, or "
    "relationship context; favor an AI model or agent when the task is "
    "bounded, mechanical, or repeatable. Weigh stated skills, current "
    "capacity/load, and cost — prefer a local or free AI over a paid cloud "
    "one when quality doesn't matter, and never send clearly private or "
    "personal content to a paid cloud AI. When uncertain, prefer the user "
    "doing it themselves over over-delegating.\n"
    "Do NOT: invent a candidate not in the list; ignore an overloaded "
    "human's load; recommend an unavailable AI provider; give more than one "
    "sentence when asked to explain a pick."
)
