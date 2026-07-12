"""Context Packing reducers — deterministic, query-independent structural shrink.

Phase 1 ships only reducers whose output does not depend on a query, so they
can run safely without knowing what the model is looking for:

    log, jsonl, json, search_result, diff

Relevance-dependent kinds (code, markdown, conversation_history) are deferred
until an LLM reducer or a real consumer needs them — a deterministic reducer
that "keeps the relevant section" would drop the answer it can't identify.

Each reducer is ``reduce(text: str) -> ReducerResult`` and must never raise on
ordinary input (return verbatim + a warning instead). The packer still wraps
every call in the fail-open contract as a backstop.
"""

from __future__ import annotations

from collections.abc import Callable

from ..blocks import ReducerResult
from . import diff, json_data, jsonl, logs, search_results

# Map a block ``kind`` to its reducer. Keys match ``blocks.REDUCIBLE_KINDS``.
REDUCERS: dict[str, Callable[[str], ReducerResult]] = {
    "log": logs.reduce,
    "jsonl": jsonl.reduce,
    "json": json_data.reduce,
    "search_result": search_results.reduce,
    "diff": diff.reduce,
}


def get_reducer(kind: str) -> Callable[[str], ReducerResult] | None:
    return REDUCERS.get(kind)
