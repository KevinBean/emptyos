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
