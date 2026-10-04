# Voice-specific shim over emptyos.sdk.intents.
# Re-exports the SDK helpers and pre-binds INTENT_PROMPT_HEADER (voice TTS-
# discipline framing) into render_intent_block so app.py + chat_pipeline.py
# don't have to pass it on every call. Behavior unchanged from the previous
# in-app module.

import re

from emptyos.sdk.intents import (  # noqa: F401  (re-export)
    INTENT_RE,
    MAX_INTENTS_IN_PROMPT,
    build_plan_dict,
    find_intents,
    intent_embedding_text,
    scope_intents,
    scope_intents_by_relevance,
    validate_args,
)
from emptyos.sdk.intents import render_intent_block as _sdk_render_intent_block

from .prompts import PROMPTS


def render_intent_block(scoped: list[dict]) -> str:
    """Voice-bound render — uses the strict TTS-aware header."""
    return _sdk_render_intent_block(scoped, header=PROMPTS.intent_prompt_header)


# Affect channel — a parallel, optional [EMOTION:<label>] marker the model may
# emit at the start of a reply. Stripped from text + TTS on the same streaming
# drain as INTENT tokens (chat_pipeline.py); surfaced as an `emotion` stream
# event the frontend lands on body[data-emotion]. Borrowed (the discipline, not
# the code) from VTuber companion stacks.
EMOTION_RE = re.compile(r"\[EMOTION:\s*([a-z]+)\s*\]", re.IGNORECASE)
VALID_EMOTIONS = {
    "neutral", "joy", "curious", "thoughtful",
    "concerned", "excited", "calm", "playful",
}

# Content card — a text-rail-only channel that lets a CHAT answer carry rich /
# list-shaped detail in a card instead of a wall of prose ("concise answer +
# card for the richness"). The model emits, in text_only mode only:
#   [CARD:list title="Focus tips"]
#   - Two-minute start
#   - Single-tab block
#   [/CARD]
# The streaming parser strips the block from the spoken/prose text and turns it
# into a `card` event. Held back until [/CARD] arrives so the raw marker never
# flashes. `title` is optional; the body is one `- item` per line.
CARD_RE = re.compile(
    r'\[CARD:\s*(\w+)\s*(?:title="([^"]*)")?\s*\](.*?)\[/CARD\]',
    re.IGNORECASE | re.DOTALL,
)


def parse_content_card(renderer: str, title: str, body: str) -> dict | None:
    """Turn a matched [CARD:...] block into a card event dict, or None if empty.

    v1 supports the `list` renderer (→ the shared task-list card): each ``- item``
    line becomes a row. Unknown renderers fall back to a list so a stray shape
    still renders something rather than vanishing.
    """
    rows = []
    for line in (body or "").splitlines():
        # Strip a leading bullet (- * •) or numbered (1. / 1)) marker.
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if s:
            rows.append({"text": s})
    if not rows:
        return None
    return {
        "type": "card",
        "intent": "",
        "renderer": "task-list",
        "title": (title or "").strip(),
        "data": rows,
    }
