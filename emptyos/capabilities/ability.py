"""Model ability taxonomy — how *strong* a think provider is.

Orthogonal to cost (free/local/paid, see ``EOS_UI.MODEL_COSTS``): ability is the
intrinsic capability of a model, used to gate features that need a strong model
(viz 3D scenes, long reasoning) and route ``think(min_ability=...)`` to the
strongest reachable provider. Three ordered tiers:

    weak     — tiny/edge models. Good for short structured tasks, classification,
               tags. Garbles complex code / long reasoning. (nano, ≤~3B locals)
    standard — capable workhorse. Diagrams, grounded Q&A, summaries, most code.
               (gpt-4o-mini, ~7-9B locals, haiku)
    strong   — frontier. Intricate code, 3D scenes, deep reasoning, large synthesis.
               (gpt-4o, gpt-5, claude sonnet/opus, claude-cli, ≥~70B, deepseek-r1)

The default ``classify()`` is heuristic (model-name patterns). Operators override
per provider with ``[capabilities.think.<provider>] ability = "strong"``.
"""

from __future__ import annotations

ABILITY_TIERS = ("weak", "standard", "strong")
ABILITY_ORDER = {"weak": 0, "standard": 1, "strong": 2}
DEFAULT_ABILITY = "standard"  # unknown models — safe middle, never over-restrict


def normalize(ability: str | None) -> str:
    """Coerce any value to a known tier (default standard)."""
    a = (ability or "").strip().lower()
    return a if a in ABILITY_ORDER else DEFAULT_ABILITY


def meets(active: str | None, required: str | None) -> bool:
    """True when an ``active`` model is at least as strong as ``required``."""
    if not required:
        return True
    return ABILITY_ORDER[normalize(active)] >= ABILITY_ORDER[normalize(required)]


# Substring markers, checked in priority order. Order matters: a name like
# "gpt-4o-mini" contains both "gpt-4o" (strong) and "mini" (standard) — the
# weak/standard size markers are matched BEFORE the strong family markers so
# the small variant wins.
_WEAK = (
    "nano", "tinyllama", "phi3:mini", "phi-3-mini", "phi3-mini",
    ":0.5b", ":1.5b", ":1b", ":2b", ":3b", "-0.5b", "-1.5b", "-1b", "-2b", "-3b",
    "gemma:2b", "gemma2:2b", "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b",
)
_STANDARD_NAME = ("mini", "haiku")  # gpt-4o-mini, openai-mini, claude-haiku
_STRONG = (
    "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "claude-cli",
    "sonnet", "opus", "deepseek-r1", "deepseek-v3", "command-r-plus",
    "-pro", "gemini-1.5-pro", "gemini-2.0-pro", "gemini-2.5-pro",
    ":32b", ":70b", ":72b", ":405b", "8x22b",
)
_STANDARD_SIZE = (":7b", ":8b", ":9b", ":13b", ":14b", "-7b", "-8b", "mixtral:8x7b", "mistral")


def classify(provider_name: str = "", model: str = "") -> str:
    """Best-effort ability tier from a provider name + model string."""
    blob = f"{provider_name or ''} {model or ''}".lower()
    if any(m in blob for m in _WEAK):
        return "weak"
    if any(m in blob for m in _STANDARD_NAME):
        return "standard"
    if any(m in blob for m in _STRONG):
        return "strong"
    if any(m in blob for m in _STANDARD_SIZE):
        return "standard"
    return DEFAULT_ABILITY
