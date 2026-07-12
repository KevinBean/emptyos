"""Context Packing — orchestration + the fail-open contract.

``pack_context`` is the one primary entry point. It is **synchronous and
deterministic** (no LLM calls in Phase 1). Per the Async Boundary rule in
``docs/CONTEXT-PACKING.md``, any caller on an async path (``BaseApp.think``,
the agent tool loop) MUST invoke it via ``await asyncio.to_thread(...)`` so the
reducer regex + the store's disk writes never block the event loop.

Fail-open is not optional: any exception returns the original input verbatim
and records a ``fail_open`` trace. A packing bug must never break an LLM call.
"""

from __future__ import annotations

from pathlib import Path

from .blocks import ContextBlock, OriginalRef, PackResult
from .budget import ContextBudget, est_tokens
from .reducers import get_reducer
from .store import save_original
from .trace import append_trace, trace_fail_open


def _verbatim(blocks: list[ContextBlock]) -> str:
    return "\n\n".join(b.text for b in blocks)


def _ctx_header(ref_id: str, source: str, original_tok: int, packed_tok: int) -> str:
    label = source or "unnamed"
    return f"[ctx:{ref_id} {label}, original {original_tok} est tok -> {packed_tok} est tok]"


def pack_context(
    blocks: list[ContextBlock],
    budget: ContextBudget | None = None,
    *,
    profile: str = "think",
    store_root: Path | None = None,
) -> PackResult:
    """Pack typed blocks into prompt-ready text. Never raises."""
    budget = budget or ContextBudget()
    try:
        return _pack(blocks, budget, profile=profile, store_root=store_root)
    except Exception as exc:  # fail-open backstop
        trace_fail_open(store_root, exc)
        return PackResult(text=_verbatim(blocks), fail_open=True)


def _pack(
    blocks: list[ContextBlock],
    budget: ContextBudget,
    *,
    profile: str,
    store_root: Path | None,
) -> PackResult:
    parts: list[str] = []
    refs: dict[str, OriginalRef] = {}
    protected_tokens = 0
    original_total = 0
    packed_total = 0
    used_reducers: list[str] = []

    for block in blocks:
        block_orig_tok = est_tokens(block.text)
        original_total += block_orig_tok

        if block.protected or not block.reducible:
            parts.append(block.text)
            packed_total += block_orig_tok
            if block.protected:
                protected_tokens += block_orig_tok
            continue

        reducer = get_reducer(block.kind)
        if reducer is None:
            parts.append(block.text)
            packed_total += block_orig_tok
            continue

        result = reducer(block.text)
        # Only accept the reduction when it saves a meaningful fraction — the
        # ctx header + recovery-ref line eat the gains of a marginal pack.
        # (>= so min_reduction_ratio=0.0 is exactly the pre-floor contract:
        # zero-savings packs still rejected.)
        if result.packed_tokens >= block_orig_tok * (1.0 - budget.min_reduction_ratio):
            parts.append(block.text)
            packed_total += block_orig_tok
            continue

        used_reducers.append(block.kind)
        ref_line = ""
        if block.reversible and store_root is not None:
            ref = save_original(
                store_root, source=block.source, kind=block.kind, text=block.text
            )
            refs[ref.id] = ref
            header = _ctx_header(
                ref.id, block.source, block_orig_tok, result.packed_tokens
            )
            ref_line = f"\nAsk for {ref.id} if exact lines are needed."
            parts.append(f"{header}\n{result.text}{ref_line}")
        else:
            parts.append(result.text)
        packed_total += result.packed_tokens

    degraded = protected_tokens > budget.usable_input_tokens
    text = "\n\n".join(parts)
    reduction_pct = (
        round(100 * (1 - packed_total / original_total), 1) if original_total else 0.0
    )
    stats = {
        "profile": profile,
        "original_est_tokens": original_total,
        "packed_est_tokens": packed_total,
        "reduction_pct": reduction_pct,
        "reducers": used_reducers,
        "refs": len(refs),
        "degraded": degraded,
    }
    append_trace(store_root, {"fail_open": False, **stats})
    return PackResult(text=text, refs=refs, stats=stats, degraded=degraded)


def pack_text(
    text: str,
    kind: str,
    *,
    source: str = "",
    store_root: Path | None = None,
    budget: ContextBudget | None = None,
    profile: str = "tool_output",
) -> PackResult:
    """Convenience: pack a single piece of text of a known kind.

    Used by the agent tool-output hook and the ``eos context pack`` CLI.
    """
    block = ContextBlock(kind=kind, text=text, source=source)
    return pack_context([block], budget, profile=profile, store_root=store_root)
