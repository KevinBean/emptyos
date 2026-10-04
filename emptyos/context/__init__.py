"""EmptyOS Context Packing — native, fail-open token compression.

Prepares material BEFORE an EmptyOS LLM call, stores originals behind stable
``ctx_*`` refs for retrieval, and fails open when anything goes wrong. It never
proxies a provider's wire protocol (the transparent-proxy approach is what
wedged the blacklisted Headroom plugin). See ``docs/CONTEXT-PACKING.md``.

Public API::

    from emptyos.context import pack_context, pack_text, ContextBlock, ContextBudget

The packer is synchronous; callers on an async path MUST run it via
``await asyncio.to_thread(...)`` (Async Boundary rule).
"""

from __future__ import annotations

from .blocks import ContextBlock, OriginalRef, PackResult, ReducerResult
from .budget import ContextBudget, est_tokens
from .packer import pack_context, pack_text
from .store import load_original, save_original

__all__ = [
    "ContextBlock",
    "ContextBudget",
    "OriginalRef",
    "PackResult",
    "ReducerResult",
    "est_tokens",
    "pack_context",
    "pack_text",
    "load_original",
    "save_original",
]
