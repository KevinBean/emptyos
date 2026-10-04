"""Context Packing — typed blocks + result shapes.

The unit of work is a typed :class:`ContextBlock`, not a raw prompt string.
Protected kinds are NEVER reduced; the policy is enforced here so no reducer
or caller can accidentally compress a system prompt, consent text, or the
current user request.

Pure data + classification. No I/O, no kernel access, no LLM calls.
See ``docs/CONTEXT-PACKING.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Kinds whose text is never compressed, byte-for-byte preserved in output.
PROTECTED_KINDS: frozenset[str] = frozenset(
    {
        "system",
        "developer_rule",
        "permission",
        "consent",
        "tool_schema",
        "current_user_request",
    }
)

# Compressible kinds that have a deterministic Phase-1 reducer. Kinds outside
# this set (e.g. "code", "markdown", "conversation_history") are deferred —
# they need query-awareness or an LLM reducer — and pass through verbatim.
REDUCIBLE_KINDS: frozenset[str] = frozenset(
    {"log", "jsonl", "json", "search_result", "diff"}
)


@dataclass
class ContextBlock:
    """One typed piece of material headed for an LLM call.

    ``protected`` is auto-derived from ``kind`` in ``__post_init__`` so a caller
    can't forget to mark a system prompt protected; an explicit ``protected=True``
    also wins (belt-and-suspenders for custom kinds).
    """

    kind: str
    text: str
    subtype: str = ""
    source: str = ""
    priority: str = "medium"  # high | medium | low
    reversible: bool = True
    protected: bool = False

    def __post_init__(self) -> None:
        if self.kind in PROTECTED_KINDS:
            self.protected = True

    @property
    def reducible(self) -> bool:
        return (not self.protected) and self.kind in REDUCIBLE_KINDS


@dataclass
class OriginalRef:
    """Pointer to a stored original, recoverable until TTL expiry."""

    id: str
    source: str
    kind: str
    sha256: str
    ttl_hours: int = 24


@dataclass
class ReducerResult:
    """What a single deterministic reducer returns.

    ``refs`` stays empty — original storage + ref minting is the packer's job,
    not the reducer's (reducers are pure, I/O-free).
    """

    text: str
    original_tokens: int
    packed_tokens: int
    refs: list[OriginalRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PackResult:
    """Output of :func:`pack_context`.

    ``text`` is prompt-ready. ``refs`` maps each minted ``ctx_*`` id to its
    stored original. ``degraded`` is True when protected blocks alone blew the
    budget (returned unchanged). ``fail_open`` is True when packing raised and
    the original input was returned verbatim.
    """

    text: str
    refs: dict[str, OriginalRef] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    degraded: bool = False
    fail_open: bool = False
