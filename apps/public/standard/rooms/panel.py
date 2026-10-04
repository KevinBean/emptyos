"""Rooms — panel mode: diverge → moderate → converge multi-agent forum.

Extracted as its own module to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the moderated multi-agent discussion loop. A normal room turn
resolves exactly ONE responder (`_resolve_responder_id`); panel mode is the
opposite — it drives every agent participant as a forum:

  round 1   — every agent gives a BLIND, independent take (concurrent; no peer
              context), so the discussion isn't anchored on whoever spoke first
  moderator — a neutral host LLM reads the round, names the single open
              disagreement, and decides whether the panel has converged
  round 2+  — every agent (concurrent) responds to the *moderator's* flagged
              point + the previous round's takes, not to a growing raw dump
  …repeat until the moderator says converged, or rounds is hit…
  synthesis — a final neutral pass naming agreement / disagreement / one rec

This is a moderated-forum shape (a neutral host that steers between rounds)
rather than the older drift-then-summarise shape. Each turn — agent takes AND
moderator notes — is appended to room history so the forum renders in the normal
room UI. For the cheap one-call alternative (no turn-by-turn artifact, single
model) use `emptyos.sdk.multi_lens.multi_lens_analyze` instead.

Reuses the single-turn machinery from chat.py via ``self`` (``_build_system``,
``think``) rather than re-implementing prompt assembly.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: ``_build_system`` (chat.py), ``_normalize_participants``
(rooms_core/participants), ``_load_agent`` / ``_load_history`` / ``_save_history``
(agents.py). Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.utils import parse_llm_json

if TYPE_CHECKING:
    from .app import RoomsApp  # noqa: F401 — for type hints only


# ─── Bind to RoomsApp class as ───────────────────────────────────────
#   run_panel      = _panel.run_panel
#   api_run_panel  = _panel.api_run_panel
# (_transcript_text / _forum_host are module-level helpers, not bound.)
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

_PANEL_MAX_ROUNDS = 4
_MODERATOR_SYSTEM = (
    "You are a fair, decisive panel moderator. You do not have opinions of "
    "your own about the subject; your only job is to fairly distil what the "
    "panel of experts said into a usable conclusion. Be concrete and brief."
)
_FORUM_HOST_SYSTEM = (
    "You are the moderator of an expert panel. You hold no opinion on the "
    "subject — you only steer the discussion. After each round you read every "
    "expert's contribution and reply with STRICT JSON only, no prose:\n"
    '{"summary": "<2-3 sentence neutral state of the discussion>", '
    '"focus": "<the single most important open disagreement or gap the next '
    'round must resolve>", "converged": <true|false>}\n'
    "Set converged=true ONLY when the experts substantively agree and another "
    "round would add nothing new."
)

# Per-round instruction fragments appended to each agent's user message. The
# persona lives in each agent's own system prompt; these only steer *this turn*.
_INSTR_OPENING = (
    "Give your opening take on the question, from your discipline only. Be "
    "specific and brief — a few sentences. You are speaking independently and "
    "cannot see the other experts yet, so commit to your own view."
)
_INSTR_REACT = (
    "Respond to the moderator's flagged point — agree, disagree, or sharpen "
    "it. Add new signal; don't repeat what's been said. Brief."
)
_INSTR_FINAL = (
    "Final round. Address the point the moderator flagged, then commit to your "
    "recommendation in one or two sentences. Where you still disagree, say so "
    "plainly."
)


def _transcript_text(transcript: list[dict]) -> str:
    return "\n\n".join(f"{t['name']}: {t['text']}" for t in transcript)


async def _forum_host(
    self, question: str, transcript: list[dict], rnd: int, rounds: int,
) -> dict:
    """Neutral moderator pass between rounds: distil the discussion, name the one
    open point the next round must resolve, and judge convergence.

    Returns ``{"summary", "focus", "converged"}`` — fails soft to empty/False so
    a bad moderator response never aborts the forum.
    """
    convo = _transcript_text(transcript)
    prompt = (
        f"PANEL QUESTION:\n{question}\n\n"
        f"Round {rnd} of at most {rounds} just finished. Everything the panel "
        f"has said so far:\n{convo}\n\n"
        "Output the moderator JSON now."
    )
    try:
        raw = await self.think(
            prompt, system=_FORUM_HOST_SYSTEM, domain="text", temperature=0.2)
        data = parse_llm_json(raw, {}) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "summary": (data.get("summary") or "").strip(),
        "focus": (data.get("focus") or "").strip(),
        "converged": bool(data.get("converged")),
    }


async def run_panel(
    self,
    room_id: str,
    question: str,
    *,
    rounds: int = 2,
    synthesize: bool = True,
) -> dict:
    """Run a moderated forum over *question* across every agent participant.

    Each round, all agents speak concurrently (round 1 blind/independent;
    round 2+ guided by the moderator's flagged point). A neutral moderator runs
    between rounds and can end the forum early on convergence. Returns the full
    transcript + final synthesis. The actual number of rounds run (which may be
    fewer than *rounds* on early convergence) is reported back as ``rounds``.
    """
    room = self._load_agent(room_id)
    if not room:
        return {"error": f"room '{room_id}' not found"}
    if not question:
        return {"error": "question required"}

    parts = self._normalize_participants(room)
    responders = []
    for p in parts:
        if p.get("type") == "agent":
            a = self._load_agent(p["id"])
            if a:
                responders.append(a)
    if len(responders) < 2:
        return {"error": "panel mode needs at least 2 agent participants"}

    rounds = max(1, min(int(rounds or 1), _PANEL_MAX_ROUNDS))

    history = self._load_history(room_id)
    now = datetime.now(timezone.utc).isoformat()
    history.append({"role": "user", "text": question, "ts": now,
                    "panel": {"kind": "question"}})

    async def _one_take(r: dict, prompt: str) -> str:
        kwargs: dict = {"system": self._build_system(r, None), "domain": "text"}
        if r.get("provider"):
            kwargs["provider"] = r["provider"]
        if r.get("model"):
            kwargs["model"] = r["model"]
        if r.get("temperature") is not None:
            kwargs["temperature"] = r["temperature"]
        if r.get("effort"):
            kwargs["effort"] = r["effort"]
        try:
            return await self.think(prompt, **kwargs)
        except Exception as e:  # one agent failing must not abort the panel
            return f"(no response — {type(e).__name__})"

    transcript: list[dict] = []
    mod = {"summary": "", "focus": "", "converged": False}
    rounds_run = 0

    for rnd in range(1, rounds + 1):
        rounds_run = rnd
        final = rnd == rounds

        prompts: list[str] = []
        for r in responders:
            if rnd == 1:
                ctx = ""
                instr = _INSTR_OPENING
            else:
                prev = [t for t in transcript if t["round"] == rnd - 1]
                prev_txt = _transcript_text(prev) or "(none)"
                ctx = (
                    f"MODERATOR'S READ OF THE DISCUSSION:\n"
                    f"{mod['summary'] or '(none)'}\n\n"
                    f"THE PANEL MUST NOW RESOLVE:\n"
                    f"{mod['focus'] or 'converge on a single recommendation'}\n\n"
                    f"WHAT THE PANEL SAID LAST ROUND:\n{prev_txt}\n\n"
                )
                instr = _INSTR_FINAL if final else _INSTR_REACT
            prompts.append(
                f"PANEL QUESTION:\n{question}\n\n{ctx}"
                f"You are {r.get('name', r['id'])}. {instr}"
            )

        # Whole round runs concurrently — no intra-round order anchoring.
        texts = await asyncio.gather(
            *[_one_take(r, p) for r, p in zip(responders, prompts)])
        round_ts = datetime.now(timezone.utc).isoformat()
        for r, text in zip(responders, texts):
            transcript.append({"name": r.get("name", r["id"]), "id": r["id"],
                               "round": rnd, "text": text})
            history.append({
                "role": "assistant", "text": text, "ts": round_ts,
                "actor": {"type": "agent", "id": r["id"]},
                "panel": {"round": rnd},
            })

        # Moderator steers between rounds (the final round is closed by synthesis).
        if not final:
            mod = await _forum_host(self, question, transcript, rnd, rounds)
            if mod["summary"] or mod["focus"]:
                note = mod["summary"]
                if mod["focus"]:
                    note = (note + "\n\n" if note else "") + f"**Next:** {mod['focus']}"
                history.append({
                    "role": "assistant",
                    "text": "**Moderator**\n\n" + note,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "actor": {"type": "agent", "id": "moderator"},
                    "panel": {"round": rnd, "kind": "moderator"},
                })
            if mod["converged"]:
                break

    synthesis = ""
    if synthesize:
        convo = _transcript_text(transcript)
        syn_prompt = (
            f"The panel was asked:\n{question}\n\n"
            f"Full discussion ({rounds_run} round(s)):\n{convo}\n\n"
            "Distil this into: (1) points of AGREEMENT across the panel, "
            "(2) the key DISAGREEMENT(s) and who is on which side, and "
            "(3) a single concrete RECOMMENDATION the panel converges on "
            "(or the closest thing to it). Be decisive and brief; use short "
            "headers."
        )
        try:
            synthesis = await self.think(
                syn_prompt, system=_MODERATOR_SYSTEM, domain="text",
                temperature=0.4)
        except Exception as e:
            synthesis = f"(synthesis failed — {type(e).__name__})"
        history.append({
            "role": "assistant",
            "text": "**Panel synthesis (moderator)**\n\n" + synthesis,
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": {"type": "agent", "id": "moderator"},
            "panel": {"kind": "synthesis"},
        })

    self._save_history(room_id, history)
    await self.emit("rooms:panel", {
        "room_id": room_id, "rounds": rounds_run,
        "responders": len(responders), "synthesized": bool(synthesize),
        "converged": bool(mod["converged"]),
    })
    return {
        "room_id": room_id,
        "question": question,
        "rounds": rounds_run,
        "converged": bool(mod["converged"]),
        "transcript": transcript,
        "synthesis": synthesis,
    }


@web_route("POST", "/api/rooms/{room_id}/panel")
async def api_run_panel(self, request):
    room_id = request.path_params["room_id"]
    data = await self.safe_json(request)
    question = (data.get("question") or "").strip()
    if not question:
        return {"error": "question required"}
    return await self.run_panel(
        room_id,
        question,
        rounds=data.get("rounds", 2),
        synthesize=data.get("synthesize", True),
    )
