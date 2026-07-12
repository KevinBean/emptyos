"""Behavioral modes for the agent (eos chat) — sticky personas (Step 2).

A *mode* is a named behavior profile layered onto the system prompt for a whole
session — "research", "code", "strategy". It is distinct from the ask/auto/plan
*permission* modes (those gate tools; these shape how the agent thinks). A mode is
manually switched with `/mode <name>` and its prompt is re-injected every turn
until changed, so it sticks across the session.

Gated behind ``feature.agent-modes.enabled`` (dark default). Built-in modes can be
overridden or extended by ``data/apps/agent/modes.json`` (a list of mode dicts, or
``{"modes": [...]}``) — same shape as the built-ins below; matching ``id`` wins.

Pure data + formatting only — the app owns the in-memory per-session active-mode
state (``self._agent_modes``) and the ``agent.default_mode`` setting.
"""

from __future__ import annotations

import json

DEFAULT_MODES = [
    {
        "id": "code",
        "name": "Code",
        "emoji": "🛠️",
        "system_prompt": (
            "MODE: Code. Bias toward shipping working code. Read the relevant files "
            "before you edit, follow CLAUDE.md conventions and the "
            "build → conform → walk → simplify → verify loop, keep diffs minimal, and "
            "run the relevant tests. Prefer reusing existing SDK helpers over new code."
        ),
    },
    {
        "id": "research",
        "name": "Research",
        "emoji": "🔍",
        "system_prompt": (
            "MODE: Research. Investigate before concluding. Read the actual sources "
            "(code, files, the vault) — never answer from memory on something checkable. "
            "Triangulate every load-bearing claim against at least two independent "
            "sources, try to refute your own findings, and grade them (read-verified vs "
            "inferred). Prefer a cited, honest report over a confident guess. Don't edit "
            "files unless the user explicitly asks."
        ),
    },
    {
        "id": "strategy",
        "name": "Strategy",
        "emoji": "🧭",
        "system_prompt": (
            "MODE: Strategy. Think long-horizon and decision-first. Surface the real "
            "tradeoffs, name the load-bearing assumptions, give a recommendation rather "
            "than a survey, and state what would change it. Keep the human's judgment in "
            "the loop — propose, don't decide irreversible things. Read the relevant "
            "vault/context before advising; don't edit files unless asked."
        ),
    },
]


def load_modes(app) -> dict:
    """Built-in modes merged with user overrides in ``data/apps/agent/modes.json``.

    A user entry with the same ``id`` overrides the built-in (shallow merge), so a
    user can retune a built-in prompt or add brand-new modes without touching code.
    """
    modes = {m["id"]: dict(m) for m in DEFAULT_MODES}
    try:
        p = app.data_dir / "modes.json"
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            entries = raw if isinstance(raw, list) else (raw.get("modes") or [])
            for m in entries:
                if isinstance(m, dict) and m.get("id"):
                    modes[m["id"]] = {**modes.get(m["id"], {}), **m}
    except Exception:
        pass
    return modes


def mode_block(mode: dict) -> str:
    """Frame a mode's persona as a system-prompt block. Empty when no prompt."""
    sp = (mode.get("system_prompt") or "").strip()
    return sp
