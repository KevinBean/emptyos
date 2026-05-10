# Voice-specific shim over emptyos.sdk.intents.
# Re-exports the SDK helpers and pre-binds INTENT_PROMPT_HEADER (voice TTS-
# discipline framing) into render_intent_block so app.py + chat_pipeline.py
# don't have to pass it on every call. Behavior unchanged from the previous
# in-app module.

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

from .prompts import INTENT_PROMPT_HEADER


def render_intent_block(scoped: list[dict]) -> str:
    """Voice-bound render — uses the strict TTS-aware header."""
    return _sdk_render_intent_block(scoped, header=INTENT_PROMPT_HEADER)
