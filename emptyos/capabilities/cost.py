"""Cost estimation for capability calls — the price table behind RunBudget.

`estimate_cost(capability, provider, **params) -> float` returns an *approximate*
USD cost for one capability call, used by `emptyos/sdk/run_budget.py` to gate
per-run spend before an expensive call fires. Borrowed 2026-06-21 from
OpenMontage's pre-call cost estimation (idea only).

Two deliberate policies:

- **Local providers are free.** ollama / comfyui / edge-tts / kokoro / xtts /
  piper / voice-api / whisper are local compute → $0. claude-cli is billed to a
  subscription, not per-token → also $0 here.
- **Unknown *capability* → $0 (fail-open); unknown *provider* on a known cloud
  capability → a conservative cloud default.** A budget that silently bills an
  un-named cloud call to $0 is useless, so think/draw/animate/speak with no
  resolvable provider fall back to a typical cloud rate. This can only ever
  *over*-estimate (protecting a cap), never under-bill. Pass the real provider
  to get an accurate figure.

Rates are order-of-magnitude, not invoices — refine the tables as real spend
data arrives (the run-budget snapshot in `run.json` is the evidence trail).
Module-level + kernel-free so it unit-tests without a daemon.
"""

from __future__ import annotations

# Known-local provider name prefixes → $0. claude-cli = subscription → $0 here.
_LOCAL_PREFIXES = (
    "ollama", "comfyui", "edge-tts", "kokoro", "xtts", "piper",
    "voice-api", "whisper", "blender", "claude-cli", "human",
)

# think: USD per 1K tokens (blended). Conservative cloud default for unknown.
_THINK_PER_1K = {
    "openai": 0.005,
    "openai-mini": 0.0005,
    "openai-nano": 0.0003,
    "openrouter": 0.001,
}
_DEFAULT_THINK_PER_1K = 0.005

# draw / animate: USD per image/clip. Conservative cloud default for unknown.
_IMAGE_FLAT = {
    "openai-image": 0.04,
    "runway": 0.05,
    "luma": 0.04,
    "kling": 0.04,
}
_DEFAULT_IMAGE = 0.04

# speak: USD per character. Conservative cloud default for unknown.
_TTS_PER_CHAR = {
    "openai-tts": 0.000015,
}
_DEFAULT_TTS_PER_CHAR = 0.000015


def _is_local(provider: str) -> bool:
    return any(provider.startswith(p) for p in _LOCAL_PREFIXES)


def _lookup(provider: str, table: dict, cloud_default: float) -> float:
    """Exact, then prefix match, then the conservative cloud default. A local
    provider always resolves to 0 before this is reached."""
    if provider in table:
        return table[provider]
    for key, rate in table.items():
        if provider.startswith(key):
            return rate
    return cloud_default


def _tokens(params: dict) -> float:
    """Best-effort token count from whatever a caller passed."""
    if params.get("tokens") is not None:
        try:
            return max(0.0, float(params["tokens"]))
        except (TypeError, ValueError):
            return 0.0
    it = params.get("input_tokens")
    ot = params.get("output_tokens")
    if it is not None or ot is not None:
        try:
            return max(0.0, float(it or 0) + float(ot or 0))
        except (TypeError, ValueError):
            return 0.0
    return _chars(params) / 4.0  # ~4 chars/token


def _chars(params: dict) -> float:
    if params.get("chars") is not None:
        try:
            return max(0.0, float(params["chars"]))
        except (TypeError, ValueError):
            return 0.0
    for key in ("text", "prompt"):
        v = params.get(key)
        if isinstance(v, str):
            return float(len(v))
        if isinstance(v, (list, tuple)):
            return float(sum(len(x) for x in v if isinstance(x, str)))
    return 0.0


def estimate_cost(capability: str, provider: str | None = None, **params) -> float:
    """Approximate USD for one capability call. Never raises — returns 0.0 on
    anything it can't price (the caller, RunBudget, also fails open)."""
    cap = (capability or "").lower()
    prov = (provider or "").lower()
    if prov and _is_local(prov):
        return 0.0
    # An empty/unknown provider flows through _lookup to the conservative cloud
    # default (never under-bills a cap); a local provider returned above.
    try:
        if cap == "think":
            return _tokens(params) / 1000.0 * _lookup(prov, _THINK_PER_1K, _DEFAULT_THINK_PER_1K)
        if cap in ("draw", "animate"):
            n = max(0, int(params.get("n", 1) or 1))
            return n * _lookup(prov, _IMAGE_FLAT, _DEFAULT_IMAGE)
        if cap == "speak":
            return _chars(params) * _lookup(prov, _TTS_PER_CHAR, _DEFAULT_TTS_PER_CHAR)
    except Exception:
        return 0.0
    return 0.0  # footage / read / search / unknown capability → unpriced
