"""people — delegation: hero-card roster spanning Me + human contacts + AI
models/agents, and a per-task routing helper ("who or what should do this").

Extracted as its own module rather than folded into ai_surfaces.py because it
aggregates across three different "who can work" sources the rest of this app
doesn't otherwise touch: the user's own profile (``_me.md``, via simulate.py's
``api_values``), the human roster (this app's own data), and AI think-providers
+ named agents contributed by other apps (voice-assistant companions, staff
agents). ai_surfaces.py stays purely person-scoped LLM surfaces.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: self.list_people / self._people_notes (spine);
self.api_values (simulate.py) for the Me card; self.select / self.think
(BaseApp) for the routing decision.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from emptyos.capabilities.cost import estimate_cost
from emptyos.sdk import web_route

from .shared import CLARIFY_QUESTION_SYSTEM, CLARITY_SYSTEM, DELEGATE_SYSTEM

if TYPE_CHECKING:
    from .app import PeopleApp  # noqa: F401 — for type hints only


# ─── Bind to PeopleApp class as ────────────────────────────────
#   _me_card               = _delegation._me_card
#   _ai_provider_roster    = _delegation._ai_provider_roster
#   _ai_agent_roster       = _delegation._ai_agent_roster
#   _delegation_roster     = _delegation._delegation_roster
#   _assess_clarity        = _delegation._assess_clarity
#   _delegate              = _delegation._delegate
#   delegate_from_text     = _delegation.delegate_from_text
#   voice_delegate         = _delegation.voice_delegate
#   api_delegation_roster  = _delegation.api_delegation_roster
#   api_delegate           = _delegation.api_delegate
# Adding a new method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

_MAX_HUMANS = 20
_MAX_AI = 12


async def _me_card(self) -> dict:
    """The user's own delegation card, sourced from ``_me.md`` (simulate.py) —
    no separate "self person note" invented; this app already has a
    dedicated home for who the user is."""
    settings = self.require("settings")
    name = (settings.get("user.name", "") if settings else "") or "You"
    try:
        profile = await self.api_values(request=None)
    except Exception:
        profile = {"values": [], "goals": [], "life_stage": ""}
    values = (profile.get("values") or [])[:3]
    goals = (profile.get("goals") or [])[:3]
    life_stage = profile.get("life_stage") or ""
    return {
        "id": "me",
        "kind": "self",
        "name": name,
        "life_stage": life_stage,
        "values": values,
        "goals": goals,
        "exists": bool(values or goals or life_stage),
    }


async def _ai_provider_roster(self) -> list[dict]:
    """AI think providers as delegation candidates."""
    try:
        rows = (await self.kernel.capabilities.status()).get("think") or []
    except Exception:
        return []
    out = []
    for r in rows:
        name = r.get("name") or ""
        if not name:
            continue
        cost_per_1k = estimate_cost("think", provider=name, tokens=1000)
        out.append(
            {
                "id": f"model:{name}",
                "kind": "ai_model",
                "name": name,
                "available": bool(r.get("available")),
                "is_cloud": bool(r.get("is_cloud")),
                "domain": r.get("domain") or "",
                "model": r.get("model") or "",
                "cost_per_1k": round(cost_per_1k, 4),
            }
        )
    return out[:_MAX_AI]


async def _ai_agent_roster(self) -> list[dict]:
    """Named AI agents/companions from other apps — soft, per-app fail-quiet
    (neither voice-assistant nor staff is a hard dependency)."""
    out: list[dict] = []
    try:
        companions = await self.call_app("voice-assistant", "api_companions", request=None)
        for c in companions or []:
            cid = c.get("id")
            if not cid:
                continue
            out.append(
                {
                    "id": f"companion:{cid}",
                    "kind": "ai_agent",
                    "subkind": "companion",
                    "name": c.get("name") or cid,
                }
            )
    except Exception:
        pass
    try:
        staff = await self.call_app("staff", "api_list", request=None)
        for a in staff or []:
            aid = a.get("id")
            if not aid:
                continue
            out.append(
                {
                    "id": f"staff:{aid}",
                    "kind": "ai_agent",
                    "subkind": "staff",
                    "name": a.get("name") or aid,
                    "emoji": a.get("emoji") or "",
                    "role": a.get("role") or "",
                    "enabled": bool(a.get("enabled")),
                    "observe_apps": a.get("observe_apps") or [],
                    "action_apps": a.get("action_apps") or [],
                }
            )
    except Exception:
        pass
    return out[:_MAX_AI]


async def _delegation_roster(self) -> dict:
    me = await self._me_card()
    humans = await self.list_people(active_only=True)
    ai_models = await self._ai_provider_roster()
    ai_agents = await self._ai_agent_roster()
    return {"me": me, "humans": humans, "ai_models": ai_models, "ai_agents": ai_agents}


def _candidate_desc(cand: dict) -> str:
    kind = cand.get("kind")
    if kind == "self":
        bits = []
        if cand.get("life_stage"):
            bits.append(f"life stage: {cand['life_stage']}")
        if cand.get("goals"):
            bits.append("focus: " + ", ".join(cand["goals"]))
        if cand.get("values"):
            bits.append("values: " + ", ".join(cand["values"]))
        return "You" + (" — " + "; ".join(bits) if bits else "")
    if kind == "human":
        skills = ", ".join(cand.get("skills") or []) or "unspecified"
        role = cand.get("role") or cand.get("relationship") or ""
        tag = f", {role}" if role else ""
        return f"{cand.get('name', '?')} (human{tag}) — skills: {skills}; load: {cand.get('band', 'ok')}"
    if kind == "ai_model":
        if not cand.get("is_cloud"):
            cost = "local, free, on-device"
        elif not cand.get("cost_per_1k"):
            cost = "cloud, free"
        else:
            cost = f"cloud, ~${cand['cost_per_1k']}/1K tokens"
        dom = cand.get("domain") or "general"
        return f"{cand.get('name', '?')} (AI model, {dom} domain) — {cost}"
    if kind == "ai_agent":
        sub = "voice companion" if cand.get("subkind") == "companion" else "scheduled staff agent"
        role = cand.get("role") or ""
        tag = f" — {role}" if role else ""
        return f"{cand.get('name', '?')} (AI {sub}){tag}"
    return cand.get("name", "?")


def _delegate_candidates(roster: dict) -> dict[str, dict]:
    registry: dict[str, dict] = {}
    me = roster.get("me")
    if me:
        registry["me"] = me
    for p in (roster.get("humans") or [])[:_MAX_HUMANS]:
        registry[p["id"]] = {"kind": "human", **p}
    for m in roster.get("ai_models") or []:
        if not m.get("available"):
            continue
        registry[m["id"]] = m
    for a in roster.get("ai_agents") or []:
        registry[a["id"]] = a
    return registry


async def _assess_clarity(self, task_with_context: str) -> dict:
    """Problem-awareness gate: is the task specific enough to route confidently?

    A closed yes/no decision, so it goes through ``select()`` (never
    invents a verdict). Only pays for a second call — generating the actual
    question — on the "unclear" branch, which should be the minority case."""
    choices = {
        "clear": "The task names a concrete action and target; what 'done' looks like is evident.",
        "unclear": "Key information is missing or ambiguous in a way that would change who should do it.",
    }
    verdict = await self.select(
        f"Task: {task_with_context}", choices, system=CLARITY_SYSTEM, default="clear"
    )
    if verdict != "unclear":
        return {"clear": True}
    question = ""
    try:
        question = await self.think(
            f"Task: {task_with_context}",
            system=CLARIFY_QUESTION_SYSTEM,
            domain="text",
            temperature=0.3,
        )
    except Exception:
        pass
    question = (question or "").strip()
    if not question:
        # Couldn't generate a question — fail open rather than get stuck.
        return {"clear": True}
    return {"clear": False, "question": question}


async def _delegate(
    self, task: str, hours=None, skills: str = "", skip_clarify: bool = False
) -> dict:
    task = (task or "").strip()
    if not task:
        return {"error": "task required"}
    extra = ""
    if hours:
        extra += f"\nEstimated time: {hours}h"
    if skills:
        extra += f"\nSkills the task needs: {skills}"
    if not skip_clarify:
        clarity = await self._assess_clarity(task + extra)
        if not clarity.get("clear", True):
            return {"task": task, "needs_clarification": True, "question": clarity["question"]}
    roster = await self._delegation_roster()
    registry = _delegate_candidates(roster)
    if not registry:
        return {"error": "No candidates available — add a person or connect an AI provider."}
    choices = {cid: _candidate_desc(c) for cid, c in registry.items()}
    prompt = (
        f"Task: {task}{extra}\n\nCandidates:\n"
        + "\n".join(f"- {cid}: {desc}" for cid, desc in choices.items())
    )
    default = "me" if "me" in choices else next(iter(choices))
    pick_id = await self.select(prompt, choices, system=DELEGATE_SYSTEM, default=default)
    picked = registry.get(pick_id) or {}
    picked_name = picked.get("name", pick_id)
    reasoning = ""
    try:
        reasoning = await self.think(
            f"Task: {task}{extra}\nPicked: {picked_name} ({picked.get('kind', '?')}: "
            f"{choices.get(pick_id, '')}).\n"
            "In one sentence, explain why this is the right choice for this task.",
            system=DELEGATE_SYSTEM,
            domain="text",
            temperature=0.3,
        )
    except Exception:
        pass
    return {
        "task": task,
        "pick": {"id": pick_id, "kind": picked.get("kind", ""), "name": picked_name},
        "reasoning": (reasoning or "").strip(),
        "considered": [
            {"id": cid, "kind": c.get("kind", ""), "name": c.get("name", cid)}
            for cid, c in registry.items()
        ],
    }


async def delegate_from_text(self, task: str = "", skip_clarify: bool = False) -> dict:
    """Kwargs-only entry for cross-app / assistant-slash callers."""
    return await self._delegate(task, skip_clarify=skip_clarify)


async def voice_delegate(self, task: str = "") -> dict:
    task = (task or "").strip()
    if not task:
        return {"say": "What's the task?"}
    result = await self._delegate(task)
    if result.get("error"):
        return {"say": result["error"]}
    if result.get("needs_clarification"):
        return {"say": result.get("question") or "Can you say more about what this task involves?"}
    pick = result.get("pick", {})
    reasoning = result.get("reasoning", "")
    say = f"I'd go with {pick.get('name', '?')}." + (f" {reasoning}" if reasoning else "")
    return {
        "say": say,
        "card": {
            "renderer": "entity-card",
            "title": "Delegation",
            "data": {
                "title": pick.get("name", ""),
                "subtitle": pick.get("kind", ""),
                "fields": [{"label": "Task", "value": task}]
                + ([{"label": "Why", "value": reasoning}] if reasoning else []),
            },
        },
    }


@web_route("GET", "/api/delegation/roster")
async def api_delegation_roster(self, request):
    return await self._delegation_roster()


@web_route("POST", "/api/delegation/suggest")
async def api_delegate(self, request):
    data = await request.json()
    return await self._delegate(
        data.get("task", ""),
        hours=data.get("hours"),
        skills=data.get("skills", ""),
        skip_clarify=bool(data.get("skip_clarify")),
    )
