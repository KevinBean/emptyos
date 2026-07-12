"""Context Packing — token estimation + budget thresholds.

Dependency-light by design: ``ceil(chars / 4)`` is the same serviceable proxy
used by ``emptyos/sdk/agent_loop.py::_estimate_chars`` for compaction. No
tiktoken; a provider-specific tokenizer can slot in later behind ``est_tokens``.

Pure functions + a dataclass. No I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Default packing thresholds (tokens). Packing should not run on small prompts.
MIN_EST_TOKENS = 8_000
TARGET_EST_TOKENS = 6_000
RESERVE_OUTPUT_TOKENS = 2_000
# Meaningful-savings floor: a reduction must save more than this fraction of
# the block's tokens or it is rejected and the original kept. The packer adds a
# ctx header + recovery-ref line on top of reduced text, so a marginal pack is
# a net loss in tokens AND in fidelity. (Discipline from OpenHuman's TokenJuice
# ratio>0.95 pass-through; reimplemented, no code taken — GPL upstream.)
MIN_REDUCTION_RATIO = 0.05


def est_tokens(text: str) -> int:
    """Rough token count: ceil(chars / 4). Good enough for budgeting."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


@dataclass
class ContextBudget:
    max_input_tokens: int = 12_000
    reserve_output_tokens: int = RESERVE_OUTPUT_TOKENS
    min_est_tokens: int = MIN_EST_TOKENS
    target_est_tokens: int = TARGET_EST_TOKENS
    min_reduction_ratio: float = MIN_REDUCTION_RATIO

    @property
    def usable_input_tokens(self) -> int:
        """Input budget after reserving room for the model's output."""
        return max(0, self.max_input_tokens - self.reserve_output_tokens)
