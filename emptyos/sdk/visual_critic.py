"""Visual critic — score a rendered 3D-model image against a text intent.

Extracted from ``apps/personal/robot-modeller/critic.py`` (the first
consumer) per CLAUDE.md rule 9 — ``apps/extension/engineering/cad``'s part
generation is the second. The MECHANISM (build the vision-LLM chat messages,
parse the graded JSON reply, format a ``<visual_signals>`` feedback block) is
shared; the SYSTEM PROMPT stays app-owned (rule 12 — named per-app), since
what "matches intent" means differs by domain: robot-modeller judges
articulation/kinematic layout across a jointed assembly, eos-cad judges a
single parametric part's topology/proportions with no joints at all. Callers
pass their own system prompt string; this module never opines on grading
criteria.

Why this exists (from the original robot-modeller docstring, still true):
compile/structural-validation signals tell the LLM whether its output is
well-formed. They say nothing about whether it produced "a desk lamp" vs "a
cylindrical lump." The visual critic closes that gap — at the cost of a
cloud vision call per turn. Strictly opt-in per consumer.

Call the built messages via plain ``self.think(messages=..., temperature=...)``
— don't pin a domain; let the capability chain route freely. Both shipped
providers handle an ``image_url`` content block: the openai-image plugin's
``OpenAIVisionProvider`` forwards it natively to gpt-4o-mini, and claude-cli
rewrites it to a temp-file Read-tool directive
(``emptyos/capabilities/providers/claude_cli.py``). Either way the existing
cloud-consent gate fires on the underlying provider per CLAUDE.md Rule 18 —
this module doesn't need to force it.
"""

from __future__ import annotations

import base64

from .utils import parse_llm_json


def parse_critique(reply: str) -> tuple[dict | None, str]:
    """Extract the critique JSON from a vision-LLM reply.

    Returns ``(critique_dict, error_msg)``. On success ``error_msg`` is "".
    Required field: ``score`` (int 1-10). Optional: ``matches``,
    ``mismatches``, ``suggestions`` (lists of strings, missing/malformed
    entries dropped rather than raising — providers sometimes omit empty
    arrays or emit a non-string item).
    """
    try:
        data = parse_llm_json(reply)
    except ValueError as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "critique reply was not a JSON object"
    score = data.get("score")
    if not isinstance(score, int) or not (1 <= score <= 10):
        return None, f"score must be int 1..10, got {score!r}"
    return {
        "score": score,
        "matches": [s for s in (data.get("matches") or []) if isinstance(s, str)],
        "mismatches": [s for s in (data.get("mismatches") or []) if isinstance(s, str)],
        "suggestions": [s for s in (data.get("suggestions") or []) if isinstance(s, str)],
    }, ""


def build_critic_messages(image_bytes: bytes, intent: str, *, system: str,
                           mime: str = "image/png") -> list[dict]:
    """Build the messages array for the vision-LLM call.

    Encodes the image as a base64 data URL inline — the shape OpenAI's chat
    completions API (and this codebase's ``OpenAIVisionProvider``) expects
    for an ``image_url`` content block.
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"INTENT: {intent.strip()}\n\nGrade the render below."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def format_visual_signals(critique: dict) -> str:
    """Render a parsed critique as a ``<visual_signals>`` block — mirrors the
    ``<compile_signals>``/``<shape_signals>`` shape so a repair prompt can
    splice structural and visual feedback together with no per-format glue.
    """
    lines = ["<visual_signals>", f"  score: {critique.get('score')} / 10"]
    for s in critique.get("matches", []):
        lines.append(f"  [match]      {s}")
    for s in critique.get("mismatches", []):
        lines.append(f"  [mismatch]   {s}")
    for s in critique.get("suggestions", []):
        lines.append(f"  [suggestion] {s}")
    lines.append("</visual_signals>")
    return "\n".join(lines)
